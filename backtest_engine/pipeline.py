#!/usr/bin/env python3
"""
pipeline.py - single entry point for the autonomous strategy MVP.

Commands:
    pipeline.py validate-data
    pipeline.py generate --count N
    pipeline.py backtest --spec-dir strategies/ --out results/raw/
    pipeline.py audit-gen [generation]
    pipeline.py audit-json

Execution is driven by the frozen data contract in data/manifest.json:
commission (COMM_FIXED $/oz per leg), spread, slippage, swap rates, and
leverage all come from the manifest — never hard-coded here.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import polars as pl

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backtest_engine"))

from engine.runner import run_backtest  # noqa: E402
from synthesis_layer.strategy_generator import generate_specs  # noqa: E402
from synthesis_layer.strategy_schema import validate_spec, read_spec  # noqa: E402
from compiler.dynamic_loader import compile_spec  # noqa: E402
from audit.gates import evaluate  # noqa: E402
from audit.writer import write_audit  # noqa: E402


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _load_manifest() -> Dict[str, Any]:
    manifest_path = PROJECT_ROOT / "backtest_engine/data/manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest missing: {manifest_path}")
    return json.loads(manifest_path.read_text())


def _git_sha() -> str:
    import subprocess
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10,
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def validate_data() -> int:
    """Verify frozen data exists AND is sane: no NaNs, no duplicate
    timestamps, monotonically increasing, contiguous enough, within the
    manifest's stated range."""
    manifest = _load_manifest()
    issues: List[str] = []
    for tf, rel in manifest.get("data_paths", {}).items():
        path = PROJECT_ROOT / "backtest_engine" / rel
        if not path.exists():
            issues.append(f"{tf}: missing {path}")
            continue
        df = pl.read_parquet(path)
        if "datetime" not in df.columns and {"date", "time"}.issubset(df.columns):
            df = df.with_columns(
                pl.concat_str([pl.col("date").cast(pl.String), pl.lit(" "), pl.col("time").cast(pl.String)])
                .str.strptime(pl.Datetime, format="%Y-%m-%d %H:%M:%S", strict=False).alias("datetime")
            )
        if "datetime" not in df.columns:
            issues.append(f"{tf}: no datetime column")
            continue
        n = len(df)
        if n == 0:
            issues.append(f"{tf}: empty")
            continue
        dt = df["datetime"]
        if dt.is_null().any():
            issues.append(f"{tf}: {int(dt.is_null().sum())} null timestamps")
        if dt.unique().len() != n:
            issues.append(f"{tf}: {n - dt.unique().len()} duplicate timestamps")
        sorted_ok = bool(dt.is_sorted())
        if not sorted_ok:
            issues.append(f"{tf}: timestamps not sorted")
        for col in ("open", "high", "low", "close"):
            if col in df.columns:
                if df[col].is_null().any():
                    issues.append(f"{tf}: nulls in {col}")
                if (df[col] <= 0).any():
                    issues.append(f"{tf}: non-positive prices in {col}")
        # bar-count sanity per timeframe
        min_bars = {"M1": 100_000, "M15": 10_000, "H1": 5_000, "H4": 1_000, "D1": 100}.get(tf, 100)
        if n < min_bars:
            issues.append(f"{tf}: only {n} bars (< {min_bars} minimum)")
    if issues:
        print("validate-data FAILED:")
        for i in issues:
            print("  -", i)
        return 1
    print("validate-data OK (all timeframes: present, clean, sorted, in-range)")
    return 0


def generate_kb(args: argparse.Namespace) -> int:
    """Pull mappable strategies from the Neo4j knowledge base into specs."""
    from synthesis_layer.neo4j_bridge import fetch_and_build_specs, write_kb_specs
    out_dir = PROJECT_ROOT / "backtest_engine/strategies"
    out_dir.mkdir(parents=True, exist_ok=True)
    prefer = [p.strip() for p in args.prefer.split(",") if p.strip()]
    specs, stats = fetch_and_build_specs(args.count, prefer_assets=prefer)
    print("bridge stats:", json.dumps(stats))
    if not specs:
        print("no mappable KB strategies produced (graph unreachable or nothing mappable)")
        return 1
    paths = write_kb_specs(specs, out_dir)
    print(f"wrote {len(paths)} KB-sourced specs to {out_dir}")
    return 0


def _load_evolved_genome():
    """Load the current baseline genome (the auto-applied LOW-risk
    improvement), or None if no evolution has run yet."""
    state_path = PROJECT_ROOT / "backtest_engine/evolution/evolution_state.json"
    if not state_path.exists():
        return None
    from evolution.genome import Genome
    return Genome.from_dict(json.loads(state_path.read_text())["baseline_genome"])


def generate(args: argparse.Namespace) -> int:
    out_dir = PROJECT_ROOT / "backtest_engine/strategies"
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.use_genome:
        genome = _load_evolved_genome()
        if genome is None:
            print("no evolved genome found (run `pipeline.py evolve` first); "
                  "falling back to the default baseline")
            genome = None
        if genome is not None:
            from evolution.genome_generator import genome_generate_specs
            specs = genome_generate_specs(genome, args.count, out_dir)
            print(f"generated {len(specs)}/{args.count} distinct genome-shaped specs under {out_dir}")
            return 0 if specs else 1
    specs = generate_specs(args.count, out_dir)
    print(f"generated {len(specs)}/{args.count} distinct specs under {out_dir}")
    return 0 if specs else 1


def evolve(args: argparse.Namespace) -> int:
    """Run the self-evolution loop: evolve the builder's genome via the EA,
    auto-apply the LOW-risk improvement, record HIGH-risk framework proposals
    to the rejected store (never auto-applied)."""
    from evolution.evolve import run_evolution
    from evolution.genome import Genome
    baseline = _load_evolved_genome()  # resume from the last auto-applied genome
    state = run_evolution(
        generations=args.generations,
        pop_size=args.pop_size,
        n_specs=args.specs_per_genome,
        start_genome=baseline,
        workdir=PROJECT_ROOT / "backtest_engine/evolution/_work",
        state_path=PROJECT_ROOT / "backtest_engine/evolution/evolution_state.json",
        rejected_store=PROJECT_ROOT / "backtest_engine/results/rejected_proposals.jsonl",
        seed=args.seed,
    )
    print(json.dumps({
        "improved_over_baseline": state["improved_over_baseline"],
        "auto_applied": state["auto_applied"],
        "best_fitness": state["best_fitness"],
        "baseline_fitness_at_eval": state["baseline_fitness_at_eval"],
        "new_baseline": state["baseline_genome"]["genome_id"],
    }, indent=2))
    return 0


def _spec_data_path(manifest: Dict[str, Any], timeframe: str) -> Path:
    rel = manifest.get("data_paths", {}).get(timeframe)
    if not rel:
        raise ValueError(f"timeframe '{timeframe}' not in manifest data_paths")
    return PROJECT_ROOT / "backtest_engine" / rel


def backtest(args: argparse.Namespace) -> int:
    manifest = _load_manifest()
    broker = manifest.get("broker", {})
    comm_cfg = manifest.get("commission", {})
    spread_cfg = manifest.get("spread", {})
    swap_cfg = manifest.get("swap", {})
    slip_cfg = manifest.get("slippage", {})

    initial_capital = float(broker.get("initial_cash", 10_000.0))
    leverage = float(broker.get("leverage", 100.0))
    # manifest commission: fixed round-turn per lot ($7/lot) => per oz per leg
    # = value / (contract_size * 2)
    contract_size = float(broker.get("contract_size", 100.0))
    commission_per_oz_leg = float(comm_cfg.get("value", 7.0)) / (contract_size * 2.0)
    spread_total = float(spread_cfg.get("total", 0.50))
    slippage = float(slip_cfg.get("pct", 0.0)) / 100.0  # manifest pct -> fraction
    swap_enabled = bool(swap_cfg.get("enabled", False))
    swap_long = float(swap_cfg.get("long_per_lot", 0.0))
    swap_short = float(swap_cfg.get("short_per_lot", 0.0))

    spec_dir = Path(args.spec_dir)
    out_root = Path(args.out)
    run_id = _timestamp()
    out_run = out_root / f"run_{run_id}"
    out_run.mkdir(parents=True, exist_ok=True)

    # Frozen test period from the manifest (recommended_start .. recommended_end)
    test_period = manifest.get("test_periods", {})
    start_date = test_period.get("recommended_start", "2006-01-01")
    end_date = test_period.get("recommended_end", "2025-01-01")

    spec_files = sorted(spec_dir.glob("spec_*.json"))
    if not spec_files:
        print("no specs found:", spec_dir)
        return 1

    run_manifest = {
        "run_id": run_id,
        "git_sha": _git_sha(),
        "generated_at": _timestamp(),
        "manifest": {
            "commission_per_oz_leg": commission_per_oz_leg,
            "spread_total": spread_total,
            "slippage_frac": slippage,
            "swap": {"enabled": swap_enabled, "long": swap_long, "short": swap_short},
            "initial_capital": initial_capital,
            "leverage": leverage,
            "period": [start_date, end_date],
        },
        "specs": [],
    }

    for spec_file in spec_files:
        print("backtesting:", spec_file.name)
        raw = read_spec(spec_file)
        spec = validate_spec(raw)
        strategy_cls = compile_spec(raw)
        timeframe = spec.timeframe
        data_path = _spec_data_path(manifest, timeframe)
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
        metrics["spec_id"] = spec.spec_id
        metrics["timeframe"] = timeframe
        out_run.joinpath(f"{spec.spec_id}_metrics.json").write_text(
            json.dumps(metrics, indent=2, default=str)
        )
        run_manifest["specs"].append({
            "spec_id": spec.spec_id,
            "thesis_id": spec.thesis_id,
            "mechanism_class": spec.mechanism_class,
            "timeframe": timeframe,
            "condition": " | ".join(r.get("condition", "") for r in spec.entry_rules),
            "total_trades": metrics.get("total_trades"),
            "total_return_pct": metrics.get("total_return_pct"),
            "error_flag": metrics.get("error_flag"),
        })

    (out_run / "run_manifest.json").write_text(json.dumps(run_manifest, indent=2, default=str))
    (out_run / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("wrote results to:", out_run)
    return 0


def audit_gen(args: argparse.Namespace) -> int:
    results_root = PROJECT_ROOT / "backtest_engine/results/raw"
    audit_root = PROJECT_ROOT / "backtest_engine/results/audit"
    run_dirs = sorted(results_root.glob("run_*"))
    if not run_dirs:
        print("no raw results found")
        return 1
    latest = run_dirs[-1]
    metrics_files = sorted(latest.glob("*_metrics.json"))
    generation = args.generation if args.generation else latest.name.split("_")[-1]
    items: List[Dict[str, Any]] = []
    for metrics_file in metrics_files:
        metrics = json.loads(metrics_file.read_text())
        result = evaluate(metrics)
        spec_id = metrics_file.name.replace("_metrics.json", "")
        items.append({
            "spec_id": spec_id,
            "result": result,
            "metrics": metrics,
        })
    path = write_audit(str(generation), items, audit_root / f"audit_gen_{generation}.json")
    print("audit written:", path)
    for it in items:
        print(f"  {it['spec_id']}: {it['result']['status']}")
    return 0


def audit_json(args: argparse.Namespace) -> int:
    root = PROJECT_ROOT / "backtest_engine/results/audit"
    summary = {"generated_at": _timestamp(), "files": []}
    for path in sorted(root.glob("audit_gen_*.json")):
        data = json.loads(path.read_text())
        statuses = {}
        for it in data.get("items", []):
            st = it.get("result", {}).get("status", "?")
            statuses[st] = statuses.get(st, 0) + 1
        summary["files"].append({"path": str(path), "status_counts": statuses})
    print(json.dumps(summary, indent=2))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline.py")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("validate-data")

    gen = sub.add_parser("generate")
    gen.add_argument("--count", type=int, default=10)
    gen.add_argument("--use-genome", action="store_true",
                     help="shape specs with the auto-applied evolved genome (from `pipeline.py evolve`)")

    gen_kb = sub.add_parser("generate-kb", help="pull mappable strategies from the Neo4j knowledge base")
    gen_kb.add_argument("--count", type=int, default=8)
    gen_kb.add_argument("--prefer", default="Gold (XAUUSD),Forex,Commodities,Commodities (Gold)")

    ev = sub.add_parser("evolve", help="run the self-evolution EA (evolve the builder's genome)")
    ev.add_argument("--generations", type=int, default=2)
    ev.add_argument("--pop-size", type=int, default=2)
    ev.add_argument("--specs-per-genome", type=int, default=3)
    ev.add_argument("--seed", type=int, default=20260915)

    bt_parser = sub.add_parser("backtest")
    bt_parser.add_argument("--spec-dir", default=str(PROJECT_ROOT / "backtest_engine/strategies"))
    bt_parser.add_argument("--out", default=str(PROJECT_ROOT / "backtest_engine/results/raw"))

    audit_parser = sub.add_parser("audit-gen")
    audit_parser.add_argument("generation", nargs="?", default=None)

    sub.add_parser("audit-json")

    serve = sub.add_parser("serve", help="run the control-plane UI + API server")
    serve.add_argument("--port", type=int, default=8050)
    serve.add_argument("--host", default="127.0.0.1")

    args = parser.parse_args(argv)
    if args.command == "validate-data":
        return validate_data()
    if args.command == "generate":
        return generate(args)
    if args.command == "evolve":
        return evolve(args)
    if args.command == "generate-kb":
        return generate_kb(args)
    if args.command == "backtest":
        return backtest(args)
    if args.command == "audit-gen":
        return audit_gen(args)
    if args.command == "audit-json":
        return audit_json(args)
    if args.command == "serve":
        import uvicorn
        uvicorn.run("ui.server:app", host=args.host, port=args.port, log_level="warning")
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
