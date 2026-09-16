"""Fitness for the self-evolution loop.

A genome's fitness is derived from the REAL audit outcomes of the distinct
theses it generates: how many survive the frozen, skeptical gates (PASS), how
many are inconclusive, how many fail, plus a conservative quality tiebreak on
the survivors. It is mechanism-agnostic — it reads only status + real backtest
numbers (net return after costs, max drawdown, trade count); it never inspects
or rewards a specific indicator, mechanism class, or strategy identity.

This is the anti-overfit guardrail: a genome is only fitter if it produces MORE
genuinely-validated survivors, not one lucky outlier (the outlier_resistance
gate already rejects single-trade luck at the spec level).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# A per-spec result as produced by the eval loop (real backtest + real audit).
# Only the fields below are consumed; everything is a real number from the run.
_REQUIRED = ("status", "net_return_pct", "max_drawdown_pct", "total_trades")


def _f(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v


def compute_fitness(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute a genome's fitness from a list of per-spec real results.

    Returns a dict whose "rank_key" is a comparable tuple used for selection:
      1. pass_count          (primary: # distinct theses that survive all gates)
      2. pass_rate           (#PASS / total evaluated)
      3. mean_pass_return    (conservative quality tiebreak among survivors,
                              real net return AFTER costs; 0.0 if none PASS)
      4. -mean_pass_dd       (prefer lower drawdown among survivors)
    Higher is fitter. No mechanism-class or strategy-name is read.
    """
    total = len(results)
    if total == 0:
        return {
            "pass_count": 0, "inconclusive_count": 0, "fail_count": 0,
            "total_evaluated": 0, "pass_rate": 0.0,
            "mean_pass_return_pct": 0.0, "mean_pass_max_dd_pct": 0.0,
            "score": 0.0, "rank_key": (0, 0.0, 0.0, 0.0),
        }

    pass_results = [r for r in results if r.get("status") == "PASS"]
    inc_results = [r for r in results if r.get("status") == "INCONCLUSIVE"]
    fail_results = [r for r in results if r.get("status") == "FAIL"]

    pass_count = len(pass_results)
    pass_rate = pass_count / total

    # Conservative quality of the survivors only (real, after-cost numbers).
    if pass_results:
        mean_pass_return = sum(_f(r.get("net_return_pct")) for r in pass_results) / pass_count
        mean_pass_dd = sum(_f(r.get("max_drawdown_pct")) for r in pass_results) / pass_count
    else:
        mean_pass_return = 0.0
        mean_pass_dd = 0.0

    # A single scalar for logging (NOT the selection key). Dominated by the
    # count of genuinely-validated survivors; the quality terms are small.
    score = pass_count * 10.0 + pass_rate * 5.0 + mean_pass_return * 0.1 - mean_pass_dd * 0.05

    rank_key: Tuple = (pass_count, pass_rate, mean_pass_return, -mean_pass_dd)
    return {
        "pass_count": pass_count,
        "inconclusive_count": len(inc_results),
        "fail_count": len(fail_results),
        "total_evaluated": total,
        "pass_rate": round(pass_rate, 4),
        "mean_pass_return_pct": round(mean_pass_return, 4),
        "mean_pass_max_dd_pct": round(mean_pass_dd, 4),
        "score": round(score, 4),
        "rank_key": rank_key,
    }
