"""
Strategy generator and mutation operations for the local Backtrader MVP path.

Generates distinct-thesis specs, enforces mechanism-class diversity,
and prevents same-thesis parameter variants.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .strategy_schema import (
    ALLOWED_INDICATOR_LOOKUP,
    MECHANISM_CLASSES,
    StrategySpec,
    validate_spec,
    dedupe_key,
    is_duplicate,
    write_spec,
)


PROJECT_ROOT = Path("/home/ml/projects/autonomous_quant_agent")
SIMULATED_STRATEGIES_DIR = PROJECT_ROOT / "backtest_engine/simulated_strategies"

MECHANISM_TEMPLATES: Dict[str, List[Dict[str, Any]]] = {
    "reversion": [
        {
            "base_name": "rsi_mean_reversion",
            "indicators": [{"name": "rsi", "lookback": 14, "shift": 1, "params": {}}],
            "entry_rules": [
                {"direction": "long", "condition": "rsi < 30"},
                {"direction": "short", "condition": "rsi > 70"},
            ],
            "exit_rules": [
                {"exit_type": "tp", "params": {"mode": "percent", "value": 0.01}},
                {"exit_type": "stop", "params": {"mode": "percent", "value": 0.007}},
            ],
        },
        {
            "base_name": "bb_reversion",
            "indicators": [{"name": "bbands", "lookback": 20, "shift": 1, "params": {"dev": 2.0}}],
            "entry_rules": [
                {"direction": "long", "condition": "close < bbands.lower"},
                {"direction": "short", "condition": "close > bbands.upper"},
            ],
            "exit_rules": [
                {"exit_type": "tp", "params": {"mode": "percent", "value": 0.01}},
                {"exit_type": "stop", "params": {"mode": "percent", "value": 0.007}},
            ],
        },
    ],
    "vol_snapback": [
        {
            "base_name": "atr_volatility",
            "indicators": [
                {"name": "atr", "lookback": 14, "shift": 1, "params": {}},
                {"name": "rsi", "lookback": 14, "shift": 1, "params": {}},
            ],
            "entry_rules": [
                {"direction": "long", "condition": "atr_snaps_low and rsi < 40"},
            ],
            "exit_rules": [
                {"exit_type": "tp", "params": {"mode": "atr", "value": 2.0}},
                {"exit_type": "stop", "params": {"mode": "atr", "value": 1.0}},
            ],
        }
    ],
    "trend_momentum": [
        {
            "base_name": "trend_momentum_sma_filter",
            "indicators": [
                {"name": "sma", "lookback": 50, "shift": 1, "params": {}},
                {"name": "macd_hist", "lookback": 26, "shift": 1, "params": {}},
            ],
            "entry_rules": [
                {"direction": "long", "condition": "close > sma and macd_hist turns positive"},
                {"direction": "short", "condition": "close < sma and macd_hist turns negative"},
            ],
            "exit_rules": [
                {"exit_type": "tp", "params": {"mode": "percent", "value": 0.015}},
                {"exit_type": "stop", "params": {"mode": "percent", "value": 0.008}},
            ],
        },
        {
            "base_name": "ema_ribbon_trend",
            "indicators": [{"name": "ema", "lookback": 20, "shift": 1, "params": {}}],
            "entry_rules": [
                {"direction": "long", "condition": "close > ema"},
            ],
            "exit_rules": [
                {"exit_type": "tp", "params": {"mode": "percent", "value": 0.012}},
                {"exit_type": "stop", "params": {"mode": "percent", "value": 0.006}},
            ],
        },
    ],
    "time_decay": [
        {
            "base_name": "time_decay_exit",
            "indicators": [{"name": "sma", "lookback": 20, "shift": 1, "params": {}}],
            "entry_rules": [
                {"direction": "long", "condition": "close > sma"},
            ],
            "exit_rules": [
                {"exit_type": "time", "params": {"bars": 24}},
                {"exit_type": "tp", "params": {"mode": "percent", "value": 0.01}},
            ],
        }
    ],
}


def _choose_template(mechanism_class: str, index: int) -> Dict[str, Any]:
    templates = MECHANISM_TEMPLATES.get(mechanism_class, [])
    if not templates:
        raise ValueError(f"no templates for mechanism_class '{mechanism_class}'")
    return templates[index % len(templates)]


def mutate_indicator_params(indicators: List[Dict[str, Any]], rng: random.Random) -> List[Dict[str, Any]]:
    mutated: List[Dict[str, Any]] = []
    for indicator in indicators:
        new_indicator = copy.deepcopy(indicator)
        lookback = int(new_indicator.get("lookback", 20))
        if lookback > 10:
            delta = rng.choice([-5, -2, -1, 0, 1, 2, 5])
            new_indicator["lookback"] = max(5, lookback + delta)
        if new_indicator["name"] == "bbands":
            dev = float(new_indicator.get("params", {}).get("dev", 2.0))
            if dev >= 1.5:
                dev += rng.choice([-0.25, 0.0, 0.25])
                new_indicator.setdefault("params", {})["dev"] = max(1.0, dev)
        mutated.append(new_indicator)
    return mutated


def mutate_exit_params(exit_rules: List[Dict[str, Any]], rng: random.Random) -> List[Dict[str, Any]]:
    mutated: List[Dict[str, Any]] = []
    for rule in exit_rules:
        new_rule = copy.deepcopy(rule)
        params = dict(new_rule.get("params", {}))
        if params.get("mode") == "percent":
            base = float(params.get("value", 0.01))
            delta = rng.choice([-0.002, -0.001, 0.0, 0.001, 0.002])
            params["value"] = max(0.001, base + delta)
        if params.get("mode") == "atr":
            base = float(params.get("value", 1.0))
            delta = rng.choice([-0.25, 0.0, 0.25])
            params["value"] = max(0.25, base + delta)
        new_rule["params"] = params
        mutated.append(new_rule)
    return mutated


def _direction_key(mechanism_class: str, index: int) -> str:
    if mechanism_class == "reversion":
        return "long" if index % 2 == 0 else "short"
    if mechanism_class == "trend_momentum":
        return "long"
    if mechanism_class == "time_decay":
        return "long"
    return "long"


def build_spec_raw(
    mechanism_class: str,
    index: int,
    source_file: Optional[str] = None,
    *,
    rng: Optional[random.Random] = None,
    mutate: bool = False,
) -> Dict[str, Any]:
    if rng is None:
        rng = random.Random(42)
    template = _choose_template(mechanism_class, index)
    direction = _direction_key(mechanism_class, index)

    entry_rules: List[Dict[str, Any]] = []
    for rule in template.get("entry_rules", []):
        if rule.get("direction") != direction:
            continue
        entry_rules.append(copy.deepcopy(rule))

    if not entry_rules:
        entry_rules.append(
            {
                "direction": direction,
                "condition": template.get("entry_rules", [{}])[0].get("condition", ""),
            }
        )

    indicators = copy.deepcopy(template.get("indicators", []))
    exit_rules = copy.deepcopy(template.get("exit_rules", []))
    if mutate:
        indicators = mutate_indicator_params(indicators, rng)
        exit_rules = mutate_exit_params(exit_rules, rng)

    raw = {
        "spec_id": uuid.uuid4().hex[:12],
        "thesis_id": template.get("base_name", f"{mechanism_class}_{index}"),
        "mechanism_class": mechanism_class,
        "timeframe": "H4",
        "indicators": indicators,
        "entry_rules": entry_rules,
        "exit_rules": exit_rules,
        "risk": {"max_positions": 1, "cooldown_bars": 0, "trailing_stop": False},
        "sizing": {"mode": "fixed_lot", "lots": 0.01},
        "source_file": source_file,
        "extra": {
            "generated_from": source_file or "builtin_template",
            "mutation_index": index,
            "unique_key": hashlib.sha1(
                json.dumps(
                    {
                        "mechanism_class": mechanism_class,
                        "indicators": indicators,
                        "entry_rules": entry_rules,
                    },
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest()[:10],
        },
    }
    return raw


def generate_specs(count: int, out_dir: Path) -> List[StrategySpec]:
    out_dir.mkdir(parents=True, exist_ok=True)
    specs: List[StrategySpec] = []
    seen_keys: Set[Tuple[str, Tuple[str, ...], str]] = set()
    rng = random.Random(20260725)

    class_counts: Dict[str, int] = {name: 0 for name in MECHANISM_CLASSES}
    generated = 0
    attempts = 0

    while generated < count and attempts < count * 12:
        attempts += 1
        mechanism_class = rng.choice(sorted(MECHANISM_CLASSES))
        mutate = generated > 8
        raw = build_spec_raw(mechanism_class, generated, mutate=mutate, rng=rng)
        try:
            spec = validate_spec(raw)
        except Exception:
            continue
        key = dedupe_key(spec)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        class_counts[spec.mechanism_class] += 1
        write_spec(spec, out_dir)
        specs.append(spec)
        generated += 1

    immigrant_candidates = sorted(SIMULATED_STRATEGIES_DIR.glob("*.py"))
    injected = 0
    while injected < min(4, count // 10 + 1) and immigrant_candidates and generated < count:
        source_file = immigrant_candidates[injected % len(immigrant_candidates)]
        raw = build_spec_raw(
            sorted(MECHANISM_CLASSES)[injected % len(MECHANISM_CLASSES)],
            generated,
            source_file=str(source_file),
            mutate=False,
            rng=rng,
        )
        try:
            spec = validate_spec(raw)
        except Exception:
            injected += 1
            continue
        key = dedupe_key(spec)
        if key in seen_keys:
            injected += 1
            continue
        seen_keys.add(key)
        class_counts[spec.mechanism_class] += 1
        write_spec(spec, out_dir)
        specs.append(spec)
        generated += 1
        injected += 1

    return specs
