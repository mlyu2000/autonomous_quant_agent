"""ui/jobs.py — thread-based job queue for pipeline commands.

Runs the REAL pipeline.py functions in worker threads, captures logs,
tracks status, supports cooperative kill, and persists job records to
results/jobs.jsonl (one JSON object per line, appended on start and on
finish) so job history survives server restarts.

Kill semantics (MVP-honest): kill() sets a flag; the job is marked
'killed' when its command returns (cooperative, not mid-command
preemption). This is stated in the job record (kill_mode field).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pipeline  # the real pipeline (backtest_engine is on sys.path)

RESULTS_DIR = Path(pipeline.PROJECT_ROOT) / "backtest_engine" / "results"
JOBS_PATH = RESULTS_DIR / "jobs.jsonl"

KNOWN_COMMANDS = {
    "validate-data", "generate", "generate-kb",
    "backtest", "audit-gen", "evolve",
}

_lock = threading.Lock()
_jobs: Dict[str, Dict[str, Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_record(record: Dict[str, Any]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with JOBS_PATH.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _load_history() -> None:
    """Merge persisted job records into in-memory state (idempotent)."""
    if not JOBS_PATH.exists():
        return
    for line in JOBS_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        jid = rec.get("job_id")
        with _lock:
            if jid and jid not in _jobs:
                _jobs[jid] = rec
            elif jid and jid in _jobs:
                # later record wins (finish over start)
                if rec.get("status") in ("done", "failed", "killed"):
                    _jobs[jid] = rec


_load_history()


def is_known_command(command: str) -> bool:
    return command in KNOWN_COMMANDS


def _persist_kb_stats(output: str) -> None:
    """Extract the bridge's 'bridge stats: {...}' line and persist it to
    results/kb_last_run.json for the /api/kb view."""
    for line in output.splitlines():
        if "bridge stats:" in line:
            payload = line.split("bridge stats:", 1)[1].strip()
            try:
                stats = json.loads(payload)
            except json.JSONDecodeError:
                return
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            (RESULTS_DIR / "kb_last_run.json").write_text(
                json.dumps({"stats": stats, "at": _now()}, indent=2))
            return


def _dispatch(command: str, params: Dict[str, Any]) -> int:
    """Call the real pipeline.py functions directly (no argv round-trip)."""
    if command == "validate-data":
        return pipeline.validate_data()
    if command == "generate":
        return pipeline.generate(argparse.Namespace(
            count=int(params.get("count", 8)),
            use_genome=bool(params.get("use_genome", False)),
        ))
    if command == "generate-kb":
        return pipeline.generate_kb(argparse.Namespace(
            count=int(params.get("count", 8)),
            prefer=str(params.get(
                "prefer",
                "Gold (XAUUSD),Forex,Commodities,Commodities (Gold)")),
        ))
    if command == "evolve":
        return pipeline.evolve(argparse.Namespace(
            generations=int(params.get("generations", 1)),
            pop_size=int(params.get("pop_size", 4)),
            specs_per_genome=int(params.get("specs_per_genome", 2)),
            seed=params.get("seed"),
        ))
    if command == "backtest":
        return pipeline.backtest(argparse.Namespace(
            spec_dir=str(params.get(
                "spec_dir",
                str(Path(pipeline.PROJECT_ROOT) / "backtest_engine" / "strategies"))),
            out=str(params.get(
                "out",
                str(Path(pipeline.PROJECT_ROOT) / "backtest_engine" / "results" / "raw"))),
        ))
    if command == "audit-gen":
        # pipeline.audit_gen always audits the LATEST run dir; the
        # optional 'generation' is just the audit label.
        return pipeline.audit_gen(
            argparse.Namespace(generation=params.get("generation")))
    raise ValueError(f"unknown command: {command!r}")


def _run_job(job_id: str, command: str, params: Dict[str, Any]) -> None:
    with _lock:
        job = _jobs[job_id]
        job["status"] = "running"
        job["started_at"] = _now()
        _append_record(dict(job))

    buf_out, buf_err = io.StringIO(), io.StringIO()
    exit_code: int
    try:
        with contextlib.redirect_stdout(buf_out), \
                contextlib.redirect_stderr(buf_err):
            exit_code = _dispatch(command, params)
    except Exception as exc:  # noqa: BLE001 — record, don't crash the server
        exit_code = -1
        buf_err.write(f"job exception: {type(exc).__name__}: {exc}\n")

    # Persist KB mapping stats for the /api/kb view (from the bridge's
    # "bridge stats:" log line).
    if command == "generate-kb":
        _persist_kb_stats(buf_out.getvalue())

    with _lock:
        job = _jobs[job_id]
        job["finished_at"] = _now()
        job["exit_code"] = exit_code
        job["log"] = buf_out.getvalue()
        job["stderr"] = buf_err.getvalue()
        job["log_tail"] = buf_out.getvalue()[-4000:]
        if job.get("kill_requested"):
            job["status"] = "killed"
        elif exit_code == 0:
            job["status"] = "done"
        else:
            job["status"] = "failed"
        _append_record(dict(job))


def start(command: str, params: Dict[str, Any]) -> str:
    job_id = uuid.uuid4().hex[:8]
    record = {
        "job_id": job_id,
        "command": command,
        "params": params,
        "status": "pending",
        "started_at": None,
        "finished_at": None,
        "exit_code": None,
        "log_tail": "",
        "kill_mode": "cooperative (flag set; status flips when command returns)",
        "kill_requested": False,
    }
    with _lock:
        _jobs[job_id] = record
    _append_record(dict(record))
    t = threading.Thread(
        target=_run_job, args=(job_id, command, params), daemon=True,
        name=f"job-{job_id}",
    )
    t.start()
    return job_id


def kill(job_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        job["kill_requested"] = True
        if job["status"] not in ("running", "pending"):
            return {"killed": False, "reason": f"job is {job['status']}"}
        _append_record(dict(job))
    return {"killed": True, "note": "kill requested; status becomes 'killed' when the command returns"}


def list_jobs() -> List[Dict[str, Any]]:
    with _lock:
        recs = [dict(j) for j in _jobs.values()]
    for r in recs:  # keep payloads small for the list view
        r.pop("log", None)
        r.pop("stderr", None)
    recs.sort(key=lambda r: (r.get("started_at") or "", r.get("job_id")), reverse=True)
    return recs


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None
