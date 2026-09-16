"""
Validation gates for backtest results.

Outputs PASS / FAIL / INCONCLUSIVE only.

Design rules (money-risk discipline):
  - A gate that CANNOT be evaluated (missing data) returns INCONCLUSIVE for
    the run, never a silent pass.
  - PASS requires EVERY gate to pass. Any FAIL gate => FAIL.
  - Cost honesty: strategies must be profitable AFTER commission + swap.
    A "profit" that is mostly cost recovery is a FAIL (cost_ratio gate).
  - Time stability uses the actual trade dates (no placeholders): a strategy
    must not concentrate nearly all profit in a single year, and must be
    net-positive in a majority of the years it traded.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _trades_by_year(metrics: Dict[str, Any]) -> Dict[int, float]:
    """Net PnL per calendar year from the trade log."""
    years: Dict[int, float] = {}
    for t in metrics.get("trades", []):
        d = _parse_date(t.get("entry_date")) or _parse_date(t.get("exit_date"))
        if d is None:
            return {}
        y = d.year
        years[y] = years.get(y, 0.0) + float(t.get("pnl_net", 0.0))
    return years


def gate_sample_size(metrics: Dict[str, Any], min_trades: int = 12) -> Dict[str, Any]:
    total_trades = int(metrics.get("total_trades") or 0)
    if total_trades < min_trades:
        return {
            "gate": "sample_size",
            "status": "INCONCLUSIVE",
            "reason": f"total_trades={total_trades} < {min_trades} (insufficient sample — not FAIL, not PASS)",
        }
    return {
        "gate": "sample_size",
        "status": "PASS",
        "reason": f"total_trades={total_trades} >= {min_trades}",
    }


def gate_min_trades_per_year(metrics: Dict[str, Any], min_per_year: float = 2.0) -> Dict[str, Any]:
    """A 10-year-dormant 'strategy' must not pass on a lucky 3 trades."""
    trades = metrics.get("trades", [])
    if len(trades) < 12:
        return {"gate": "min_trades_per_year", "status": "INCONCLUSIVE",
                "reason": "insufficient trade sample"}
    years = _trades_by_year(metrics)
    if not years:
        return {"gate": "min_trades_per_year", "status": "INCONCLUSIVE",
                "reason": "trade dates unavailable"}
    span = max(years) - min(years) + 1
    rate = len(trades) / span
    if rate < min_per_year:
        return {"gate": "min_trades_per_year", "status": "FAIL",
                "reason": f"{len(trades)} trades over {span} years = {rate:.2f}/yr < {min_per_year}/yr"}
    return {"gate": "min_trades_per_year", "status": "PASS",
            "reason": f"{rate:.2f} trades/yr >= {min_per_year}/yr over {span} years"}


def gate_time_stability(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Real time stability from trade dates:
    - must be net-positive in at least 55% of the years it traded, and
    - no single year may contribute more than 70% of total net profit.
    """
    years = _trades_by_year(metrics)
    if len(years) < 3:
        return {"gate": "time_stability", "status": "INCONCLUSIVE",
                "reason": f"only {len(years)} active year(s); need >= 3 for time-stability evidence"}
    net = float(metrics.get("net_profit", 0.0))
    pos_years = sum(1 for v in years.values() if v > 0)
    pos_frac = pos_years / len(years)
    # concentration: only defined when net profit is positive
    if net > 0:
        top = max(years.values())
        concentration = top / net
    else:
        concentration = 1.0
    problems = []
    if pos_frac < 0.55:
        problems.append(f"positive in only {pos_years}/{len(years)} years ({pos_frac:.0%})")
    if concentration > 0.70:
        problems.append(f"top year is {concentration:.0%} of net profit")
    if problems:
        return {"gate": "time_stability", "status": "FAIL",
                "reason": "; ".join(problems) + f" | years={ {y: round(v,2) for y,v in sorted(years.items())} }"}
    return {"gate": "time_stability", "status": "PASS",
            "reason": f"positive in {pos_years}/{len(years)} years, top-year concentration {concentration:.0%} <= 70%"}


def gate_pnl_invariant(metrics: Dict[str, Any], tolerance: float = 1.0) -> Dict[str, Any]:
    """final_value must equal initial_capital + sum(per-trade net PnL).
    Catches accounting bugs (mis-tracked positions, phantom cash, unlogged
    trades). Tolerance is $1 (rounding), not 10%."""
    trades = metrics.get("trades", [])
    if not trades:
        return {"gate": "pnl_invariant", "status": "INCONCLUSIVE", "reason": "no trades to reconcile"}
    pnl_sum = float(sum(t.get("pnl_net", 0.0) for t in trades))
    expected = float(metrics.get("initial_capital", 0.0)) + pnl_sum
    final = float(metrics.get("final_value", 0.0))
    diff = abs(final - expected)
    if diff > tolerance:
        return {"gate": "pnl_invariant", "status": "FAIL",
                "reason": f"final_value={final:.2f} vs expected={expected:.2f} (diff {diff:.2f} > ${tolerance})"}
    return {"gate": "pnl_invariant", "status": "PASS",
            "reason": f"abs(final_value - expected)={diff:.4f} <= ${tolerance}"}


def gate_outlier_resistance(metrics: Dict[str, Any]) -> Dict[str, Any]:
    trades = metrics.get("trades", [])
    total_trades = int(metrics.get("total_trades") or 0)
    if total_trades < 12:
        return {"gate": "outlier_resistance", "status": "INCONCLUSIVE",
                "reason": f"total_trades={total_trades} < 12"}
    net_profit = float(metrics.get("net_profit", 0.0))
    if net_profit <= 0:
        return {"gate": "outlier_resistance", "status": "FAIL",
                "reason": f"net_profit={net_profit:.2f} <= 0 (strategy loses money)"}
    top1 = max(abs(float(t.get("pnl_net", 0.0))) for t in trades)
    top1_ratio = top1 / net_profit
    pf = float(metrics.get("profit_factor") or 0.0)
    if top1_ratio > 0.35:
        return {"gate": "outlier_resistance", "status": "FAIL",
                "reason": f"single trade is {top1_ratio:.0%} of net profit (lucky-trade risk)"}
    if pf > 10.0:
        return {"gate": "outlier_resistance", "status": "FAIL",
                "reason": f"profit_factor={pf:.1f} implausibly high (likely artifact)"}
    return {"gate": "outlier_resistance", "status": "PASS",
            "reason": f"top1_ratio={top1_ratio:.3f} <= 0.35, profit_factor={pf:.2f} in (0, 10]"}


def gate_max_drawdown(metrics: Dict[str, Any], max_dd_pct: float = 30.0) -> Dict[str, Any]:
    dd = float(metrics.get("max_drawdown_pct") or 0.0)
    if metrics.get("total_trades", 0) < 12:
        return {"gate": "max_drawdown", "status": "INCONCLUSIVE", "reason": "insufficient trade sample"}
    if dd > max_dd_pct:
        return {"gate": "max_drawdown", "status": "FAIL",
                "reason": f"max_drawdown={dd:.1f}% > {max_dd_pct:.0f}%"}
    return {"gate": "max_drawdown", "status": "PASS",
            "reason": f"max_drawdown={dd:.1f}% <= {max_dd_pct:.0f}%"}


def gate_cost_ratio(metrics: Dict[str, Any], max_cost_frac: float = 0.50) -> Dict[str, Any]:
    """Execution costs (commission + swap) must not dominate the strategy.
    If costs >= 50% of gross profit, the 'edge' is mostly a cost artifact —
    it will not survive live spread/execution variance."""
    trades = metrics.get("trades", [])
    if len(trades) < 12:
        return {"gate": "cost_ratio", "status": "INCONCLUSIVE", "reason": "insufficient trade sample"}
    gross = sum(max(0.0, float(t.get("pnl_gross", 0.0))) for t in trades)
    costs = sum(abs(float(t.get("commission_paid", 0.0))) + abs(float(t.get("swap_paid", 0.0))) for t in trades)
    if gross <= 0:
        return {"gate": "cost_ratio", "status": "FAIL", "reason": "gross profit <= 0 (costs exceed all wins)"}
    frac = costs / gross
    if frac > max_cost_frac:
        return {"gate": "cost_ratio", "status": "FAIL",
                "reason": f"costs={costs:.2f} = {frac:.0%} of gross profit {gross:.2f} (> {max_cost_frac:.0%})"}
    net = float(metrics.get("net_profit", 0.0))
    if net <= 0:
        return {"gate": "cost_ratio", "status": "FAIL",
                "reason": f"net profit after costs = {net:.2f} <= 0 (edge does not survive execution costs)"}
    return {"gate": "cost_ratio", "status": "PASS",
            "reason": f"costs={costs:.2f} = {frac:.0%} of gross, net after costs = {net:.2f} > 0"}


def gate_open_position(metrics: Dict[str, Any]) -> Dict[str, Any]:
    if metrics.get("error_flag"):
        return {"gate": "open_position", "status": "FAIL",
                "reason": f"run error flag: {metrics['error_flag']}"}
    return {"gate": "open_position", "status": "PASS", "reason": "no run error flags"}


GATES = [
    gate_sample_size,
    gate_min_trades_per_year,
    gate_time_stability,
    gate_pnl_invariant,
    gate_outlier_resistance,
    gate_max_drawdown,
    gate_cost_ratio,
    gate_open_position,
]


def evaluate(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """PASS only if ALL gates PASS. Any FAIL => FAIL.
    No FAIL but any INCONCLUSIVE => INCONCLUSIVE (evidence insufficient)."""
    gates: List[Dict[str, Any]] = [g(metrics) for g in GATES]
    statuses = [g["status"] for g in gates]
    if "FAIL" in statuses:
        status = "FAIL"
        reason = "AT_LEAST_ONE_GATE_FAILED: " + "; ".join(g["reason"] for g in gates if g["status"] == "FAIL")
    elif "INCONCLUSIVE" in statuses:
        status = "INCONCLUSIVE"
        reason = "INSUFFICIENT_EVIDENCE: " + "; ".join(g["reason"] for g in gates if g["status"] == "INCONCLUSIVE")
    else:
        status = "PASS"
        reason = "ALL_GATES_PASSED"
    return {"status": status, "gates": gates, "reason": reason}
