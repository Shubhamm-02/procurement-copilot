"""FastAPI backend: JSON API for the copilot + serves the static product UI.

Endpoints
    GET  /api/health         - liveness
    GET  /api/meta           - active LLM provider, reference date, tool catalog
    GET  /api/requests       - list of example requests (for the UI picker)
    GET  /api/requests/{id}  - one request's raw detail
    POST /api/decide         - run a request through architecture A or B
    GET  /                   - the product UI (static index.html)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config, data_access as da, llm  # noqa: E402
from src.solution import handle_request  # noqa: E402
from src.tools import REGISTRY  # noqa: E402

app = FastAPI(title="Procurement Request Copilot", version="1.0")

STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.middleware("http")
async def no_cache(request, call_next):
    """Local dev tool: never let the browser cache assets/responses (avoids the
    classic 'stale JS/CSS' confusion between code edits)."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


class DecideRequest(BaseModel):
    request_id: str
    architecture: str = "single"


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/meta")
def meta() -> dict:
    return {
        "llm_provider": llm.provider_label(),
        "llm_available": llm.is_available(),
        "reference_date": config.REFERENCE_DATE.isoformat(),
        "architectures": ["single", "staged"],
        "tools": [
            {"name": t.name, "kind": t.kind, "description": t.description}
            for t in REGISTRY.values()
        ],
    }


@app.get("/api/requests")
def list_requests() -> list[dict]:
    employees = {e["employee_id"]: e for e in da.load_employees().to_dict("records")}
    out = []
    for r in da.load_requests():
        emp = employees.get(r.get("requester_id"), {})
        out.append({
            "request_id": r["request_id"],
            "product_name": r.get("product_name"),
            "vendor_name": r.get("vendor_name"),
            "category": r.get("category"),
            "annual_cost_usd": r.get("annual_cost_usd"),
            "requester": emp.get("name"),
            "department": emp.get("department"),
            "urgency": r.get("urgency"),
        })
    return out


@app.get("/api/requests/{request_id}")
def get_request(request_id: str) -> dict:
    try:
        return da.get_request(request_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown request_id: {request_id}")


@app.post("/api/decide")
def decide(body: DecideRequest) -> dict:
    if body.architecture not in ("single", "staged"):
        raise HTTPException(status_code=400, detail="architecture must be 'single' or 'staged'")
    try:
        start = time.perf_counter()
        decision = handle_request(body.request_id, architecture=body.architecture)
        latency_ms = round((time.perf_counter() - start) * 1000, 1)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown request_id: {body.request_id}")
    payload = decision.model_dump()
    payload["latency_ms"] = latency_ms
    payload["architecture"] = body.architecture
    return payload


# --- static UI (mounted last so /api/* wins) --------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
