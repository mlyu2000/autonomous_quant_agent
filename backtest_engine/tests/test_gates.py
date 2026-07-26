"""
Tests for validation gates: PnL invariant, sample size, outlier resistance.
"""
from __future__ import annotations

from pathlib import Path
from sys import path as _sys_path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_sys_path.insert(0, str(PROJECT_ROOT / "backtest_engine"))

from audit.gates import evaluate, gate_pnl_invariant, gate_sample_size, gate_outlier_resistance


def _base_metrics(**overrides):
    m = {
        "initial_capital": 10000.0,
        "final_value": 11000.0,
        "net_profit": 1000.0,
        "total_trades": 50,
        "profit_factor": 1.5,
        "total_bars": 32082,
        "data_start_date": "2006-01-02",
        "data_end_date": "2026-01-01",
        "trades": [{"pnl_net": 100.0} for _ in range(10)],
    }
    m.update(overrides)
    return m


def test_sample_size_pass():
    assert gate_sample_size(_base_metrics(total_trades=50))["passed"] is True
    assert gate_sample_size(_base_metrics(total_trades=5))["passed"] is False


def test_pnl_invariant_pass_when_consistent():
    # sum(pnl_net)=1000, expected=11000, final=11000 => diff 0
    assert gate_pnl_invariant(_base_metrics())["passed"] is True


def test_pnl_invariant_fail_on_real_discrepancy():
    # final_value disagrees with trade PnL by $500 => real bug caught
    bad = _base_metrics(final_value=11500.0)
    res = gate_pnl_invariant(bad, tolerance=0.10)
    assert res["passed"] is False


def test_outlier_resistance_blocks_dominated_trade():
    # one trade is 90% of net profit => concentration fail
    trades = [{"pnl_net": 900.0}] + [{"pnl_net": 10.0} for _ in range(9)]
    res = gate_outlier_resistance(_base_metrics(trades=trades, net_profit=1000.0))
    assert res["passed"] is False


def test_evaluate_fail_when_sample_too_small():
    r = evaluate(_base_metrics(total_trades=0, trades=[], profit_factor=None))
    assert r["status"] == "FAIL"
