"""Governance for the self-evolution loop.

Two hard rules from the project's money-risk discipline:

  1. LOW-risk improvements (genome / search-distribution parameter changes,
     strictly within declared bounds, only touching the evolution/ surface) are
     auto-applied and committed.

  2. HIGH-risk changes — ANYTHING touching the skeptical gates, the frozen data
     manifest, the shared grammar, the compiler/runner/schema, or
     credentials — are NEVER auto-applied. They are stored in
     results/rejected_proposals.jsonl and re-reviewed every 5 generations.

The harmlessness gate is the last line of defence: even a LOW-classified
proposal that somehow touches a protected path is rejected. This is a hard
boundary — the EA can evolve its search distribution, it cannot rewrite the
skeptical auditor.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class Harmfulness(Exception):
    pass


# Protected surfaces (harmlessness gate). A proposal touching any of these is
# ALWAYS HIGH-risk and never auto-applied. Matched as path substrings (repo
# relative) or filename patterns.
PROTECTED_PATHS = (
    "audit/gates.py",          # the skeptical gates — never auto-modified
    "audit/writer.py",
    "data/manifest.json",      # frozen data contract
    "synthesis_layer/grammar.py",  # shared condition grammar (single source of truth)
    "synthesis_layer/strategy_schema.py",
    "compiler/dynamic_loader.py",
    "engine/runner.py",
    "engine/custom_analyzers.py",
)

PROTECTED_FILENAME_RE = re.compile(
    r"(\.env$|\.env\..*|rubric\.ya?ml$|credential|secret|\.pem$|\.key$|token|password)",
    re.IGNORECASE,
)

# LOW-risk proposal kinds: parameter-level changes to the evolution surface only.
LOW_RISK_KINDS = {
    "genome_gene",        # a single genome gene value (within BOUNDS)
    "genome",             # a whole-genome replacement (every gene within BOUNDS)
    "search_range",       # a declared search-range tuning (within BOUNDS)
    "mutation_weight",    # a mutation-operator weight (within BOUNDS)
    "selection_pressure", # EA selection strength (within [0,1])
}

# The only directory a LOW-risk proposal may touch.
LOW_RISK_ROOT = "backtest_engine/evolution/"

# Re-review cadence for rejected HIGH-risk proposals (generations).
REVIEW_EVERY_N_GENS = 5


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_protected(path: str) -> bool:
    p = path.replace("\\", "/")
    if any(prot in p for prot in PROTECTED_PATHS):
        return True
    if PROTECTED_FILENAME_RE.search(p):
        return True
    return False


def classify_risk(proposal: Dict[str, Any]) -> str:
    """Return "LOW" or "HIGH" for a proposal.

    LOW requires ALL of:
      - kind in LOW_RISK_KINDS
      - path (if any) within the evolution/ surface
      - not touching a protected path
    Otherwise HIGH (fail-closed: default is HIGH).
    """
    kind = proposal.get("kind", "")
    if kind not in LOW_RISK_KINDS:
        return "HIGH"
    path = proposal.get("path", "")
    if path:
        p = path.replace("\\", "/")
        if is_protected(p):
            return "HIGH"
        if not p.startswith(LOW_RISK_ROOT):
            return "HIGH"
    return "LOW"


def harmlessness_check(proposal: Dict[str, Any]) -> Dict[str, Any]:
    """HARD gate: no proposal may touch protected surfaces, regardless of
    declared risk. Returns {"ok": bool, "reason": str}."""
    path = proposal.get("path", "")
    if path and is_protected(path):
        return {"ok": False, "reason": f"touches protected surface: {path}"}
    # Even without a path, a proposal whose kind is a code edit is disallowed
    # from auto-apply by classify_risk; here we double-check the kind.
    if proposal.get("kind") not in LOW_RISK_KINDS and proposal.get("auto_apply"):
        return {"ok": False, "reason": f"kind '{proposal.get('kind')}' is not auto-applicable"}
    return {"ok": True, "reason": "no protected surface touched"}


def record_rejected(proposal: Dict[str, Any], reason: str, generation: int,
                    store_path: Path) -> Dict[str, Any]:
    """Append a rejected HIGH-risk proposal to the JSONL store.

    The proposal is kept (never discarded) and re-reviewed every
    REVIEW_EVERY_N_GENS generations.
    """
    entry = {
        "id": proposal.get("id") or f"rej_{generation}_{len(_read_store(store_path))}",
        "generated_at": _now(),
        "generation": int(generation),
        "kind": proposal.get("kind", "unknown"),
        "path": proposal.get("path", ""),
        "description": proposal.get("description", ""),
        "detail": proposal.get("detail", {}),
        "risk": "HIGH",
        "reason_rejected": reason,
        "next_review_gen": int(generation) + REVIEW_EVERY_N_GENS,
        "status": "pending_review",
    }
    store_path = Path(store_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    with store_path.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    return entry


def _read_store(store_path: Path) -> List[Dict[str, Any]]:
    store_path = Path(store_path)
    if not store_path.exists():
        return []
    out = []
    for line in store_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def due_for_review(generation: int, store_path: Path) -> List[Dict[str, Any]]:
    """Proposals whose next_review_gen <= current generation (re-review cadence)."""
    return [e for e in _read_store(store_path)
            if e.get("status") == "pending_review"
            and int(e.get("next_review_gen", 0)) <= int(generation)]


def mark_reviewed(entries: List[Dict[str, Any]], generation: int, store_path: Path,
                  disposition: str = "rejected") -> None:
    """After re-review, push the next review out by REVIEW_EVERY_N_GENS.

    The default disposition keeps the proposal in the re-review cycle
    (status stays "pending_review", next_review_gen advances) — a rejected
    HIGH-risk proposal is RE-reviewed every cadence, never silently dropped.
    disposition="accepted_manually" records that a HUMAN explicitly approved
    it (the EA itself never does this), which also takes it out of the cycle.
    """
    by_id = {e.get("id"): e for e in _read_store(store_path)}
    for entry in entries:
        if entry.get("id") in by_id:
            by_id[entry["id"]]["next_review_gen"] = int(generation) + REVIEW_EVERY_N_GENS
            by_id[entry["id"]]["last_review_gen"] = int(generation)
            if disposition == "accepted_manually":
                by_id[entry["id"]]["status"] = "accepted_manually"
            else:
                # stays in the re-review cycle (status unchanged = pending_review)
                by_id[entry["id"]]["status"] = "pending_review"
    if by_id:
        store_path = Path(store_path)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with store_path.open("w") as f:
            for e in by_id.values():
                f.write(json.dumps(e, default=str) + "\n")
