"""ui/server.py — FastAPI control-plane server.

Read endpoints over ui/collect.py; write endpoints (jobs, spec CRUD,
genome, governance) are wired in ui/jobs.py + ui/control.py and mounted
here via include_router-style functions.

Entry: pipeline.py serve --port 8050 (uvicorn runs ui.server:app).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

import ui.collect as collect

UI_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Quant Agent Control Plane", version="1.0")


@app.exception_handler(StarletteHTTPException)
async def _http_handler(request: Request, exc: StarletteHTTPException):
    """Contract: errors come back as {"error": "..."}."""
    return JSONResponse(status_code=exc.status_code,
                        content={"error": str(exc.detail)})


@app.exception_handler(Exception)
async def _error_handler(request: Request, exc: Exception):
    """Contract: errors come back as {"error": "..."} (not FastAPI's
    {"detail": ...})."""
    return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}: {exc}"})


# ----------------------------------------------------------------------
# Health / observability (read-only)
# ----------------------------------------------------------------------
@app.get("/api/health")
def api_health():
    return collect.health()


@app.get("/api/runs")
def api_runs():
    return collect.list_runs()


@app.get("/api/runs/{run_id}")
def api_run_detail(run_id: str):
    data = collect.get_run(run_id)
    if data is None:
        raise HTTPException(status_code=404, detail="run not found")
    return data


@app.get("/api/audit")
def api_audit():
    return collect.list_audit()


@app.get("/api/audit/{generation}/{spec_id}")
def api_audit_item(generation: str, spec_id: str):
    """Full audit item (complete metrics + trades) for one spec."""
    data = collect.get_audit_item(generation, spec_id)
    if data is None:
        raise HTTPException(status_code=404, detail="audit item not found")
    return data


@app.get("/api/evolution")
def api_evolution():
    return collect.get_evolution()


@app.get("/api/specs")
def api_specs():
    return collect.list_specs()


@app.get("/api/specs/{spec_id}")
def api_spec_detail(spec_id: str):
    spec = collect.get_spec(spec_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="spec not found")
    return spec


@app.get("/api/manifest")
def api_manifest():
    return collect.get_data_manifest()


@app.get("/api/data-lake")
def api_data_lake():
    return collect.list_data_lake()


@app.get("/api/kb")
def api_kb():
    return {"last_run": collect.get_kb_last_run()}


@app.get("/api/governance")
def api_governance():
    return collect.get_governance()


@app.get("/api/events")
def api_events():
    return collect.get_events(200)


# ----------------------------------------------------------------------
# Jobs (control plane)
# ----------------------------------------------------------------------
@app.get("/api/jobs")
def api_jobs():
    from ui import jobs
    return jobs.list_jobs()


@app.get("/api/jobs/{job_id}")
def api_job_detail(job_id: str):
    from ui import jobs
    data = jobs.get_job(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="job not found")
    return data


@app.post("/api/jobs")
async def api_job_start(request: Request):
    from ui import jobs
    body = await request.json()
    command = (body.get("command") or "").strip()
    if not jobs.is_known_command(command):
        raise HTTPException(
            status_code=400,
            detail=f"unknown command: {command!r}. "
                   f"known: {sorted(jobs.KNOWN_COMMANDS)}",
        )
    job_id = jobs.start(command, body.get("params") or {})
    return JSONResponse(status_code=202, content={"job_id": job_id})


@app.post("/api/jobs/{job_id}/kill")
def api_job_kill(job_id: str):
    from ui import jobs
    result = jobs.kill(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="job not found")
    return result


# ----------------------------------------------------------------------
# Spec CRUD (governed writes)
# ----------------------------------------------------------------------
@app.post("/api/specs")
async def api_spec_create(request: Request):
    from ui import control
    body = await request.json()
    spec = body.get("spec")
    if not isinstance(spec, dict):
        raise HTTPException(status_code=400, detail="body must be {spec: {...}}")
    try:
        spec_id = control.create_spec(spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # fail-closed: any schema/grammar rejection
        raise HTTPException(status_code=400, detail=f"invalid spec: {exc}")
    return JSONResponse(status_code=201, content={"spec_id": spec_id})


@app.patch("/api/specs/{spec_id}")
async def api_spec_update(request: Request, spec_id: str):
    from ui import control
    body = await request.json()
    try:
        return control.update_spec(spec_id, body)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # fail-closed re-validation rejection
        raise HTTPException(status_code=400, detail=f"invalid spec: {exc}")


@app.post("/api/specs/{spec_id}/archive")
def api_spec_archive(spec_id: str):
    from ui import control
    try:
        return control.archive_spec(spec_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ----------------------------------------------------------------------
# Genome control + governance review (governed writes)
# ----------------------------------------------------------------------
@app.post("/api/controls/genome")
async def api_genome(request: Request):
    from ui import control
    body = await request.json()
    try:
        return control.apply_genome(body.get("genome") or {},
                                    body.get("reason") or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/governance/review")
async def api_governance_review(request: Request):
    from ui import control
    body = await request.json()
    try:
        return control.review_proposals(
            body.get("entries") or [], body.get("disposition") or "reject")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ----------------------------------------------------------------------
# Static UI
# ----------------------------------------------------------------------
@app.get("/")
def ui_index():
    index = UI_DIR / "index.html"
    if not index.exists():
        return JSONResponse(
            status_code=404,
            content={"error": "ui/index.html not found (frontend pending)"},
        )
    return FileResponse(index)


def _static_file(name: str):
    def handler():
        p = UI_DIR / name
        if not p.exists():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(p)
    return handler


for _name in ("app.js", "app2.js", "style.css"):
    app.add_api_route(f"/{_name}", _static_file(_name), methods=["GET"])
