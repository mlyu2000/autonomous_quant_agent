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


def test_every_grammar_indicator_runs():
    """Regression guard: every whitelisted indicator must not only COMPILE but
    actually INSTANTIATE and RUN without crashing. This is what catches the
    donchian/VWAP-class bug where a valid spec blows up mid-run because the
    compiler called a non-existent indicator. compile_spec() only builds the
    class; the indicators are constructed inside the strategy __init__ (run
    time), so a compile-only check is not sufficient."""
    import backtrader as bt
    import pandas as pd

    data = Path(PROJECT_ROOT / "backtest_engine/data_lake/XAUUSD_H4.parquet")
    df = pd.read_parquet(data)
    # Keep the run fast: a short slice, but long enough for period-20
    # indicators to warm up.
    df = df.iloc[:200].copy()

    from engine.runner import run_backtest

    for name in INDICATOR_NAMES:
        spec = _spec_for(name)
        try:
            run_backtest(
                strategy_class=compile_spec(spec),
                data_source=df,
                start_date="2006-01-01",
                end_date="2006-06-30",
                initial_capital=10_000.0,
                commission=0.035,
                warmup_bars=50,
            )
        except Exception as exc:  # noqa: BLE001 - must surface the crash
            raise AssertionError(
                f"indicator '{name}' failed to RUN (a valid spec crashed "
                f"mid-backtest): {type(exc).__name__}: {exc}"
            ) from exc
