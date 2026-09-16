"""ui/control.py — governed write operations for the control plane.

Three surfaces, each reusing the REAL project code (no re-implementation):

  1. Spec CRUD  -> synthesis_layer.strategy_schema.validate_spec (fail-closed
     grammar + schema validation, never eval)
  2. Genome     -> evolution.genome.Genome.validate + evolution.governance
     (LOW risk auto-applies to evolution_state.json; HIGH risk is stored in
     results/rejected_proposals.jsonl and NEVER applied)
  3. Review     -> evolution.governance.mark_reviewed (5-gen re-review cycle)

Every mutation appends an entry to results/events.jsonl (actor='ui').
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import pipeline
import ui.collect as collect
from evolution import governance
from evolution.genome import BOUNDS, DEFAULT_GENOME, Genome, GenomeValidationError
from synthesis_layer.strategy_schema import read_spec, validate_spec

ENGINE_ROOT = Path(pipeline.PROJECT_ROOT) / "backtest_engine"
STRATEGIES_DIR = ENGINE_ROOT / "strategies"
ARCHIVE_DIR = STRATEGIES_DIR / "archive"
RESULTS_DIR = ENGINE_ROOT / "results"
REJECTED_STORE = RESULTS_DIR / "rejected_proposals.jsonl"
EVOLUTION_STATE = ENGINE_ROOT / "evolution" / "evolution_state.json"

_GENOME_GENES = [
    "lookback_min", "lookback_max", "tp_min", "tp_max", "sl_min", "sl_max",
    "tp_delta", "sl_delta", "bbands_dev_min", "bbands_dev_max", "mutation_rate",
]
# extra allowed fields a genome may carry beyond the 11 numeric genes
_EXTRA_GENOME_KEYS = {"genome_id", "lookback_deltas", "seed"}
_TIMEFRAMES = {"M1", "M15", "H1", "H4", "D1"}


def _event(action: str, detail: Dict[str, Any]) -> None:
    collect.append_event(action, detail)


# ----------------------------------------------------------------------
# 1. Spec CRUD
# ----------------------------------------------------------------------
def create_spec(spec: Dict[str, Any]) -> str:
    """Validate + write a new strategy spec. Returns the spec_id.

    spec_id (if provided) must be a fresh, non-colliding id; otherwise a
    deterministic id is derived from the content.
    """
    if not isinstance(spec, dict):
        raise ValueError("spec must be a dict")
    raw = copy.deepcopy(spec)
    # fail-closed: the real schema+grammar validator raises on anything bad
    validated = validate_spec(raw)
    spec_id = raw.get("spec_id") or validated.spec_id
    if not re.fullmatch(r"[a-z0-9]{4,24}", str(spec_id)):
        raise ValueError(
            f"spec_id {spec_id!r} invalid (must be 4-24 lowercase alphanumerics)")
    out = STRATEGIES_DIR / f"spec_{spec_id}.json"
    if out.exists():
        raise ValueError(f"spec already exists: {spec_id}")
    raw["spec_id"] = spec_id
    # normalize through the schema so the on-disk file is canonical
    canonical = validated.to_dict()
    canonical["spec_id"] = spec_id
    if raw.get("source_file") is not None:
        canonical["source_file"] = raw["source_file"]
    out.write_text(json.dumps(canonical, indent=2, default=str))
    _event("spec_create", {"spec_id": spec_id})
    return spec_id


def _load_spec_raw(spec_id: str) -> Dict[str, Any]:
    path = STRATEGIES_DIR / f"spec_{spec_id}.json"
    if not path.exists():
        raise LookupError(f"spec not found: {spec_id}")
    return read_spec(path)


def update_spec(spec_id: str, partial: Dict[str, Any]) -> Dict[str, Any]:
    """Merge partial changes into an existing spec, re-validate fail-closed."""
    if not isinstance(partial, dict) or not partial:
        raise ValueError("body must be a non-empty dict of changed fields")
    raw = _load_spec_raw(spec_id)
    merged = copy.deepcopy(raw)
    merged.update(copy.deepcopy(partial))
    # spec_id is immutable via update
    if merged.get("spec_id") != spec_id:
        raise ValueError("spec_id is immutable (use archive + create)")
    validate_spec(merged)  # fail-closed full re-validation
    path = STRATEGIES_DIR / f"spec_{spec_id}.json"
    path.write_text(json.dumps(merged, indent=2, default=str))
    _event("spec_update", {"spec_id": spec_id, "fields": sorted(partial.keys())})
    return merged


def archive_spec(spec_id: str) -> Dict[str, Any]:
    """Move a spec to strategies/archive (NEVER deletes)."""
    path = STRATEGIES_DIR / f"spec_{spec_id}.json"
    if not path.exists():
        raise LookupError(f"spec not found: {spec_id}")
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dest = ARCHIVE_DIR / path.name
    shutil.move(str(path), str(dest))
    _event("spec_archive", {"spec_id": spec_id, "archived_to": str(dest)})
    return {"archived_to": str(dest)}


# ----------------------------------------------------------------------
# 2. Genome control (governance-gated)
# ----------------------------------------------------------------------
def apply_genome(genome: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """Apply a genome edit. LOW risk -> auto-applied to evolution_state.json.
    HIGH risk (out of bounds / protected surface / bad shape) -> stored in the
    rejected-proposal store and NOT applied. Fail-closed throughout."""
    if not isinstance(genome, dict) or not genome:
        raise ValueError("genome must be a non-empty dict")

    # unknown fields -> HIGH (fail-closed: don't silently drop/accept)
    unknown = set(genome.keys()) - _EXTRA_GENOME_KEYS - set(_GENOME_GENES)
    # build the full candidate genome (merge over the current baseline)
    current = collect.get_evolution() or {}
    base = dict(current.get("baseline_genome") or DEFAULT_GENOME)
    candidate = copy.deepcopy(base)
    candidate.update(copy.deepcopy(genome))
    candidate["genome_id"] = "ui_edit"

    validation_error = None
    try:
        Genome.from_dict(candidate).validate()
    except GenomeValidationError as exc:
        validation_error = str(exc)

    proposal = {
        "kind": "genome",
        "path": "backtest_engine/evolution/evolution_state.json",
        "id": f"ui_{collect._git_sha()[:6]}_{int(time.time())}",
        "description": reason or "UI genome edit",
        "detail": {
            "genome": candidate,
            "unknown_fields": sorted(unknown),
            "validation_error": validation_error,
        },
    }

    if unknown:
        return _store_high(proposal,
                           f"unknown genome fields: {sorted(unknown)}", reason)
    if validation_error is not None:
        return _store_high(proposal, validation_error, reason)

    # bounds are OK — now apply the governance risk classification
    risk = governance.classify_risk(proposal)
    harmless = governance.harmlessness_check(proposal)
    if risk == "HIGH" or not harmless["ok"]:
        return _store_high(proposal,
                           harmless["reason"] or "classified HIGH by governance",
                           reason)

    # LOW risk + harmless -> auto-apply to the evolution state
    state = collect.get_evolution() or {}
    candidate["genome_id"] = f"ui_edit_{collect._git_sha()[:6]}"
    state["baseline_genome"] = candidate
    state["best_genome"] = candidate
    state["updated_at"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    EVOLUTION_STATE.parent.mkdir(parents=True, exist_ok=True)
    EVOLUTION_STATE.write_text(json.dumps(state, indent=2, default=str))
    _event("genome_apply", {
        "risk": "LOW", "applied": True,
        "genome_id": candidate["genome_id"], "reason": reason,
    })
    return {"risk": "LOW", "applied": True, "stored": False,
            "reason": "LOW-risk genome edit auto-applied to evolution state",
            "genome_id": candidate["genome_id"]}


def _store_high(proposal: Dict[str, Any], reason: str, user_reason: str) -> Dict[str, Any]:
    generation = collect.get_governance().get("current_generation", 0)
    entry = governance.record_rejected(proposal, reason, generation, REJECTED_STORE)
    _event("genome_stored_high", {
        "id": entry.get("id"), "reason": reason, "user_reason": user_reason,
    })
    return {
        "risk": "HIGH",
        "applied": False,
        "stored": True,
        "reason": reason,
        "stored_id": entry.get("id"),
        "next_review_gen": entry.get("next_review_gen"),
        "note": "HIGH-risk genome edit is NOT applied; stored in rejected "
                "proposals for the 5-generation re-review cycle.",
    }


# ----------------------------------------------------------------------
# 3. Governance review board
# ----------------------------------------------------------------------
_VALID_DISPOSITIONS = {"approve", "reject", "hold"}


def review_proposals(entries: Any, disposition: str) -> Dict[str, Any]:
    """Disposition pending-rejected proposals.

    approve -> accepted_manually (out of the re-review cycle)
    reject/hold -> stays in the cycle (next review advanced)
    """
    if disposition not in _VALID_DISPOSITIONS:
        raise ValueError(
            f"disposition must be one of {sorted(_VALID_DISPOSITIONS)}")
    if not isinstance(entries, list) or not entries:
        raise ValueError("entries must be a non-empty list")

    store = governance._read_store(REJECTED_STORE)
    by_id = {e.get("id"): e for e in store}
    # entries may be ids (str) or full dicts
    ids = [e.get("id") if isinstance(e, dict) else e for e in entries]
    matched = [by_id[i] for i in ids if i in by_id]
    missing = [i for i in ids if i not in by_id]
    if not matched:
        raise ValueError(f"no matching proposals (missing: {missing})")

    gen = collect.get_governance().get("current_generation", 0)
    mapped = "accepted_manually" if disposition == "approve" else "rejected"
    governance.mark_reviewed(matched, gen, REJECTED_STORE, disposition=mapped)
    _event("governance_review", {
        "ids": ids, "disposition": disposition, "missing": missing,
    })
    return {
        "reviewed": len(matched),
        "disposition": disposition,
        "missing": missing,
        "note": ("approved (accepted_manually, out of cycle)"
                 if disposition == "approve"
                 else "remains in the re-review cycle"),
    }
