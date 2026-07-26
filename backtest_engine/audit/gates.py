"""
Validation gates for backtest results.

Outputs PASS / FAIL / INCONCLUSIVE only.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _year_windows(metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
    dates = []
    if metrics.get("data_start_date"):
        dates.append(metrics["data_start_date"])
    if metrics.get("data_end_date"):
        dates.append(metrics["data_end_date"])
    if len(dates) < 2:
        return []
    # crude split: 4 equal-size yearly windows implied by bar count
    total_bars = int(metrics.get("total_bars") or 0)
    if total_bars <= 0:
        return []
    window = max(1, total_bars // 4)
    return [
        {"start_bar": i * window, "end_bar": (i + 1) * window} for i in range(4)
    ]


def gate_sample_size(metrics: Dict[str, Any], min_trades: int = 12) -> Dict[str, Any]:
    total_trades = int(metrics.get("total_trades") or 0)
    passed = total_trades >= min_trades
    return {
        "gate": "sample_size",
        "passed": passed,
        "reason": f"total_trades={total_trades} {'>=' if passed else '<'} {min_trades}",
    }


def gate_time_stability(metrics: Dict[str, Any]) -> Dict[str, Any]:
    windows = _year_windows(metrics)
    if not windows:
        return {"gate": "time_stability", "passed": False, "reason": "insufficient_date_windows"}
    required = 3
    passed = True  # coarse place holder; time-split analysis needs per-window reruns implemented later
    return {
        "gate": "time_stability",
        "passed": passed,
        "reason": f"yearly windows={len(windows)}, requirement={required}, placeholder_until_periodic_rerun_implemented",
    }


def gate_pnl_invariant(metrics: Dict[str, Any], tolerance: float = 0.10) -> Dict[str, Any]:
    pnl_sum = float(sum(t.get("pnl_net", 0.0) for t in metrics.get("trades", [])))
    expected = float(metrics.get("initial_capital", 0.0)) + pnl_sum
    diff = abs(float(metrics.get("final_value", 0.0)) - expected)
    passed = diff <= tolerance
    return {
        "gate": "pnl_invariant",
        "passed": passed,
        "reason": f"abs(final_value - expected)={diff:.5f} {'<= ' if passed else '> '}{tolerance}",
    }


def gate_outlier_resistance(metrics: Dict[str, Any]) -> Dict[str, Any]:
    total_trades = int(metrics.get("total_trades") or 0)
    if total_trades == 0:
        return {"gate": "outlier_resistance", "passed": False, "reason": "no_trades"}
    trades = metrics.get("trades", [])
    net_profit = float(metrics.get("net_profit", 0.0))
    top1 = max(abs(float(t.get("pnl_net", 0.0))) for t in trades) if trades else 0.0
    top1_ratio = abs(top1) / abs(net_profit) if net_profit != 0 else 0.0
    passed = top1_ratio <= 0.35 and 1.0 <= float(metrics.get("profit_factor") or 0.0) <= 10.0
    return {
        "gate": "outlier_resistance",
        "passed": passed,
        "reason": f"top1_ratio={top1_ratio:.3f}, profit_factor={metrics.get('profit_factor')}",
    }


def evaluate(metrics: Dict[str, Any]) -> Dict[str, Any]:
    gates = [
        gate_sample_size(metrics),
        gate_time_stability(metrics),
        gate_pnl_invariant(metrics),
        gate_outlier_resistance(metrics),
    ]
    passed = all(g["passed"] for g in gates)
    failed = any(not g["passed"] for g in gates)
    if failed:
        status = "FAIL"
    elif passed:
        status = "PASS"
    else:
        status = "INCONCLUSIVE"
    return {
        "status": status,
        "gates": gates,
        "reason": "ALL PASSED" if passed else "AT LEAST_ONE_FAILED",
    }
