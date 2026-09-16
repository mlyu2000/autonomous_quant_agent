"""
Fail-open guard: EVERY indicator name in the shared grammar whitelist must
validate AND compile to a runnable Backtrader strategy.

This is the regression guard for the "grammar whitelist vs compiler" drift
bug: previously `vwap` was in the grammar (specs validated) but the compiler
called a non-existent `bt.indicators.VolumeWeightedAveragePrice`, so a
valid spec crashed the whole backtest run. Any new grammar indicator that
the compiler cannot build is a money-risk failure (a spec can pass
validation and then blow up mid-run).
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backtest_engine"))

from compiler.dynamic_loader import compile_spec  # noqa: E402
from synthesis_layer.grammar import INDICATOR_NAMES  # noqa: E402
from synthesis_layer.strategy_schema import (  # noqa: E402
    ALLOWED_INDICATOR_LOOKUP,
    validate_spec,
)


def _spec_for(name: str) -> dict:
    return {
        "spec_id": f"compile_check_{name}",
        "thesis_id": "compile_check",
        "mechanism_class": "trend_momentum",
        "timeframe": "H4",
        "indicators": [{"name": name, "lookback": 20, "shift": 1, "params": {}}],
        "entry_rules": [{"direction": "long", "condition": f"{name}(20) > 0"}],
        "exit_rules": [
            {"exit_type": "tp", "params": {"mode": "percent", "value": 0.015}},
            {"exit_type": "stop", "params": {"mode": "percent", "value": 0.008}},
        ],
        "risk": {"max_positions": 1, "cooldown_bars": 0, "trailing_stop": False},
        "sizing": {"mode": "fixed_lot", "lots": 0.01},
    }


def test_grammar_and_schema_indicator_whitelists_match():
    """The schema must allow exactly the grammar's indicators (no drift)."""
    assert ALLOWED_INDICATOR_LOOKUP == set(INDICATOR_NAMES)


def test_every_grammar_indicator_compiles():
    """Every whitelisted indicator must validate AND compile."""
    for name in INDICATOR_NAMES:
        spec = _spec_for(name)
        validate_spec(spec)          # must pass validation
        compile_spec(spec)           # must build a runnable strategy
