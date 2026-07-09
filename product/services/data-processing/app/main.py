"""data-processing service HTTP surface (FastAPI, :8085).

POST /ingest  — body = a pushed C1 raw-stream envelope. Validate C1 -> dedup on
                chunk_id -> pull the blob by ref from storage -> run ASR ->
                build a C2 record -> POST it to storage /context -> return
                {ok, record_id}. This is the C1 push receiver (learn-loop M0).
GET  /health  — liveness + effective ASR backend.

The whole loop runs headless on any box: ASR_BACKEND defaults to `mock` (no GPU).
"""
from __future__ import annotations

import hashlib
import logging

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import asr, schemas
from .config import get_settings
from .dedup import DedupStore
from .models import C1Envelope, C2Record
from .pipeline import build_c2, chunk_span_seconds
from .storage_client import StorageClient
from .timeutil import now_iso

logger = logging.getLogger("data-processing")


def create_app() -> FastAPI:
    """App factory. Reads env at call time so tests can point STORAGE_URL / flip
    ASR_BACKEND before construction and inject a mock storage transport after."""
    app = FastAPI(
        title="Nucleus data-processing service",
        version="0.0",
        summary="C1 -> ASR -> C2 capture skeleton for the learn loop.",
    )
    settings = get_settings()
    app.state.storage = StorageClient(settings.storage_url, timeout=settings.http_timeout)
    app.state.dedup = DedupStore()

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "asr_backend": get_settings().asr_backend}

    @app.post("/ingest")
    async def ingest(request: Request) -> JSONResponse:
        settings = get_settings()

        # ---- Parse + validate the incoming C1 envelope -----------------------
        try:
            c1 = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"error": "body is not valid JSON"})

        # 1) Authoritative gate: validate against the frozen C1 JSON Schema.
        problems = schemas.validate_c1(c1)
        if problems:
            raise HTTPException(
                status_code=422,
                detail={"error": "C1 schema validation failed", "violations": problems},
            )
        # 2) Mirror check: the pydantic model must agree with the schema.
        C1Envelope.model_validate(c1)

        chunk_id = c1["chunk_id"]
        dedup: DedupStore = request.app.state.dedup

        # ---- Dedup (fast path): already-processed chunk_id -> prior record_id -
        prior = dedup.get(chunk_id)
        if prior is not None:
            logger.info("dedup hit (processed) chunk_id=%s -> %s", chunk_id, prior)
            return JSONResponse(content={"ok": True, "record_id": prior})

        # Serialize concurrent redeliveries of the same in-flight chunk_id.
        lock = await dedup.lock_for(chunk_id)
        async with lock:
            prior = dedup.get(chunk_id)
            if prior is not None:  # resolved while we waited on the lock (in-flight)
                logger.info("dedup hit (in-flight) chunk_id=%s -> %s", chunk_id, prior)
                return JSONResponse(content={"ok": True, "record_id": prior})

            storage: StorageClient = request.app.state.storage
            backend = asr.select(settings)

            # ---- Pull the raw chunk bytes by blob_ref ------------------------
            try:
                audio_bytes = await storage.get_blob(c1["blob_ref"])
            except httpx.HTTPStatusError as exc:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": "blob fetch failed",
                        "status": exc.response.status_code,
                        "blob_ref": c1["blob_ref"],
                    },
                )
            except httpx.HTTPError as exc:  # connect/timeout/etc.
                raise HTTPException(
                    status_code=502,
                    detail={"error": f"blob fetch error: {exc}", "blob_ref": c1["blob_ref"]},
                )

            # End-to-end integrity check against /raw (C1 carries the sha256).
            if settings.verify_blob_sha256:
                actual = hashlib.sha256(audio_bytes).hexdigest()
                if actual != c1["blob_sha256"]:
                    raise HTTPException(
                        status_code=502,
                        detail={
                            "error": "blob sha256 mismatch",
                            "expected": c1["blob_sha256"],
                            "actual": actual,
                        },
                    )

            # ---- Run ASR (off the event loop; faster_whisper is blocking) ----
            span_seconds = chunk_span_seconds(c1)
            asr_result = await run_in_threadpool(
                backend.transcribe,
                settings,
                audio_bytes,
                c1["codec"],
                span_seconds,
                chunk_id,
            )

            # ---- Build + self-validate the C2 record -------------------------
            c2 = build_c2(c1, asr_result, backend.PIPELINE_VERSION, now_iso())
            c2_problems = schemas.validate_c2(c2)
            if c2_problems:  # pragma: no cover - would indicate a builder bug
                raise HTTPException(
                    status_code=500,
                    detail={"error": "produced C2 failed schema validation", "violations": c2_problems},
                )
            C2Record.model_validate(c2)

            # ---- Write to storage /context (idempotent upsert on record_id) --
            try:
                resp = await storage.post_record(c2)
            except httpx.HTTPError as exc:
                raise HTTPException(
                    status_code=502, detail={"error": f"context write failed: {exc}"}
                )

            record_id = (resp or {}).get("record_id") or c2["record_id"]
            dedup.put(chunk_id, record_id)
            return JSONResponse(content={"ok": True, "record_id": record_id})

    return app


app = create_app()
