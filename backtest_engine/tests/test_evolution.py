"""Tests for the self-evolution subsystem (genome, fitness, governance,
genome->spec bridge). These are fast unit tests (no full backtests); the real
end-to-end `pipeline.py evolve` (real backtests + real audit) is exercised
separately as an integration check.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backtest_engine"))

from evolution.genome import (  # noqa: E402
    Genome, DEFAULT_GENOME, BOUNDS, mutate, GenomeValidationError,
)
from evolution.fitness import compute_fitness  # noqa: E402
from evolution import governance  # noqa: E402
from evolution.genome_generator import genome_generate_specs  # noqa: E402


# ── Genome ──────────────────────────────────────────────────────────────

def test_default_genome_validates():
    g = Genome.from_dict(DEFAULT_GENOME)
    assert g.validate() is g


def test_unknown_field_fails_closed():
    bad = dict(DEFAULT_GENOME)
    bad["not_a_gene"] = 1
    with pytest.raises(GenomeValidationError):
        Genome.from_dict(bad)


def test_out_of_bounds_fails_closed():
    bad = dict(DEFAULT_GENOME)
    bad["mutation_rate"] = 1.5  # > 1.0
    with pytest.raises(GenomeValidationError):
        Genome.from_dict(bad)


def test_inconsistent_ranges_fail_closed():
    bad = dict(DEFAULT_GENOME)
    bad["tp_min"] = 0.05
    bad["tp_max"] = 0.01  # min > max
    with pytest.raises(GenomeValidationError):
        Genome.from_dict(bad)


def test_mutation_stays_within_bounds_and_valid():
    g = Genome.from_dict(DEFAULT_GENOME)
    rng = random.Random(7)
    for _ in range(50):
        c = mutate(g, rng)
        c.validate()  # must not raise
        for key, (lo, hi) in BOUNDS.items():
            assert lo <= getattr(c, key) <= hi, key
        # monotone invariants preserved
        assert c.lookback_min <= c.lookback_max
        assert c.tp_min <= c.tp_max
        assert c.sl_min <= c.sl_max
        assert c.bbands_dev_min <= c.bbands_dev_max


def test_mutation_is_deterministic_per_seed():
    g = Genome.from_dict(DEFAULT_GENOME)
    a = mutate(g, random.Random(123))
    b = mutate(g, random.Random(123))
    assert a.to_dict() == b.to_dict()


# ── Fitness (mechanism-agnostic) ─────────────────────────────────────────

def test_fitness_counts_survivors_only():
    res = [
        {"status": "PASS", "net_return_pct": 12, "max_drawdown_pct": 15, "total_trades": 50},
        {"status": "PASS", "net_return_pct": 8, "max_drawdown_pct": 20, "total_trades": 30},
        {"status": "FAIL", "net_return_pct": -4, "max_drawdown_pct": 25, "total_trades": 40},
        {"status": "INCONCLUSIVE", "net_return_pct": 0, "max_drawdown_pct": 5, "total_trades": 3},
    ]
    f = compute_fitness(res)
    assert f["pass_count"] == 2
    assert f["fail_count"] == 1
    assert f["inconclusive_count"] == 1
    assert f["total_evaluated"] == 4
    assert f["pass_rate"] == 0.5
    assert f["mean_pass_return_pct"] == pytest.approx(10.0)


def test_fitness_ranks_survivors_over_losses():
    better = [{"status": "PASS", "net_return_pct": 20, "max_drawdown_pct": 10, "total_trades": 60}]
    worse = [{"status": "FAIL", "net_return_pct": -10, "max_drawdown_pct": 40, "total_trades": 60}]
    assert compute_fitness(better)["rank_key"] > compute_fitness(worse)["rank_key"]


def test_fitness_empty():
    f = compute_fitness([])
    assert f["pass_count"] == 0 and f["total_evaluated"] == 0
    assert f["rank_key"] == (0, 0.0, 0.0, 0.0)


def test_fitness_does_not_read_mechanism_or_name():
    # Two result sets identical in the fitness-relevant fields but with
    # different (fake) strategy identities must have equal fitness.
    a = [{"status": "PASS", "net_return_pct": 15, "max_drawdown_pct": 12,
          "total_trades": 40, "strategy_name": "rsi_reversion"}]
    b = [{"status": "PASS", "net_return_pct": 15, "max_drawdown_pct": 12,
          "total_trades": 40, "strategy_name": "donchian_breakout"}]
    assert compute_fitness(a)["rank_key"] == compute_fitness(b)["rank_key"]


# ── Governance (harmlessness + rejected store) ───────────────────────────

def test_risk_classification_low_high():
    low = {"kind": "genome_gene", "path": "backtest_engine/evolution/genome.json"}
    assert governance.classify_risk(low) == "LOW"
    high = {"kind": "gate_threshold", "path": "backtest_engine/audit/gates.py"}
    assert governance.classify_risk(high) == "HIGH"
    # an unknown kind is HIGH (fail-closed)
    assert governance.classify_risk({"kind": "mystery"}) == "HIGH"


def test_harmlessness_blocks_protected_surfaces():
    assert governance.is_protected("backtest_engine/audit/gates.py")
    assert governance.is_protected("backtest_engine/data/manifest.json")
    assert governance.is_protected("backtest_engine/synthesis_layer/grammar.py")
    assert governance.is_protected("backtest_engine/.env")
    # even a "low" label can't touch a protected path
    evil = {"kind": "genome_gene", "path": "backtest_engine/audit/gates.py"}
    assert governance.classify_risk(evil) == "HIGH"
    check = governance.harmlessness_check(evil)
    assert check["ok"] is False


def test_rejected_store_roundtrip_and_review_cadence(tmp_path):
    store = tmp_path / "rejected_proposals.jsonl"
    prop = {"id": "p1", "kind": "gate_threshold", "path": "backtest_engine/audit/gates.py",
            "description": "lower sample size", "detail": {}}
    entry = governance.record_rejected(prop, "test", generation=1, store_path=store)
    # first review is due at gen 1 + REVIEW_EVERY_N_GENS = 6
    assert entry["next_review_gen"] == 1 + governance.REVIEW_EVERY_N_GENS
    first_due = 1 + governance.REVIEW_EVERY_N_GENS
    # not due before the first review point
    assert governance.due_for_review(first_due - 1, store) == []
    due = governance.due_for_review(first_due, store)
    assert [e["id"] for e in due] == ["p1"]
    governance.mark_reviewed(due, first_due, store)
    # rejected proposals STAY in the re-review cycle (never silently dropped):
    # next review is pushed out by one more cadence and is due again
    assert [e["id"] for e in governance.due_for_review(first_due, store)] == []
    assert [e["id"] for e in governance.due_for_review(2 * first_due, store)] == ["p1"]
    st = governance._read_store(store)[0]
    assert st["status"] == "pending_review"
    assert st["last_review_gen"] == first_due
    # a human can explicitly accept one, which takes it out of the cycle
    governance.mark_reviewed(due, 2 * first_due, store, disposition="accepted_manually")
    assert [e["id"] for e in governance.due_for_review(3 * first_due, store)] == []
    assert governance._read_store(store)[0]["status"] == "accepted_manually"


# ── Genome -> spec bridge ────────────────────────────────────────────────

def test_genome_generate_produces_valid_distinct_specs(tmp_path):
    g = Genome.from_dict(DEFAULT_GENOME)
    specs = genome_generate_specs(g, 5, tmp_path, rng=random.Random(1))
    assert len(specs) == 5
    from synthesis_layer.strategy_schema import dedupe_key
    keys = [dedupe_key(s) for s in specs]
    assert len(set(keys)) == len(keys)  # distinct theses
    for s in specs:
        # every lookback must respect the genome's declared bounds
        for ind in s.indicators:
            if "lookback" in ind:
                assert g.lookback_min <= ind["lookback"] <= g.lookback_max
