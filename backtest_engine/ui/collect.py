"""ui/collect.py — read layer.

Parses on-disk artifacts into clean dicts for the frozen UI API contract.
Read-only: never mutates anything on disk.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import polars as pl

ENGINE_ROOT = Path(__file__).resolve().parents[1]          # backtest_engine/
STRATEGIES_DIR = ENGINE_ROOT / "strategies"
STRATEGY_ARCHIVE_DIR = STRATEGIES_DIR / "archive"
RESULTS_DIR = ENGINE_ROOT / "results"
RAW_RESULTS_DIR = RESULTS_DIR / "raw"
AUDIT_DIR = RESULTS_DIR / "audit"
DATA_MANIFEST_PATH = ENGINE_ROOT / "data" / "manifest.json"
DATA_LAKE_DIR = ENGINE_ROOT / "data_lake"
EVOLUTION_STATE_PATH = ENGINE_ROOT / "evolution" / "evolution_state.json"
KB_LAST_RUN_PATH = RESULTS_DIR / "kb_last_run.json"


def _git_sha() -> str:
    import subprocess
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ENGINE_ROOT.parent, capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _count_runs() -> int:
    if not RAW_RESULTS_DIR.is_dir():
        return 0
    return sum(1 for p in RAW_RESULTS_DIR.glob("run_*") if p.is_dir())


def _count_specs() -> int:
    if not STRATEGIES_DIR.is_dir():
        return 0
    return sum(1 for p in STRATEGIES_DIR.glob("spec_*.json"))


def _count_audit_gens() -> int:
    if not AUDIT_DIR.is_dir():
        return 0
    return sum(1 for p in AUDIT_DIR.glob("audit_gen_*.json"))


def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "git_sha": _git_sha(),
        "counts": {
            "runs": _count_runs(),
            "specs": _count_specs(),
            "audit_gens": _count_audit_gens(),
        },
    }


def _audit_for_run(run_id: str) -> Optional[Dict[str, Any]]:
    path = AUDIT_DIR / f"audit_gen_{run_id}.json"
    if not path.exists():
        return None
    try:
        return _read_json(path)
    except (json.JSONDecodeError, OSError):
        return None


def list_runs() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for run_dir in sorted(RAW_RESULTS_DIR.glob("run_*")):
        if not run_dir.is_dir():
            continue
        manifest_path = run_dir / "run_manifest.json"
        if not manifest_path.exists():
            continue
        manifest = _read_json(manifest_path)
        specs = manifest.get("specs", [])
        run_id = manifest.get("run_id", run_dir.name)
        audit = _audit_for_run(run_id)
        summary = (audit or {}).get("summary", {})
        out.append({
            "run_id": run_id,
            "git_sha": manifest.get("git_sha", _git_sha()),
            "generated_at": manifest.get("generated_at"),
            "spec_count": len(specs),
            "pass": summary.get("PASS", 0),
            "fail": summary.get("FAIL", 0),
            "inconclusive": summary.get("INCONCLUSIVE", 0),
            "error_flags": sum(
                1 for s in specs if s.get("error_flag") is not None
            ),
            "has_audit": audit is not None,
            "period": (manifest.get("manifest") or {}).get("period"),
        })
    return out


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    run_dir = RAW_RESULTS_DIR / f"run_{run_id}"
    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.exists():
        return None
    manifest = _read_json(manifest_path)
    specs_out: List[Dict[str, Any]] = []
    for entry in manifest.get("specs", []):
        spec_id = entry.get("spec_id")
        metrics_path = run_dir / f"{spec_id}_metrics.json"
        if metrics_path.exists():
            metrics = _read_json(metrics_path)
        else:
            metrics = {}
        specs_out.append({
            "spec_id": spec_id,
            "thesis_id": entry.get("thesis_id"),
            "mechanism_class": entry.get("mechanism_class"),
            "timeframe": entry.get("timeframe"),
            "condition": entry.get("condition"),
            "error_flag": entry.get("error_flag"),
            "metrics": metrics,
            "trades": metrics.get("trades", []),
        })
    return {
        "run_id": manifest.get("run_id", run_id),
        "manifest": manifest,
        "specs": specs_out,
    }


# Whitelist of metrics shown in the (lightweight) audit list view. The full
# metrics (incl. the trades array) live in the per-item detail endpoint.
AUDIT_ITEM_METRIC_KEYS = (
    "total_return_pct", "annualized_return", "max_drawdown_pct", "sharpe_ratio",
    "sortino_ratio", "calmar_ratio", "sqn", "total_trades", "closed_trades",
    "win_rate", "profit_factor", "total_commissions", "buy_and_hold_return_pct",
    "max_consecutive_losses", "avg_mafe", "avg_mfe", "data_start_date",
    "data_end_date", "total_bars", "error_flag",
)


def _light_metrics(metrics: Any) -> Dict[str, Any]:
    if not isinstance(metrics, dict):
        return {}
    return {k: metrics.get(k) for k in AUDIT_ITEM_METRIC_KEYS}


def list_audit() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in sorted(AUDIT_DIR.glob("audit_gen_*.json")):
        try:
            data = _read_json(path)
        except (json.JSONDecodeError, OSError):
            continue
        items = [
            {
                "spec_id": it.get("spec_id"),
                "result": it.get("result"),
                "metrics": _light_metrics(it.get("metrics")),
            }
            for it in data.get("items", [])
        ]
        out.append({
            "generation": data.get("generation", path.stem.replace("audit_gen_", "")),
            "count": data.get("count"),
            "summary": data.get("summary", {}),
            "items": items,
        })
    return out


def get_audit_item(generation: str, spec_id: str) -> Optional[Dict[str, Any]]:
    """Full audit item (incl. complete metrics + trades) for one spec."""
    path = AUDIT_DIR / f"audit_gen_{generation}.json"
    if not path.exists():
        return None
    try:
        data = _read_json(path)
    except (json.JSONDecodeError, OSError):
        return None
    for it in data.get("items", []):
        if it.get("spec_id") == spec_id:
            return it
    return None


def get_evolution() -> Any:
    if not EVOLUTION_STATE_PATH.exists():
        return {}
    return _read_json(EVOLUTION_STATE_PATH)


def list_specs() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in sorted(STRATEGIES_DIR.glob("spec_*.json")):
        try:
            spec = _read_json(path)
        except (json.JSONDecodeError, OSError):
            continue
        out.append({
            "spec_id": spec.get("spec_id", path.stem.replace("spec_", "")),
            "thesis_id": spec.get("thesis_id"),
            "mechanism_class": spec.get("mechanism_class"),
            "timeframe": spec.get("timeframe"),
            "condition": " | ".join(
                r.get("condition", "") for r in spec.get("entry_rules", [])
            ),
            "source": spec.get("source_file"),
            "mtime_iso": datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).astimezone().isoformat(),
        })
    return out


def get_spec(spec_id: str) -> Optional[Dict[str, Any]]:
    path = STRATEGIES_DIR / f"spec_{spec_id}.json"
    if not path.exists():
        return None
    return _read_json(path)


def spec_paths() -> Dict[str, Path]:
    return {"dir": STRATEGIES_DIR, "archive": STRATEGY_ARCHIVE_DIR}


def get_data_manifest() -> Any:
    return _read_json(DATA_MANIFEST_PATH)


def list_data_lake() -> List[Dict[str, Any]]:
    manifest = _read_json(DATA_MANIFEST_PATH)
    out: List[Dict[str, Any]] = []
    for tf, rel in manifest.get("data_paths", {}).items():
        path = ENGINE_ROOT / rel
        if not path.exists():
            out.append({
                "timeframe": tf, "path": str(path), "rows": 0,
                "start": None, "end": None, "size_mb": 0.0,
            })
            continue
        size_mb = round(path.stat().st_size / (1024 * 1024), 2)
        key = (str(path), path.stat().st_mtime)
        cached = _LAKE_CACHE.get(key)
        if cached:
            n, start, end = cached
        else:
            n, start, end = _parquet_stats(path)
            _LAKE_CACHE[key] = (n, start, end)
        out.append({
            "timeframe": tf,
            "path": str(path),
            "rows": n,
            "start": start,
            "end": end,
            "size_mb": size_mb,
        })
    return out


def _parquet_stats(path: Path):
    """Fast parquet stats: row count from metadata; datetime min/max from a
    lazy column-only scan (no full read). Returns (rows, start_iso, end_iso)."""
    try:
        import pyarrow.parquet as pq
        meta = pq.ParquetFile(path).metadata
        n = meta.num_rows
        if "datetime" not in meta.schema.names:
            return n, None, None
        mins, maxs = [], []
        for batch in pq.ParquetFile(path).iter_batches(
                batch_size=1_000_000, columns=["datetime"]):
            col = batch.column("datetime")
            mins.append(col.min().as_py())
            maxs.append(col.max().as_py())
        mn = min(mins) if mins else None
        mx = max(maxs) if maxs else None
        fmt = lambda v: v.isoformat() if v is not None else None
        return n, fmt(mn), fmt(mx)
    except Exception:
        # fallback: full read (slow) but correct
        import polars as pl
        df = pl.read_parquet(path)
        n = len(df)
        if "datetime" in df.columns:
            s, e = df["datetime"].min(), df["datetime"].max()
            return n, (s.isoformat() if s is not None else None), (e.isoformat() if e is not None else None)
        return n, None, None


# cache for data-lake stats (keyed by path + mtime)
_LAKE_CACHE: Dict[Any, Any] = {}


def get_kb_last_run() -> Optional[Dict[str, Any]]:
    if not KB_LAST_RUN_PATH.exists():
        return None
    try:
        data = _read_json(KB_LAST_RUN_PATH)
    except (json.JSONDecodeError, OSError):
        return None
    # persisted shape: {"stats": {...}, "at": ...} -> contract wants the stats
    return data.get("stats", data)


def get_governance() -> Dict[str, Any]:
    from evolution import governance

    store_path = RESULTS_DIR / "rejected_proposals.jsonl"
    state = get_evolution() or {}
    history = state.get("history") or []
    try:
        gen = max(
            (int(g.get("generation", 0)) for g in history), default=0
        )
    except (TypeError, ValueError):
        gen = 0
    return {
        "protected_paths": list(governance.PROTECTED_PATHS),
        "rejected": governance._read_store(store_path),
        "due_for_review": governance.due_for_review(gen, store_path),
        "current_generation": gen,
    }


def get_events(limit: int = 200) -> Dict[str, Any]:
    path = RESULTS_DIR / "events.jsonl"
    if not path.exists():
        return {"events": []}
    events: List[Dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"events": events[-limit:]}


def append_event(action: str, detail: Dict[str, Any]) -> None:
    """Log a UI mutation to results/events.jsonl (actor='ui')."""
    entry = {
        "actor": "ui",
        "timestamp": datetime.now().astimezone().isoformat(),
        "action": action,
        "detail": detail,
        "git_sha": _git_sha(),
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with (RESULTS_DIR / "events.jsonl").open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
