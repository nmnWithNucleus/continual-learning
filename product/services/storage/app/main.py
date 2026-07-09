"""Storage service (:8083) — FastAPI app for the serve-loop MVP.

Endpoints:
  POST /sessions/turns                 -> validate a C4 turn record, persist, {ok, turn_id}
  GET  /sessions/turns/{turn_id}       -> the stored C4 (404 if absent)
  GET  /sessions/{session_id}/turns    -> C4 turns for a session, ordered by created_at
  GET  /model-directory/resolve        -> C6 body {model_id, adapter, adapter_path}
  GET  /health                         -> {ok: true}

Scope note (v0.0): this is `/sessions` + the model directory ONLY. `/context` and `/raw`
are later slices and deliberately absent here.
"""
from __future__ import annotations

from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from . import schemas
from .db import Store
from .models import Health, ResolveResponse, TurnRecord, TurnWriteAck


def create_app() -> FastAPI:
    """App factory. Reads STORAGE_DB_PATH at call time, so tests can point at a temp DB."""
    app = FastAPI(
        title="Nucleus storage service",
        version="0.0",
        summary="Durable /sessions + model directory for the serve-loop MVP.",
    )
    store = Store()
    store.seed_base()
    app.state.store = store

    @app.get("/health", response_model=Health)
    def health() -> Health:
        return Health(ok=True)

    @app.post("/sessions/turns", response_model=TurnWriteAck)
    def write_turn(record: dict[str, Any] = Body(...)) -> TurnWriteAck:
        # 1) Authoritative gate: validate against the frozen C4 JSON Schema.
        problems = schemas.validate_c4(record)
        if problems:
            raise HTTPException(
                status_code=422,
                detail={"error": "C4 schema validation failed", "violations": problems},
            )
        # 2) Mirror check: the pydantic model must agree with the schema.
        TurnRecord.model_validate(record)
        # 3) Persist the record verbatim (idempotent on turn_id).
        turn_id = store.put_turn(record)
        return TurnWriteAck(ok=True, turn_id=turn_id)

    @app.get("/sessions/turns/{turn_id}")
    def read_turn(turn_id: str) -> JSONResponse:
        record = store.get_turn(turn_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"turn_id {turn_id!r} not found")
        return JSONResponse(content=record)

    @app.get("/sessions/{session_id}/turns")
    def list_session_turns(session_id: str) -> JSONResponse:
        return JSONResponse(content=store.list_turns(session_id))

    @app.get("/model-directory/resolve", response_model=ResolveResponse)
    def resolve(user_id: str = Query(..., min_length=1)) -> ResolveResponse:
        body = store.resolve(user_id)
        # Contract-check our own output against the frozen C6 JSON Schema before serving.
        problems = schemas.validate_c6(body)
        if problems:  # pragma: no cover - would indicate a directory-data bug
            raise HTTPException(
                status_code=500,
                detail={"error": "C6 schema validation failed", "violations": problems},
            )
        return ResolveResponse(**body)

    return app


app = create_app()
