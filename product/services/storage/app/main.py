"""Storage service (:8083) — FastAPI app for the serve-loop + learn-loop MVP.

Serve-loop (v0.0):
  POST /sessions/turns                 -> validate a C4 turn record, persist, {ok, turn_id}
  GET  /sessions/turns/{turn_id}       -> the stored C4 (404 if absent)
  GET  /sessions/{session_id}/turns    -> C4 turns for a session, ordered by created_at
  GET  /model-directory/resolve        -> C6 body {model_id, adapter, adapter_path}

Learn-loop (capture M0) — the /raw blob leg (C1) + the /context store (C2):
  PUT  /raw/blobs?user_id=&device_id=&chunk_id=&codec=&sha256=&bytes=
       body = raw bytes (application/octet-stream). Verifies sha256+bytes, mints an opaque
       blob_ref, stores the bytes; idempotent on chunk_id. -> {blob_ref, bytes, sha256}
  GET  /raw/blobs?ref=<blob_ref>       -> the raw bytes (ref is a query param, may contain '/';
       404 if unknown OR since-deleted)
  POST /context/records                -> validate a C2 record, idempotent upsert, {ok, record_id}
  GET  /context/records/{record_id}    -> the stored C2 (404 if absent)
  GET  /context/records?user_id=&from=&to=  -> C2 records for a user, ordered by t_start
                                               (window is half-open [from, to); bounds optional)

  GET  /health                         -> {ok: true}
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from . import schemas
from .db import Store
from .models import (
    BlobWriteAck,
    ContextWriteAck,
    Health,
    ProcessedRecord,
    ResolveResponse,
    TurnRecord,
    TurnWriteAck,
)


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

    # --- /raw blob leg (C1) -----------------------------------------------------

    @app.put("/raw/blobs", response_model=BlobWriteAck)
    async def put_raw_blob(
        request: Request,
        user_id: str = Query(..., min_length=1),
        chunk_id: str = Query(..., min_length=1),
        sha256: str = Query(..., min_length=1, description="SHA-256 hex of the blob bytes"),
        device_id: Optional[str] = Query(None),
        codec: Optional[str] = Query(None),
        blob_bytes: Optional[int] = Query(None, alias="bytes", ge=0),
    ) -> BlobWriteAck:
        data = await request.body()
        # End-to-end integrity: the bytes we received must match what recording claims.
        actual_sha = hashlib.sha256(data).hexdigest()
        if actual_sha.lower() != sha256.lower():
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "sha256 mismatch",
                    "declared": sha256,
                    "actual": actual_sha,
                },
            )
        if blob_bytes is not None and blob_bytes != len(data):
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "bytes mismatch",
                    "declared": blob_bytes,
                    "actual": len(data),
                },
            )
        result = store.put_blob(
            chunk_id=chunk_id,
            user_id=user_id,
            device_id=device_id,
            codec=codec,
            sha256=actual_sha,
            data=data,
        )
        return BlobWriteAck(**result)

    @app.get("/raw/blobs")
    def get_raw_blob(ref: str = Query(..., min_length=1)) -> Response:
        # ref is a QUERY param (not a path segment) because a blob_ref may contain '/'.
        data = store.get_blob(ref)
        if data is None:
            raise HTTPException(status_code=404, detail=f"blob ref {ref!r} not found")
        return Response(content=data, media_type="application/octet-stream")

    # --- /context store (C2) ----------------------------------------------------

    @app.post("/context/records", response_model=ContextWriteAck)
    def write_context_record(record: dict[str, Any] = Body(...)) -> ContextWriteAck:
        # 1) Authoritative gate: validate against the frozen C2 JSON Schema.
        problems = schemas.validate_c2(record)
        if problems:
            raise HTTPException(
                status_code=422,
                detail={"error": "C2 schema validation failed", "violations": problems},
            )
        # 2) Mirror check: the pydantic model must agree with the schema.
        ProcessedRecord.model_validate(record)
        # 3) Persist verbatim, time-indexed on (user_id, t_start); idempotent on record_id.
        record_id = store.put_context(record)
        return ContextWriteAck(ok=True, record_id=record_id)

    @app.get("/context/records/{record_id}")
    def read_context_record(record_id: str) -> JSONResponse:
        record = store.get_context(record_id)
        if record is None:
            raise HTTPException(
                status_code=404, detail=f"record_id {record_id!r} not found"
            )
        return JSONResponse(content=record)

    @app.get("/context/records")
    def list_context_records(
        user_id: str = Query(..., min_length=1),
        from_ts: Optional[str] = Query(
            None, alias="from", description="Window start, RFC3339 UTC (inclusive)"
        ),
        to_ts: Optional[str] = Query(
            None, alias="to", description="Window end, RFC3339 UTC (exclusive)"
        ),
    ) -> JSONResponse:
        return JSONResponse(content=store.list_context(user_id, from_ts, to_ts))

    return app


app = create_app()
