"""data-processing service HTTP surface (FastAPI, :8085) — MODALITY-AGNOSTIC core.

POST /ingest  — body = a pushed C1 raw-stream envelope. Validate C1 -> dedup on
                chunk_id -> pull the blob by ref from storage -> dispatch to the
                Processor registered for envelope.modality -> for EACH ProcessedUnit
                it returns, assemble a C2 record and POST it to storage /context ->
                return {ok, record_ids:[...]}. This is the C1 push receiver.
GET  /health  — liveness + effective ASR backend.

The core knows nothing about audio/image/video/text: modality behavior lives in
disjoint plugin files under ``processing/processors/`` (see ``processing/``), so a
future session owns a modality by dropping in one file. One chunk MAY yield many
records (e.g. video keyframes); audio/image/text yield a single-element list.

The whole loop runs headless on any box: ASR_BACKEND defaults to `mock` (no GPU).
"""
from __future__ import annotations

import hashlib
import logging

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import schemas
from .config import get_settings
from .dedup import DedupStore
from .models import C1Envelope, C2Record
from .pipeline import build_c2, chunk_span_seconds
from .processing.registry import get_processor
from .storage_client import StorageClient
from .timeutil import now_iso

logger = logging.getLogger("data-processing")


def create_app() -> FastAPI:
    """App factory. Reads env at call time so tests can point STORAGE_URL / flip
    ASR_BACKEND before construction and inject a mock storage transport after."""
    app = FastAPI(
        title="Nucleus data-processing service",
        version="0.0",
        summary="C1 -> Processor -> C2 capture skeleton for the learn loop.",
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
        modality = c1["modality"]
        dedup: DedupStore = request.app.state.dedup

        # ---- Dedup (fast path): already-processed chunk_id -> prior record_ids -
        prior = dedup.get(chunk_id)
        if prior is not None:
            logger.info("dedup hit (processed) chunk_id=%s -> %s", chunk_id, prior)
            return JSONResponse(content={"ok": True, "record_ids": prior})

        # ---- Select the Processor for this modality (before any I/O) ----------
        # C1 schema already restricts modality to the enum; a valid modality with no
        # registered plugin is a clean 501, not a crash.
        try:
            processor = get_processor(modality)
        except KeyError:
            raise HTTPException(
                status_code=501,
                detail={"error": f"no processor registered for modality {modality!r}"},
            )
        pipeline_version = processor.pipeline_version(settings)

        # Serialize concurrent redeliveries of the same in-flight chunk_id.
        lock = await dedup.lock_for(chunk_id)
        async with lock:
            prior = dedup.get(chunk_id)
            if prior is not None:  # resolved while we waited on the lock (in-flight)
                logger.info("dedup hit (in-flight) chunk_id=%s -> %s", chunk_id, prior)
                return JSONResponse(content={"ok": True, "record_ids": prior})

            storage: StorageClient = request.app.state.storage

            # ---- Pull the raw chunk bytes by blob_ref ------------------------
            try:
                blob_bytes = await storage.get_blob(c1["blob_ref"])
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
                actual = hashlib.sha256(blob_bytes).hexdigest()
                if actual != c1["blob_sha256"]:
                    raise HTTPException(
                        status_code=502,
                        detail={
                            "error": "blob sha256 mismatch",
                            "expected": c1["blob_sha256"],
                            "actual": actual,
                        },
                    )

            # ---- Run the modality Processor (off the event loop; may be heavy) -
            span_seconds = chunk_span_seconds(c1)
            units = await run_in_threadpool(
                processor.process, c1, blob_bytes, settings, span_seconds
            )
            if not units:  # a Processor must return >= 1 unit
                raise HTTPException(
                    status_code=500,
                    detail={"error": f"processor for {modality!r} returned no units"},
                )

            # ---- Assemble + write a C2 per unit (idempotent upsert on record_id)
            processed_at = now_iso()  # one stamp for the whole processing run
            record_ids: list[str] = []
            for unit in units:
                c2 = build_c2(c1, unit, pipeline_version, processed_at)

                c2_problems = schemas.validate_c2(c2)
                if c2_problems:  # pragma: no cover - would indicate a builder bug
                    raise HTTPException(
                        status_code=500,
                        detail={
                            "error": "produced C2 failed schema validation",
                            "violations": c2_problems,
                        },
                    )
                C2Record.model_validate(c2)

                try:
                    resp = await storage.post_record(c2)
                except httpx.HTTPError as exc:
                    raise HTTPException(
                        status_code=502, detail={"error": f"context write failed: {exc}"}
                    )
                record_ids.append((resp or {}).get("record_id") or c2["record_id"])

            dedup.put(chunk_id, record_ids)
            return JSONResponse(content={"ok": True, "record_ids": record_ids})

    return app


app = create_app()
