"""
Strategy spec schema and validation for the local Backtrader MVP path.

Specs are stored as JSON and validated before compilation/runtime.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


from .grammar import validate_spec_conditions, INDICATOR_NAMES

MECHANISM_CLASSES = {"reversion", "vol_snapback", "trend_momentum", "time_decay"}
ALLOWED_EXIT_TYPES = {"stop", "tp", "time", "reverse"}
ALLOWED_SIZING_MODES = {"fixed_lot"}
# Single source of truth: the grammar's indicator whitelist. Anything here must
# be compilable by the engine (enforced by tests/test_indicator_compilability.py).
# Do NOT add names here that the compiler cannot build — that is fail-open.
ALLOWED_INDICATOR_LOOKUP = set(INDICATOR_NAMES)


class SpecValidationError(Exception):
    pass


@dataclass(frozen=True)
class IndicatorSpec:
    name: str
    params: Dict[str, Any]
    lookback: int
    shift: int = 1

    def validate(self) -> None:
        if self.name not in ALLOWED_INDICATOR_LOOKUP:
            raise SpecValidationError(f"indicator '{self.name}' is not allowed")
        if self.lookback < 1 or self.shift < 1:
            raise SpecValidationError("lookback and shift must be >= 1")
        if self.shift < 1:
            raise SpecValidationError("indicators must be shifted by at least 1 bar")


@dataclass(frozen=True)
class ExitRuleSpec:
    exit_type: str
    params: Dict[str, Any]

    def validate(self) -> None:
        if self.exit_type not in ALLOWED_EXIT_TYPES:
            raise SpecValidationError(f"exit_type '{self.exit_type}' is not allowed")


@dataclass(frozen=True)
class RiskSpec:
    max_positions: int = 1
    cooldown_bars: int = 0
    trailing_stop: bool = False
    trailing_params: Optional[Dict[str, Any]] = None

    def validate(self) -> None:
        if self.max_positions != 1:
            raise SpecValidationError("MVP max_positions must be 1")
        if self.cooldown_bars < 0:
            raise SpecValidationError("cooldown_bars must be >= 0")
        if self.trailing_stop and not isinstance(self.trailing_params, dict):
            raise SpecValidationError("trailing_stop requires trailing_params when enabled")


@dataclass(frozen=True)
class SizingSpec:
    mode: str = "fixed_lot"
    lots: float = 0.01

    def validate(self) -> None:
        if self.mode not in ALLOWED_SIZING_MODES:
            raise SpecValidationError(f"sizing mode '{self.mode}' is not allowed in MVP")
        if not math.isfinite(self.lots) or self.lots <= 0:
            raise SpecValidationError("sizing.lots must be positive finite")


@dataclass(frozen=True)
class StrategySpec:
    spec_id: str
    thesis_id: str
    mechanism_class: str
    timeframe: str
    indicators: List[Dict[str, Any]]
    entry_rules: List[Dict[str, Any]]
    exit_rules: List[Dict[str, Any]]
    risk: Dict[str, Any]
    sizing: Dict[str, Any]
    source_file: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None

    def validate(self) -> None:
        if self.mechanism_class not in MECHANISM_CLASSES:
            raise SpecValidationError(f"mechanism_class '{self.mechanism_class}' is invalid")
        if not self.indicators:
            raise SpecValidationError("at least one indicator is required")
        if not self.entry_rules:
            raise SpecValidationError("at least one entry rule is required")
        if not self.exit_rules:
            raise SpecValidationError("at least one exit rule is required")
        for raw_indicator in self.indicators:
            indicator = IndicatorSpec(
                name=raw_indicator["name"],
                params=raw_indicator.get("params", {}),
                lookback=int(raw_indicator.get("lookback", 20)),
                shift=int(raw_indicator.get("shift", 1)),
            )
            indicator.validate()
            if indicator.name in {"sma", "ema", "wma"} and indicator.lookback < 2:
                raise SpecValidationError(f"indicator '{indicator.name}' lookback too small")
        for raw_exit in self.exit_rules:
            ExitRuleSpec(exit_type=raw_exit["exit_type"], params=raw_exit.get("params", {})).validate()
        RiskSpec(**self.risk).validate()
        SizingSpec(**self.sizing).validate()
        if not any(rule["exit_type"] in {"stop", "tp"} for rule in self.exit_rules):
            raise SpecValidationError("exit_rules must include at least one stop or tp")
        # Fail-closed grammar check: every entry condition must parse.
        raw_dict = {
            "entry_rules": [
                {"condition": rule.get("condition", ""), "direction": rule.get("direction", "long")}
                for rule in self.entry_rules
            ]
        }
        try:
            validate_spec_conditions(raw_dict)
        except Exception as exc:
            raise SpecValidationError(f"entry condition grammar violation: {exc}") from exc

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "thesis_id": self.thesis_id,
            "mechanism_class": self.mechanism_class,
            "timeframe": self.timeframe,
            "indicators": self.indicators,
            "entry_rules": self.entry_rules,
            "exit_rules": self.exit_rules,
            "risk": self.risk,
            "sizing": self.sizing,
            "source_file": self.source_file,
            "extra": self.extra,
        }


def _lookback_band(lookback: Any) -> int:
    """Band lookbacks so parameter-neighbor variants share a thesis key."""
    try:
        lb = int(lookback)
    except (TypeError, ValueError):
        return 0
    if lb < 10:
        return 5
    if lb < 20:
        return 10
    if lb < 40:
        return 20
    if lb < 100:
        return 40
    return 100


def _fingerprint_indicators(indicators: List[Dict[str, Any]]) -> Tuple[str, ...]:
    parts: List[str] = []
    for indicator in indicators:
        name = str(indicator.get("name", ""))
        lookback = indicator.get("lookback", 20)
        parts.append(f"{name}~{_lookback_band(lookback)}")
    return tuple(parts)


def _entry_condition_fingerprint(entry_rules: List[Dict[str, Any]]) -> Tuple[str, ...]:
    parts: List[str] = []
    for rule in entry_rules:
        cond = str(rule.get("condition", "")).strip().lower()
        if cond:
            parts.append(cond)
    return tuple(sorted(parts))


def dedupe_key(spec: StrategySpec) -> Tuple[str, Tuple[str, ...], str]:
    """Thesis key = mechanism class + indicator CORE (names, banded) + direction.

    Per the project definition: dedupe by mechanism class + indicator core +
    direction. Parameter neighbors (lookback within a band, TP/SL values) and
    threshold variants of the same indicator core are the SAME thesis and are
    collapsed. A different indicator core or direction is a distinct thesis.
    """
    # Indicator core: (name, lookback-band) per indicator, order-insensitive.
    # The band collapses lookback noise (14 vs 15) but keeps genuinely
    # different horizons distinct (rsi(14) core vs rsi(50) core).
    core = tuple(sorted(_fingerprint_indicators(spec.indicators)))
    return (
        spec.mechanism_class,
        core,
        _entry_direction(spec.entry_rules),
    )


def _entry_direction(entry_rules: List[Dict[str, Any]]) -> str:
    values = [str(rule.get("direction", "")).strip().lower() for rule in entry_rules]
    if all(v == "long" for v in values if v):
        return "long"
    if all(v == "short" for v in values if v):
        return "short"
    if any(v == "long" for v in values if v) and any(v == "short" for v in values if v):
        return "mixed"
    return "unknown"


def normalize_spec(raw: Dict[str, Any]) -> StrategySpec:
    return StrategySpec(
        spec_id=str(raw.get("spec_id") or _make_spec_id(raw)),
        thesis_id=str(raw.get("thesis_id") or raw.get("spec_id") or _make_spec_id(raw)),
        mechanism_class=str(raw["mechanism_class"]),
        timeframe=str(raw["timeframe"]).upper(),
        indicators=list(raw.get("indicators", [])),
        entry_rules=list(raw.get("entry_rules", [])),
        exit_rules=list(raw.get("exit_rules", [])),
        risk=dict(raw.get("risk", {"max_positions": 1, "cooldown_bars": 0, "trailing_stop": False})),
        sizing=dict(raw.get("sizing", {"mode": "fixed_lot", "lots": 0.01})),
        source_file=raw.get("source_file"),
        extra=raw.get("extra"),
    )


def validate_spec(raw: Dict[str, Any]) -> StrategySpec:
    spec = normalize_spec(raw)
    spec.validate()
    return spec


def is_duplicate(a: StrategySpec, b: StrategySpec) -> bool:
    return dedupe_key(a) == dedupe_key(b)


def write_spec(spec: StrategySpec, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"spec_{spec.spec_id}.json"
    path.write_text(json.dumps(spec.to_dict(), indent=2, default=str))
    return path


def read_spec(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text())


def _make_spec_id(raw: Dict[str, Any]) -> str:
    payload = json.dumps(raw, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:10]
