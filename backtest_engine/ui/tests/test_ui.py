"""ui/tests/test_ui.py — truth-baseline tests for the control plane.

Rules: UI must match files on disk (no invented data); writes are
fail-closed (invalid specs rejected, HIGH-risk genomes stored not
applied, protected surfaces refused); jobs run the REAL pipeline.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKTEST = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKTEST))

import ui.collect as collect  # noqa: E402
import ui.control as control  # noqa: E402
from ui.server import app  # noqa: E402


@pytest.fixture()
def client():
    return TestClient(app)


# ----------------------------------------------------------------------
# (a) read layer == files on disk (no invented data)
# ----------------------------------------------------------------------
def test_counts_match_disk():
    runs = len([p for p in collect.RAW_RESULTS_DIR.glob("run_*") if p.is_dir()])
    specs = len(list(collect.STRATEGIES_DIR.glob("spec_*.json")))
    audits = len(list(collect.AUDIT_DIR.glob("audit_gen_*.json")))
    h = collect.health()
    assert h["counts"]["runs"] == runs
    assert h["counts"]["specs"] == specs
    assert h["counts"]["audit_gens"] == audits


def test_api_counts_match_disk(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    h = r.json()
    assert h["counts"]["runs"] == collect.health()["counts"]["runs"]
    assert h["counts"]["specs"] == collect.health()["counts"]["specs"]


def test_run_pass_fail_from_audit(client):
    data = client.get("/api/runs").json()
    assert data, "no runs on disk"
    for run in data:
        if run["has_audit"]:
            audit = client.get(f"/api/runs/{run['run_id']}").json()
            # PASS+FAIL+INCONCLUSIVE must reconcile with the audit summary
            audit_file = collect.AUDIT_DIR / f"audit_gen_{run['run_id']}.json"
            summary = json.loads(audit_file.read_text())["summary"]
            assert run["pass"] == summary["PASS"]
            assert run["fail"] == summary["FAIL"]
            assert run["inconclusive"] == summary["INCONCLUSIVE"]


# ----------------------------------------------------------------------
# (b) spec writes are fail-closed
# ----------------------------------------------------------------------
_VALID_SPEC = {
    "thesis_id": "ui_test_thesis",
    "mechanism_class": "trend_momentum",
    "timeframe": "H4",
    "indicators": [{"name": "sma", "lookback": 50, "shift": 1, "params": {}}],
    "entry_rules": [{"direction": "long", "condition": "close > sma(50)"}],
    "exit_rules": [{"exit_type": "tp", "params": {"mode": "percent", "value": 0.015}},
                   {"exit_type": "stop", "params": {"mode": "percent", "value": 0.008}}],
    "risk": {"max_positions": 1, "cooldown_bars": 0, "trailing_stop": False},
    "sizing": {"mode": "fixed_lot", "lots": 0.01},
}


def test_create_valid_spec(client, tmp_path, monkeypatch):
    monkeypatch.setattr(control, "STRATEGIES_DIR", tmp_path)
    monkeypatch.setattr(collect, "STRATEGIES_DIR", tmp_path)
    r = client.post("/api/specs", json={"spec": dict(_VALID_SPEC, spec_id="uitest01")})
    assert r.status_code == 201, r.text
    assert r.json()["spec_id"] == "uitest01"
    assert (tmp_path / "spec_uitest01.json").exists()


def test_create_invalid_condition_rejected(client, tmp_path, monkeypatch):
    monkeypatch.setattr(control, "STRATEGIES_DIR", tmp_path)
    spec = json.loads(json.dumps(_VALID_SPEC))
    spec["entry_rules"] = [{"direction": "long", "condition": "eval('1+1')"}]
    spec["indicators"] = []
    r = client.post("/api/specs", json={"spec": spec})
    assert r.status_code == 400, r.text
    assert "error" in r.json()
    # nothing written
    assert not list(tmp_path.glob("*.json"))


def test_create_unknown_indicator_rejected(client, tmp_path, monkeypatch):
    monkeypatch.setattr(control, "STRATEGIES_DIR", tmp_path)
    spec = json.loads(json.dumps(_VALID_SPEC))
    spec["indicators"] = [{"name": "fakemetric", "lookback": 10, "shift": 1, "params": {}}]
    spec["entry_rules"] = [{"direction": "long", "condition": "fakemetric > 0"}]
    r = client.post("/api/specs", json={"spec": spec})
    assert r.status_code == 400
    assert not list(tmp_path.glob("*.json"))


def test_update_invalid_rejected_file_unchanged(client, tmp_path, monkeypatch):
    monkeypatch.setattr(control, "STRATEGIES_DIR", tmp_path)
    monkeypatch.setattr(collect, "STRATEGIES_DIR", tmp_path)
    control.create_spec(dict(_VALID_SPEC, spec_id="uitest02"))
    before = (tmp_path / "spec_uitest02.json").read_text()
    r = client.patch("/api/specs/uitest02",
                     json={"entry_rules": [{"direction": "long", "condition": "close > not_a_indicator()"}]})
    assert r.status_code == 400
    assert (tmp_path / "spec_uitest02.json").read_text() == before


def test_archive_moves_not_deletes(client, tmp_path, monkeypatch):
    monkeypatch.setattr(control, "STRATEGIES_DIR", tmp_path)
    monkeypatch.setattr(collect, "STRATEGIES_DIR", tmp_path)
    monkeypatch.setattr(control, "ARCHIVE_DIR", tmp_path / "archive")
    control.create_spec(dict(_VALID_SPEC, spec_id="uitest03"))
    r = client.post("/api/specs/uitest03/archive")
    assert r.status_code == 200, r.text
    assert not (tmp_path / "spec_uitest03.json").exists()
    assert (tmp_path / "archive" / "spec_uitest03.json").exists()


# ----------------------------------------------------------------------
# (c) genome control: LOW applied, HIGH stored-not-applied
# ----------------------------------------------------------------------
def _good_genome() -> dict:
    # all within BOUNDS
    return {
        "lookback_min": 8, "lookback_max": 100,
        "tp_min": 0.008, "tp_max": 0.02,
        "sl_min": 0.004, "sl_max": 0.01,
        "tp_delta": 0.002, "sl_delta": 0.001,
        "bbands_dev_min": 1.5, "bbands_dev_max": 2.3,
        "mutation_rate": 0.7, "seed": 42,
        "lookback_deltas": [1, 2, 5],
    }


def test_low_risk_genome_applied(client, tmp_path, monkeypatch):
    state_file = tmp_path / "evolution_state.json"
    state_file.write_text(json.dumps({
        "baseline_genome": dict(control.DEFAULT_GENOME, genome_id="baseline"),
        "best_genome": dict(control.DEFAULT_GENOME, genome_id="baseline"),
        "history": [],
    }))
    monkeypatch.setattr(control, "EVOLUTION_STATE", state_file)
    monkeypatch.setattr(collect, "EVOLUTION_STATE_PATH", state_file)
    r = client.post("/api/controls/genome",
                    json={"genome": _good_genome(), "reason": "test low"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["risk"] == "LOW"
    assert body["applied"] is True
    assert body["stored"] is False
    new_state = json.loads(state_file.read_text())
    assert new_state["baseline_genome"]["lookback_max"] == 100


def test_out_of_bounds_genome_stored_not_applied(client, tmp_path, monkeypatch):
    state_file = tmp_path / "evolution_state.json"
    state_file.write_text(json.dumps({
        "baseline_genome": dict(control.DEFAULT_GENOME, genome_id="baseline"),
        "best_genome": dict(control.DEFAULT_GENOME, genome_id="baseline"),
        "history": [],
    }))
    store = tmp_path / "rejected_proposals.jsonl"
    monkeypatch.setattr(control, "EVOLUTION_STATE", state_file)
    monkeypatch.setattr(collect, "EVOLUTION_STATE_PATH", state_file)
    monkeypatch.setattr(control, "REJECTED_STORE", store)
    monkeypatch.setattr(collect, "RESULTS_DIR", tmp_path)
    bad = _good_genome()
    bad["tp_max"] = 0.9  # way out of BOUNDS (0.01..0.06)
    r = client.post("/api/controls/genome",
                    json={"genome": bad, "reason": "test high"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["risk"] == "HIGH"
    assert body["applied"] is False
    assert body["stored"] is True
    # not applied
    new_state = json.loads(state_file.read_text())
    assert new_state["baseline_genome"]["tp_max"] != 0.9
    # stored in the rejected store
    lines = [json.loads(l) for l in store.read_text().splitlines() if l.strip()]
    assert any(e["detail"]["genome"]["tp_max"] == 0.9 for e in lines)


def test_unknown_genome_field_stored_high(client, tmp_path, monkeypatch):
    state_file = tmp_path / "evolution_state.json"
    state_file.write_text(json.dumps({
        "baseline_genome": dict(control.DEFAULT_GENOME, genome_id="baseline"),
        "best_genome": dict(control.DEFAULT_GENOME, genome_id="baseline"),
        "history": [],
    }))
    store = tmp_path / "rejected_proposals.jsonl"
    monkeypatch.setattr(control, "EVOLUTION_STATE", state_file)
    monkeypatch.setattr(collect, "EVOLUTION_STATE_PATH", state_file)
    monkeypatch.setattr(control, "REJECTED_STORE", store)
    monkeypatch.setattr(collect, "RESULTS_DIR", tmp_path)
    r = client.post("/api/controls/genome",
                    json={"genome": dict(_good_genome(), hacky_field=1),
                          "reason": "bad field"})
    body = r.json()
    assert body["risk"] == "HIGH" and body["applied"] is False


# ----------------------------------------------------------------------
# (d) protected surfaces refused (governance hard gate)
# ----------------------------------------------------------------------
def test_protected_path_refused():
    from evolution import governance
    # a genome-style proposal whose path points at a protected file must be HIGH
    for prot in governance.PROTECTED_PATHS:
        assert governance.is_protected(prot)
        assert governance.classify_risk(
            {"kind": "genome", "path": f"backtest_engine/{prot}"}) == "HIGH"
    assert governance.classify_risk(
        {"kind": "genome", "path": "backtest_engine/data/manifest.json"}) == "HIGH"
    # credentials-style filenames too
    assert governance.is_protected("backtest_engine/.env")
    assert governance.is_protected("secrets/password.txt")


def test_harmlessness_blocks_protected_even_if_low_kind():
    from evolution import governance
    check = governance.harmlessness_check(
        {"kind": "genome", "path": "backtest_engine/audit/gates.py"})
    assert check["ok"] is False


# ----------------------------------------------------------------------
# (e) job lifecycle: real validate-data job
# ----------------------------------------------------------------------
def test_job_lifecycle_validate_data(client):
    r = client.post("/api/jobs", json={"command": "validate-data", "params": {}})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    import time
    j = None
    for _ in range(60):
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "failed", "killed"):
            break
        time.sleep(1)
    assert j is not None and j["status"] == "done", j
    assert j["exit_code"] == 0
    assert "validate-data" in j["log"]
    # persisted to jobs.jsonl
    assert (collect.RESULTS_DIR / "jobs.jsonl").exists()


def test_unknown_command_400(client):
    r = client.post("/api/jobs", json={"command": "nope", "params": {}})
    assert r.status_code == 400


def test_audit_payload_is_lightweight(client):
    """The audit list must NOT ship full trades arrays (was 24MB)."""
    r = client.get("/api/audit")
    assert r.status_code == 200
    body = r.json()
    assert body, "no audit generations"
    for gen in body:
        for it in gen["items"]:
            assert "trades" not in it["metrics"], "trades leaked into list view"
    # detail endpoint returns the full item (incl. trades if present)
    gen, it = body[0], body[0]["items"][0]
    r2 = client.get(f"/api/audit/{gen['generation']}/{it['spec_id']}")
    assert r2.status_code == 200
    assert r2.json()["spec_id"] == it["spec_id"]


def test_kill_unknown_job_404(client):
    r = client.post("/api/jobs/deadbeef/kill")
    assert r.status_code == 404
