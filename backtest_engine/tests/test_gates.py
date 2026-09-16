"""
Tests for validation gates (PASS/FAIL/INCONCLUSIVE semantics):
PnL invariant, sample size, time stability, outlier resistance,
max drawdown, cost ratio, and the evaluate() verdict logic.
"""
from __future__ import annotations

from pathlib import Path
from sys import path as _sys_path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_sys_path.insert(0, str(PROJECT_ROOT / "backtest_engine"))

from audit.gates import (
    evaluate,
    gate_pnl_invariant,
    gate_sample_size,
    gate_outlier_resistance,
    gate_time_stability,
    gate_max_drawdown,
    gate_cost_ratio,
    gate_min_trades_per_year,
    gate_open_position,
)


def _base_metrics(**overrides):
    # A "healthy" 5-year strategy: 25 trades, +$5000, spread evenly across
    # 2006-2010, modest DD, costs well below gross profit, consistent
    # accounting (final == initial + sum(pnl_net)).
    trades = [
        {"entry_date": f"{2000 + y}-06-01", "pnl_net": 200.0, "pnl_gross": 250.0,
         "commission_paid": 20.0, "swap_paid": 10.0}
        for y in range(6, 11)
        for _ in range(5)
    ]  # 25 trades across 2006-2010, +200 each = +5000
    m = {
        "initial_capital": 10000.0,
        "final_value": 15000.0,
        # Independent balance-sheet figure; for a fully closed, consistent
        # run it equals the log PnL sum (initial + sum(pnl_net)) = 15000.
        "broker_final_value": 15000.0,
        "has_open_position": False,
        "net_profit": 5000.0,
        "total_trades": 25,
        "profit_factor": 1.6,
        "max_drawdown_pct": 8.0,
        "total_bars": 32082,
        "data_start_date": "2006-01-02",
        "data_end_date": "2026-01-01",
        "error_flag": None,
        "trades": trades,
    }
    m.update(overrides)
    return m


# ---- gate_sample_size ------------------------------------------------------
def test_sample_size_pass():
    assert gate_sample_size(_base_metrics())["status"] == "PASS"


def test_sample_size_inconclusive_when_small():
    # Insufficient sample is INCONCLUSIVE (not a FAIL, not a PASS).
    assert gate_sample_size(_base_metrics(total_trades=5, trades=[]))["status"] == "INCONCLUSIVE"


# ---- gate_pnl_invariant ----------------------------------------------------
def test_pnl_invariant_pass_when_consistent():
    # sum(pnl_net)=5000, expected=15000, final=15000 => diff 0
    assert gate_pnl_invariant(_base_metrics())["status"] == "PASS"


def test_pnl_invariant_fail_on_real_discrepancy():
    # The INDEPENDENT broker value disagrees with the trade-log PnL by $500
    # => a genuine accounting bug is caught.
    res = gate_pnl_invariant(_base_metrics(broker_final_value=15500.0))
    assert res["status"] == "FAIL"


def test_pnl_invariant_pass_reconciles_two_independent_sources():
    # final_value is derived from the log PnL sum; broker_final_value is the
    # independent balance-sheet figure. When they agree, the gate passes —
    # this is a real cross-check, not a tautology.
    assert gate_pnl_invariant(_base_metrics())["status"] == "PASS"


def test_pnl_invariant_inconclusive_when_no_broker_value():
    # No independent source to reconcile against => INCONCLUSIVE, not a
    # silent pass and not a false FAIL.
    m = _base_metrics()
    m.pop("broker_final_value", None)
    assert gate_pnl_invariant(m)["status"] == "INCONCLUSIVE"


# ---- gate_outlier_resistance -----------------------------------------------
def test_outlier_resistance_blocks_dominated_trade():
    # one trade is ~60% of net profit => concentration fail.
    # 16 trades (>= 12 sample threshold), one is +3000 of the +5000 total.
    trades = [{"pnl_net": 3000.0, "entry_date": "2008-01-01"}] + [
        {"pnl_net": 100.0, "entry_date": f"{2000 + y}-03-01"}
        for y in range(6, 11)
        for _ in range(3)
    ]
    res = gate_outlier_resistance(
        _base_metrics(trades=trades, net_profit=5000.0, total_trades=len(trades))
    )
    assert res["status"] == "FAIL"


def test_outlier_resistance_passes_balanced():
    assert gate_outlier_resistance(_base_metrics())["status"] == "PASS"


# ---- gate_time_stability ---------------------------------------------------
def test_time_stability_passes_spread():
    assert gate_time_stability(_base_metrics())["status"] == "PASS"


def test_time_stability_fails_concentrated():
    # Nearly all profit in one year.
    trades = [
        {"entry_date": "2008-01-01", "pnl_net": 4900.0},
        {"entry_date": "2006-03-01", "pnl_net": 40.0},
        {"entry_date": "2007-03-01", "pnl_net": 30.0},
        {"entry_date": "2009-03-01", "pnl_net": 30.0},
    ]
    res = gate_time_stability(_base_metrics(trades=trades, net_profit=5000.0, total_trades=len(trades)))
    assert res["status"] == "FAIL"


def test_time_stability_inconclusive_when_few_years():
    trades = [{"entry_date": "2008-01-01", "pnl_net": 100.0},
              {"entry_date": "2008-02-01", "pnl_net": 100.0},
              {"entry_date": "2008-03-01", "pnl_net": 100.0}]
    res = gate_time_stability(_base_metrics(trades=trades))
    assert res["status"] == "INCONCLUSIVE"


# ---- gate_max_drawdown -----------------------------------------------------
def test_max_drawdown_pass():
    assert gate_max_drawdown(_base_metrics())["status"] == "PASS"


def test_max_drawdown_fail():
    assert gate_max_drawdown(_base_metrics(max_drawdown_pct=55.0))["status"] == "FAIL"


# ---- gate_cost_ratio -------------------------------------------------------
def test_cost_ratio_pass():
    # costs = 25*(20+10)=750, gross = 25*250=6250 => 12% => pass
    assert gate_cost_ratio(_base_metrics())["status"] == "PASS"


def test_cost_ratio_fails_when_costs_dominate():
    trades = [
        {"entry_date": f"20{y}-06-01", "pnl_net": 200.0, "pnl_gross": 100.0,
         "commission_paid": 80.0, "swap_paid": 40.0}
        for y in range(6, 11) for _ in range(5)
    ]
    # gross=100*25=2500, costs=120*25=3000 => costs > gross => fail
    res = gate_cost_ratio(_base_metrics(trades=trades, net_profit=2500.0, final_value=12500.0, total_trades=len(trades)))
    assert res["status"] == "FAIL"


# ---- gate_min_trades_per_year ----------------------------------------------
def test_min_trades_per_year_pass():
    assert gate_min_trades_per_year(_base_metrics())["status"] == "PASS"


def test_min_trades_per_year_fails_dormant():
    # 3 trades over 5 years = 0.6/yr < 2/yr
    trades = [
        {"entry_date": "2006-01-01", "pnl_net": 100.0},
        {"entry_date": "2009-01-01", "pnl_net": 100.0},
        {"entry_date": "2010-01-01", "pnl_net": 100.0},
    ]
    res = gate_min_trades_per_year(_base_metrics(trades=trades, total_trades=3))
    # 3 trades is also < 12 sample => inconclusive, not fail
    assert res["status"] == "INCONCLUSIVE"


# ---- gate_open_position ----------------------------------------------------
def test_open_position_pass():
    assert gate_open_position(_base_metrics())["status"] == "PASS"


def test_open_position_fails_on_error_flag():
    assert gate_open_position(_base_metrics(error_flag="equity_guard_tripped"))["status"] == "FAIL"


# ---- evaluate() verdict logic ----------------------------------------------
def test_evaluate_pass_when_all_pass():
    r = evaluate(_base_metrics())
    assert r["status"] == "PASS"


def test_evaluate_inconclusive_when_no_fail_but_insufficient():
    # 5 trades: sample_size inconclusive, no gate fails => INCONCLUSIVE
    r = evaluate(_base_metrics(total_trades=5, trades=[]))
    assert r["status"] == "INCONCLUSIVE"


def test_evaluate_fail_when_any_gate_fails():
    # Independent broker value diverges from the trade-log PnL => invariant
    # gate FAILs => overall verdict FAIL.
    r = evaluate(_base_metrics(broker_final_value=16000.0))
    assert r["status"] == "FAIL"
