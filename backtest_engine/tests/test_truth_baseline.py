"""
Truth baseline tests for the local Backtrader MVP path.

Validates runner/metrics invariants using deterministic strategy fixtures.
Establishes honest conventions for open-position-at-end cases.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import backtrader as bt
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = PROJECT_ROOT / "backtest_engine/data_lake/XAUUSD_H4.parquet"
START_DATE = "2006-01-01"
END_DATE = "2006-12-31"

sys.path.insert(0, str(PROJECT_ROOT / "backtest_engine"))
from engine.runner import run_backtest  # noqa: E402


@pytest.fixture(scope="module")
def parquet_path() -> str:
    return str(DATA_PATH)


class AlwaysLong(bt.Strategy):
    def next(self):
        if not self.position:
            self.buy(size=1, tradeid="always_long")


class NeverTrades(bt.Strategy):
    def next(self):
        return


class CloseOnFirstBar(bt.Strategy):
    def next(self):
        if len(self.data) == 1:
            self.buy(size=1, tradeid="open_first_bar")


class Overtrade(bt.Strategy):
    def next(self):
        if not self.position:
            self.buy(size=1, tradeid="overtrade")
            self.sell(size=1, tradeid="overtrade")


def test_never_trades_has_zero_commission_and_final_value_equals_initial(parquet_path: str):
    metrics = run_backtest(
        strategy_class=NeverTrades,
        data_class=bt.feeds.PandasData,
        data_source=parquet_path,
        start_date=START_DATE,
        end_date=END_DATE,
        initial_capital=10_000.0,
        commission=0.0,
        position_sizing="fixed",
        stake=1,
        warmup_bars=0,
    )
    assert metrics["total_trades"] == 0
    assert metrics["total_commissions"] == 0.0
    assert abs(metrics["final_value"] - metrics["initial_capital"]) < 1e-9


def test_never_trades_reports_no_open_position(parquet_path: str):
    metrics = run_backtest(
        strategy_class=NeverTrades,
        data_class=bt.feeds.PandasData,
        data_source=parquet_path,
        start_date=START_DATE,
        end_date=END_DATE,
        initial_capital=10_000.0,
        commission=0.0,
        position_sizing="fixed",
        stake=1,
        warmup_bars=0,
    )
    assert metrics["has_open_position"] is False
    assert metrics["error_flag"] is None


def test_overtrade_produces_finite_count_and_stable_final_value(parquet_path: str):
    metrics = run_backtest(
        strategy_class=Overtrade,
        data_class=bt.feeds.PandasData,
        data_source=parquet_path,
        start_date=START_DATE,
        end_date=END_DATE,
        initial_capital=10_000.0,
        commission=0.0,
        position_sizing="fixed",
        stake=1,
        warmup_bars=0,
    )
    assert metrics["total_trades"] > 0
    assert math.isfinite(metrics["final_value"])
    assert metrics["final_cash"] >= 0.0


def test_open_position_at_end_does_not_crash(parquet_path: str):
    metrics = run_backtest(
        strategy_class=AlwaysLong,
        data_class=bt.feeds.PandasData,
        data_source=parquet_path,
        start_date=START_DATE,
        end_date=END_DATE,
        initial_capital=10_000.0,
        commission=0.0,
        position_sizing="fixed",
        stake=1,
        warmup_bars=50,
    )
    assert "error_flag" in metrics
    assert math.isfinite(metrics["final_value"])
    assert metrics["final_value"] >= 0.0
    assert metrics["error_flag"] in ("open_position_at_end", "never_closed_any_trade")
    assert metrics["has_open_position"] is True


def test_broker_value_components_are_consistent(parquet_path: str):
    metrics = run_backtest(
        strategy_class=CloseOnFirstBar,
        data_class=bt.feeds.PandasData,
        data_source=parquet_path,
        start_date=START_DATE,
        end_date=END_DATE,
        initial_capital=10_000.0,
        commission=0.0,
        position_sizing="fixed",
        stake=1,
        warmup_bars=0,
    )
    assert math.isfinite(metrics["final_value"])
    assert math.isfinite(metrics["final_cash"])
    assert metrics["final_value"] >= 0.0
    assert metrics["final_cash"] >= 0.0


def test_trade_log_count_agrees_with_completed_trades(parquet_path: str):
    metrics = run_backtest(
        strategy_class=CloseOnFirstBar,
        data_class=bt.feeds.PandasData,
        data_source=parquet_path,
        start_date=START_DATE,
        end_date=END_DATE,
        initial_capital=10_000.0,
        commission=0.0,
        position_sizing="fixed",
        stake=1,
        warmup_bars=0,
    )
    trades = metrics.get("trades", [])
    assert all(isinstance(t.get("pnl_net"), (int, float)) for t in trades)
    assert metrics["total_trades"] == len(trades)
    if metrics["total_trades"] > 0:
        assert metrics["winning_trades"] + metrics["losing_trades"] == metrics["total_trades"]
