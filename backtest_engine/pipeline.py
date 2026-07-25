#!/usr/bin/env python3
"""
pipeline.py - single entry point for the autonomous strategy MVP.

Commands:
    pipeline.py validate-data
    pipeline.py generate --count N
    pipeline.py backtest --spec-dir strategies/ --out results/raw/
    pipeline.py audit-gen N
    pipeline.py audit-json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path("/home/ml/projects/autonomous_quant_agent")
sys.path.insert(0, str(PROJECT_ROOT / "backtest_engine"))

from engine.runner import run_backtest  # noqa: E402
from synthesis_layer.strategy_generator import generate_specs  # noqa: E402
from synthesis_layer.strategy_schema import validate_spec, read_spec  # noqa: E402
from compiler.dynamic_loader import compile_spec  # noqa: E402
from audit.gates import evaluate  # noqa: E402
from audit.writer import write_audit  # noqa: E402

import backtrader as bt  # noqa: E402


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def validate_data() -> int:
    manifest_path = PROJECT_ROOT / "backtest_engine/data/manifest.json"
    if not manifest_path.exists():
        print("manifest missing:", manifest_path)
        return 1
    manifest = json.loads(manifest_path.read_text())
    missing = [path for path in manifest.get("data_paths", {}).values() if not (PROJECT_ROOT / "backtest_engine" / path).exists()]
    if missing:
        print("missing data files:", missing)
        return 1
    print("validate-data OK")
    return 0


def generate(args: argparse.Namespace) -> int:
    out_dir = PROJECT_ROOT / "backtest_engine/strategies"
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = generate_specs(args.count, out_dir)
    print(f"generated {len(specs)} specs under {out_dir}")
    return 0 if specs else 1


def backtest(args: argparse.Namespace) -> int:
    spec_dir = Path(args.spec_dir)
    out_root = Path(args.out)
    run_id = _timestamp()
    out_run = out_root / f"run_{run_id}"
    out_run.mkdir(parents=True, exist_ok=True)
    spec_files = sorted(spec_dir.glob("spec_*.json"))
    if not spec_files:
        print("no specs found:", spec_dir)
        return 1
    for spec_file in spec_files:
        print("backtesting:", spec_file.name)
        raw = read_spec(spec_file)
        spec = validate_spec(raw)
        strategy_cls = compile_spec(raw)
        metrics = run_backtest(
            strategy_class=strategy_cls,
            data_class=bt.feeds.PandasData,
            data_source=str(PROJECT_ROOT / "backtest_engine/data_lake/XAUUSD_H4.parquet"),
            start_date="2006-01-01",
            end_date="2026-01-01",
            initial_capital=10_000.0,
            commission=0.0,
            position_sizing="fixed",
            stake=1,
            coc=False,
            coo=True,
            swap_rate_long_per_lot=0.0,
            swap_rate_short_per_lot=0.0,
            warmup_bars=50,
        )
        out_run.joinpath(f"{spec.spec_id}_metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
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
    return 0


def audit_json(args: argparse.Namespace) -> int:
    root = PROJECT_ROOT / "backtest_engine/results/audit"
    summary = {"generated_at": _timestamp(), "files": []}
    for path in sorted(root.glob("audit_gen_*.json")):
        summary["files"].append(str(path))
    print(json.dumps(summary, indent=2))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = __import__("argparse").ArgumentParser(prog="pipeline.py")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("validate-data")

    gen = sub.add_parser("generate")
    gen.add_argument("--count", type=int, default=10)

    bt_parser = sub.add_parser("backtest")
    bt_parser.add_argument("--spec-dir", default=str(PROJECT_ROOT / "backtest_engine/strategies"))
    bt_parser.add_argument("--out", default=str(PROJECT_ROOT / "backtest_engine/results/raw"))

    audit_parser = sub.add_parser("audit-gen")
    audit_parser.add_argument("generation", nargs="?", default=None)

    sub.add_parser("audit-json")

    args = parser.parse_args(argv)
    if args.command == "validate-data":
        return validate_data()
    if args.command == "generate":
        return generate(args)
    if args.command == "backtest":
        return backtest(args)
    if args.command == "audit-gen":
        return audit_gen(args)
    if args.command == "audit-json":
        return audit_json(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
