"""The self-evolution loop (evolutionary algorithm over the builder's genome).

Flow per generation:
  1. Maintain a population of genomes (elitism: the incumbent baseline is
     always retained; the rest are mutated offspring).
  2. For each genome: generate N distinct specs shaped by the genome's search
     distribution, backtest each against the FROZEN data lake, and audit each
     with the REAL skeptical gates. Fitness = mechanism-agnostic count of
     genuinely-PASS survivors (fitness.py).
  3. Select the fittest genome; if it beats the baseline, it is proposed as a
     LOW-risk improvement (auto-applied: becomes the new baseline genome and is
     committed). If it does not beat baseline, baseline is kept (no regression).
  4. The EA observes its OWN failure modes (why things are INCONCLUSIVE/FAIL)
     and emits HIGH-risk framework proposals (gate/manifest/grammar changes).
     These are NEVER auto-applied — they go to results/rejected_proposals.jsonl
     and are re-reviewed every 5 generations by a human.

Every fitness number is a real backtest result + real audit verdict. No stubs.
"""
from __future__ import annotations

import copy
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import sys

ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from evolution.genome import Genome, DEFAULT_GENOME, mutate as mutate_genome  # noqa: E402
from evolution.genome_generator import genome_generate_specs  # noqa: E402
from evolution.fitness import compute_fitness  # noqa: E402
from evolution import governance  # noqa: E402

from engine.runner import run_backtest  # noqa: E402
from compiler.dynamic_loader import compile_spec  # noqa: E402
from synthesis_layer.strategy_schema import validate_spec, read_spec  # noqa: E402
from audit.gates import evaluate as audit_evaluate  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_manifest() -> Dict[str, Any]:
    return json.loads((ENGINE_ROOT / "data/manifest.json").read_text())


def _spec_data_path(manifest: Dict[str, Any], timeframe: str) -> Path:
    rel = manifest.get("data_paths", {}).get(timeframe)
    if not rel:
        raise ValueError(f"timeframe '{timeframe}' not in manifest")
    return ENGINE_ROOT / rel


def backtest_spec(raw: Dict[str, Any], manifest: Dict[str, Any], tf: str) -> Dict[str, Any]:
    """One real backtest of a raw spec on the frozen data lake. Returns the
    runner metrics (real numbers)."""
    broker = manifest.get("broker", {})
    comm_cfg = manifest.get("commission", {})
    spread_cfg = manifest.get("spread", {})
    swap_cfg = manifest.get("swap", {})
    slip_cfg = manifest.get("slippage", {})
    initial_capital = float(broker.get("initial_cash", 10_000.0))
    contract_size = float(broker.get("contract_size", 100.0))
    commission_per_oz_leg = float(comm_cfg.get("value", 7.0)) / (contract_size * 2.0)
    spread_total = float(spread_cfg.get("total", 0.50))
    slippage = float(slip_cfg.get("pct", 0.0)) / 100.0
    swap_enabled = bool(swap_cfg.get("enabled", False))
    swap_long = float(swap_cfg.get("long_per_lot", 0.0))
    swap_short = float(swap_cfg.get("short_per_lot", 0.0))
    test_period = manifest.get("test_periods", {})
    start_date = test_period.get("recommended_start", "2006-01-01")
    end_date = test_period.get("recommended_end", "2025-01-01")

    spec = validate_spec(raw)
    strategy_cls = compile_spec(raw)
    data_path = _spec_data_path(manifest, spec.timeframe)
    metrics = run_backtest(
        strategy_class=strategy_cls,
        data_source=str(data_path),
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        commission=commission_per_oz_leg,
        slippage=slippage,
        spread_total=spread_total,
        swap_enabled=swap_enabled,
        swap_rate_long_per_lot=swap_long,
        swap_rate_short_per_lot=swap_short,
        warmup_bars=300,
    )
    return metrics


def _audit_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    return audit_evaluate(metrics)


def _spec_result(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Per-spec real result for fitness (status + real after-cost numbers)."""
    status = _audit_metrics(metrics)["status"]
    return {
        "status": status,
        "net_return_pct": metrics.get("total_return_pct", 0.0),
        "max_drawdown_pct": metrics.get("max_drawdown_pct", 0.0),
        "total_trades": metrics.get("total_trades", 0),
    }


def evaluate_genome(genome: Genome, n_specs: int, manifest: Dict[str, Any],
                   workdir: Path, rng: random.Random) -> Dict[str, Any]:
    """Generate n_specs distinct specs for `genome`, backtest + audit each
    (real), and compute fitness. Returns {genome_id, fitness, spec_results,
    inconclusive_gates, fail_gates}."""
    spec_dir = Path(workdir) / f"specs_{genome.genome_id}"
    specs = genome_generate_specs(genome, n_specs, spec_dir, rng=rng)
    results: List[Dict[str, Any]] = []
    inconclusive_gates: Dict[str, int] = {}
    fail_gates: Dict[str, int] = {}
    for spec in specs:
        raw = spec.to_dict()
        try:
            metrics = backtest_spec(raw, manifest, spec.timeframe)
        except Exception as exc:  # fail-closed: a spec that cannot run is a loss
            results.append({"status": "FAIL", "net_return_pct": 0.0,
                            "max_drawdown_pct": 0.0, "total_trades": 0,
                            "error": str(exc)})
            continue
        res = _spec_result(metrics)
        results.append(res)
        if res["status"] in ("FAIL", "INCONCLUSIVE"):
            for g in _audit_metrics(metrics)["gates"]:
                if g["status"] == "FAIL":
                    fail_gates[g["gate"]] = fail_gates.get(g["gate"], 0) + 1
                elif g["status"] == "INCONCLUSIVE":
                    inconclusive_gates[g["gate"]] = inconclusive_gates.get(g["gate"], 0) + 1
    fitness = compute_fitness(results)
    return {
        "genome_id": genome.genome_id,
        "fitness": fitness,
        "spec_results": results,
        "inconclusive_gates": inconclusive_gates,
        "fail_gates": fail_gates,
    }


def _select_and_breed(pop_evals: List[Dict[str, Any]], pop_genomes: List[Genome],
                      pop_size: int, rng: random.Random) -> List[Genome]:
    """Elitism (keep the fittest) + mutate to fill the next population."""
    order = sorted(range(len(pop_evals)),
                   key=lambda i: pop_evals[i]["fitness"]["rank_key"], reverse=True)
    next_genomes: List[Genome] = []
    # Elitism: keep the single fittest genome unchanged.
    next_genomes.append(pop_genomes[order[0]].clone())
    # Fill the rest by mutating the top genomes (biased toward the fittest).
    i = 1
    while len(next_genomes) < pop_size:
        src = pop_genomes[order[i % len(order)]]
        next_genomes.append(mutate_genome(src, rng))
        i += 1
    return next_genomes


def _observe_high_risk_proposals(gen: int, pop_evals: List[Dict[str, Any]],
                                 store: Path) -> List[Dict[str, Any]]:
    """The EA inspects its OWN failure modes and proposes framework changes.

    These are ALWAYS HIGH-risk (they touch gates/manifest/grammar) and are
    NEVER auto-applied — recorded to the rejected store for human re-review
    every REVIEW_EVERY_N_GENS generations.
    """
    proposals: List[Dict[str, Any]] = []
    total = sum(e["fitness"]["total_evaluated"] for e in pop_evals)
    if total == 0:
        return proposals
    inc = sum(e["fitness"]["inconclusive_count"] for e in pop_evals)
    fail = sum(e["fitness"]["fail_count"] for e in pop_evals)
    ppass = sum(e["fitness"]["pass_count"] for e in pop_evals)

    # Aggregated gate failure causes across the population.
    gate_fail: Dict[str, int] = {}
    gate_inc: Dict[str, int] = {}
    for e in pop_evals:
        for k, v in e["fail_gates"].items():
            gate_fail[k] = gate_fail.get(k, 0) + v
        for k, v in e["inconclusive_gates"].items():
            gate_inc[k] = gate_inc.get(k, 0) + v

    def emit(kind, path, description, detail):
        proposal = {
            "id": f"prop_{gen}_{kind}",
            "kind": kind,
            "path": path,
            "description": description,
            "detail": detail,
        }
        risk = governance.classify_risk(proposal)
        if risk == "LOW":
            return  # LOW-risk changes are auto-applied elsewhere
        entry = governance.record_rejected(
            proposal,
            reason=description,
            generation=gen,
            store_path=store,
        )
        proposals.append(entry)

    # 1) High INCONCLUSIVE on sample_size => the sample gate is binding;
    #    propose relaxing the MINIMUM sample (a gate change).
    if gate_inc.get("sample_size", 0) >= 3:
        emit("gate_threshold", "backtest_engine/audit/gates.py",
             f"sample_size gate INCONCLUSIVE on {gate_inc['sample_size']} specs "
             f"(total evaluated {total}); consider lowering the 12-trade minimum "
             f"for the frozen H4 horizon",
             {"gate": "sample_size", "inconclusive": gate_inc["sample_size"]})

    # 2) cost_ratio FAILs => the edge is cost-dominated; propose a manifest
    #    cost/slippage review (manifest change).
    if gate_fail.get("cost_ratio", 0) >= 3:
        emit("manifest_cost", "backtest_engine/data/manifest.json",
             f"cost_ratio gate FAIL on {gate_fail['cost_ratio']} specs; the "
             f"commission/slippage/swap model may be under- or over-estimating "
             f"execution cost — human review of the manifest cost parameters",
             {"gate": "cost_ratio", "fail": gate_fail["cost_ratio"]})

    # 3) Zero PASS across the whole population => the template pool or the
    #    grammar whitelist may be too narrow (grammar change).
    if ppass == 0:
        emit("grammar_whitelist", "backtest_engine/synthesis_layer/grammar.py",
             f"no strategy PASSed in generation {gen} (evaluated {total}); the "
             f"template thesis pool or the indicator grammar whitelist may be "
             f"too narrow to contain a viable edge — human review",
             {"pass_count": 0, "total_evaluated": total})

    # 4) max_drawdown FAILs => propose a risk-sizing or DD-gate review.
    if gate_fail.get("max_drawdown", 0) >= 3:
        emit("risk_gate", "backtest_engine/audit/gates.py",
             f"max_drawdown gate FAIL on {gate_fail['max_drawdown']} specs; "
             f"consider a risk-based sizing model or a tighter DD threshold",
             {"gate": "max_drawdown", "fail": gate_fail["max_drawdown"]})
    return proposals


def _save_state(path: Path, payload: Dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str))


def run_evolution(
    generations: int = 2,
    pop_size: int = 2,
    n_specs: int = 3,
    start_genome: Optional[Genome] = None,
    manifest: Optional[Dict[str, Any]] = None,
    workdir: Path = Path("/tmp/quant_evolve"),
    state_path: Path = Path("evolution/evolution_state.json"),
    rejected_store: Path = Path("results/rejected_proposals.jsonl"),
    seed: int = 20260915,
) -> Dict[str, Any]:
    """Run the EA. Returns a summary dict (per-generation stats, best genome,
    auto-applied LOW-risk improvement, HIGH-risk proposals recorded)."""
    manifest = manifest or _load_manifest()
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    state_path = Path(state_path) if Path(state_path).is_absolute() else ENGINE_ROOT / Path(state_path)
    rejected_store = (Path(rejected_store) if Path(rejected_store).is_absolute()
                      else ENGINE_ROOT / Path(rejected_store))
    rng = random.Random(seed)

    baseline = (start_genome or Genome.from_dict(DEFAULT_GENOME)).clone()
    baseline.genome_id = "baseline"

    # Initial population: baseline + mutated offspring.
    population: List[Genome] = [baseline]
    while len(population) < pop_size:
        population.append(mutate_genome(population[0], rng))

    history: List[Dict[str, Any]] = []
    best_eval: Optional[Dict[str, Any]] = None
    best_genome: Genome = baseline.clone()

    for gen in range(1, generations + 1):
        pop_evals: List[Dict[str, Any]] = []
        for genome in population:
            r = random.Random(rng.randint(0, 2**31 - 1))
            pop_evals.append(evaluate_genome(genome, n_specs, manifest, workdir, r))
            print(f"  [gen {gen}] {genome.genome_id}: "
                  f"pass={pop_evals[-1]['fitness']['pass_count']} "
                  f"inc={pop_evals[-1]['fitness']['inconclusive_count']} "
                  f"fail={pop_evals[-1]['fitness']['fail_count']} "
                  f"score={pop_evals[-1]['fitness']['score']}")
        # Track the fittest genome overall.
        fittest_idx = max(range(len(pop_evals)), key=lambda i: pop_evals[i]["fitness"]["rank_key"])
        if best_eval is None or pop_evals[fittest_idx]["fitness"]["rank_key"] > best_eval["fitness"]["rank_key"]:
            best_eval = pop_evals[fittest_idx]
            best_genome = population[fittest_idx].clone()
        # Rejected-store re-review cadence.
        due = governance.due_for_review(gen, rejected_store)
        if due:
            governance.mark_reviewed(due, gen, rejected_store)
        # HIGH-risk framework proposals from observed failure modes.
        high_risk = _observe_high_risk_proposals(gen, pop_evals, rejected_store)
        history.append({
            "generation": gen,
            "genomes": [{
                "genome_id": e["genome_id"],
                "fitness": e["fitness"],
            } for e in pop_evals],
            "high_risk_proposals_recorded": [p["id"] for p in high_risk],
        })
        print(f"  [gen {gen}] fittest={pop_evals[fittest_idx]['genome_id']} "
              f"high_risk_proposals={[p['id'] for p in high_risk]}")
        if gen < generations:
            population = _select_and_breed(pop_evals, population, pop_size, rng)

    # ── Governance: auto-apply the LOW-risk improvement (best genome) ──────
    # The best genome only tunes the search distribution within bounds and
    # touches only the evolution/ surface => LOW risk => auto-applied as the
    # new baseline. It is only applied if it is not WORSE than the incumbent
    # baseline (no-regression guard).
    if best_eval is None:
        # No generations ran: nothing to compare or auto-apply.
        state = {
            "updated_at": _now(),
            "baseline_genome": baseline.to_dict(),
            "best_genome": baseline.to_dict(),
            "best_fitness": None,
            "baseline_fitness_at_eval": None,
            "improved_over_baseline": False,
            "auto_applied": False,
            "history": history,
            "seed": seed,
        }
        _save_state(state_path, state)
        return state

    base_eval = evaluate_genome(baseline, n_specs, manifest, workdir,
                                random.Random(rng.randint(0, 2**31 - 1)))
    improved = best_eval["fitness"]["rank_key"] > base_eval["fitness"]["rank_key"]
    auto_applied = improved
    if improved:
        baseline = best_genome
        baseline.genome_id = f"evolved_{datetime.now(timezone.utc).strftime('%Y%m%d')}"

    state = {
        "updated_at": _now(),
        "baseline_genome": baseline.to_dict(),
        "best_genome": best_genome.to_dict(),
        "best_fitness": best_eval["fitness"],
        "baseline_fitness_at_eval": base_eval["fitness"],
        "improved_over_baseline": improved,
        "auto_applied": auto_applied,
        "history": history,
        "seed": seed,
    }
    _save_state(state_path, state)
    return state
