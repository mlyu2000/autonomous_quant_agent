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
from .grammar import ConditionParseError


PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Templates emit conditions in the shared grammar (synthesis_layer/grammar.py).
# Each entry condition must parse; validate_spec rejects anything else
# (fail-closed). Indicator references in conditions must be declared in the
# template's `indicators` list with matching lookback.
MECHANISM_TEMPLATES: Dict[str, List[Dict[str, Any]]] = {
    "reversion": [
        {
            "base_name": "rsi_mean_reversion",
            "indicators": [{"name": "rsi", "lookback": 14, "shift": 1, "params": {}}],
            "entry_rules": [
                {"direction": "long", "condition": "rsi(14) < 30"},
                {"direction": "short", "condition": "rsi(14) > 70"},
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
                {"direction": "long", "condition": "close < bbands.bot"},
                {"direction": "short", "condition": "close > bbands.top"},
            ],
            "exit_rules": [
                {"exit_type": "tp", "params": {"mode": "percent", "value": 0.01}},
                {"exit_type": "stop", "params": {"mode": "percent", "value": 0.007}},
            ],
        },
        {
            "base_name": "rsi_bb_reversion",
            "indicators": [
                {"name": "rsi", "lookback": 14, "shift": 1, "params": {}},
                {"name": "bbands", "lookback": 20, "shift": 1, "params": {"dev": 2.0}},
            ],
            "entry_rules": [
                {"direction": "long", "condition": "rsi(14) < 35 and close < bbands.bot"},
                {"direction": "short", "condition": "rsi(14) > 65 and close > bbands.top"},
            ],
            "exit_rules": [
                {"exit_type": "tp", "params": {"mode": "percent", "value": 0.012}},
                {"exit_type": "stop", "params": {"mode": "percent", "value": 0.008}},
            ],
        },
    ],
    "vol_snapback": [
        {
            "base_name": "rsi_vol_snapback",
            "indicators": [
                {"name": "rsi", "lookback": 14, "shift": 1, "params": {}},
                {"name": "volume_ratio", "lookback": 20, "shift": 1, "params": {}},
            ],
            "entry_rules": [
                {"direction": "long", "condition": "rsi(14) < 35 and volume_ratio(20) > 2.0"},
                {"direction": "short", "condition": "rsi(14) > 65 and volume_ratio(20) > 2.0"},
            ],
            "exit_rules": [
                {"exit_type": "tp", "params": {"mode": "percent", "value": 0.01}},
                {"exit_type": "stop", "params": {"mode": "percent", "value": 0.006}},
            ],
        },
        {
            "base_name": "stoch_reversion",
            "indicators": [{"name": "stoch_k", "lookback": 14, "shift": 1, "params": {}}],
            "entry_rules": [
                {"direction": "long", "condition": "stoch_k(14) < 20"},
                {"direction": "short", "condition": "stoch_k(14) > 80"},
            ],
            "exit_rules": [
                {"exit_type": "tp", "params": {"mode": "percent", "value": 0.01}},
                {"exit_type": "stop", "params": {"mode": "percent", "value": 0.007}},
            ],
        },
    ],
    "trend_momentum": [
        {
            "base_name": "trend_momentum_sma_macd",
            "indicators": [
                {"name": "sma", "lookback": 50, "shift": 1, "params": {}},
                {"name": "macd_hist", "lookback": 26, "shift": 1, "params": {}},
            ],
            "entry_rules": [
                {"direction": "long", "condition": "close > sma(50) and macd_hist > 0"},
                {"direction": "short", "condition": "close < sma(50) and macd_hist < 0"},
            ],
            "exit_rules": [
                {"exit_type": "tp", "params": {"mode": "percent", "value": 0.015}},
                {"exit_type": "stop", "params": {"mode": "percent", "value": 0.008}},
            ],
        },
        {
            "base_name": "ema_trend",
            "indicators": [{"name": "ema", "lookback": 20, "shift": 1, "params": {}}],
            "entry_rules": [
                {"direction": "long", "condition": "close > ema(20)"},
                {"direction": "short", "condition": "close < ema(20)"},
            ],
            "exit_rules": [
                {"exit_type": "tp", "params": {"mode": "percent", "value": 0.012}},
                {"exit_type": "stop", "params": {"mode": "percent", "value": 0.006}},
            ],
        },
        {
            "base_name": "donchian_breakout",
            "indicators": [{"name": "donchian", "lookback": 20, "shift": 1, "params": {}}],
            "entry_rules": [
                {"direction": "long", "condition": "close > donchian.mid"},
                {"direction": "short", "condition": "close < donchian.mid"},
            ],
            "exit_rules": [
                {"exit_type": "tp", "params": {"mode": "percent", "value": 0.015}},
                {"exit_type": "stop", "params": {"mode": "percent", "value": 0.009}},
            ],
        },
        {
            "base_name": "adx_trend",
            "indicators": [
                {"name": "sma", "lookback": 50, "shift": 1, "params": {}},
                {"name": "adx", "lookback": 14, "shift": 1, "params": {}},
            ],
            "entry_rules": [
                {"direction": "long", "condition": "close > sma(50) and adx(14) > 25"},
            ],
            "exit_rules": [
                {"exit_type": "tp", "params": {"mode": "percent", "value": 0.02}},
                {"exit_type": "stop", "params": {"mode": "percent", "value": 0.01}},
            ],
        },
    ],
    "time_decay": [
        {
            "base_name": "time_decay_exit",
            "indicators": [{"name": "sma", "lookback": 20, "shift": 1, "params": {}}],
            "entry_rules": [
                {"direction": "long", "condition": "close > sma(20)"},
                {"direction": "short", "condition": "close < sma(20)"},
            ],
            "exit_rules": [
                {"exit_type": "time", "params": {"bars": 24}},
                {"exit_type": "tp", "params": {"mode": "percent", "value": 0.01}},
            ],
        },
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
    seen_keys = set()
    rng = random.Random(20260725)

    class_counts: Dict[str, int] = {name: 0 for name in MECHANISM_CLASSES}
    generated = 0
    attempts = 0
    max_attempts = count * 60  # dedupe can reject many candidates

    while generated < count and attempts < max_attempts:
        attempts += 1
        mechanism_class = rng.choice(sorted(MECHANISM_CLASSES))
        mutate = generated > 8  # mutate after the base population exists
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

    if generated < count:
        import logging
        logging.getLogger(__name__).warning(
            "generate_specs: requested %d, produced %d (distinct-thesis pool exhausted "
            "after %d attempts)", count, generated, attempts,
        )
    return specs
