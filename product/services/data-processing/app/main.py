"""data-processing service HTTP surface (FastAPI, :8085) — MODALITY-AGNOSTIC core.

POST /ingest  — body = a pushed C1 raw-stream envelope. Validate C1 -> dedup on
                chunk_id -> pull the blob by ref from storage -> dispatch to the
                Processor registered for envelope.modality -> for EACH ProcessedUnit
                it returns, assemble a C2 record and POST it to storage /context ->
                return {ok, record_ids:[...]}. This is the C1 push receiver.

                Two processing modes (INGEST_ASYNC, FROZEN once at startup):
                  * INLINE (default): process inside the request, return
                    {ok, record_ids:[...]} (200). Byte-identical to M0.
                  * ASYNC (M7, arriving early): ACK 202 {ok, accepted, chunk_id} the
                    moment the chunk is claimed, process on a worker pool. Retry safety
                    rides chunk_id dedup + deterministic record_id upserts. A redelivery
                    of an ALREADY-DONE chunk still returns its record_ids (200); an
                    in-flight redelivery re-ACKs 202; a full queue is 503 backpressure.
                Deterministic C1/modality rejections (400/422/501) resolve
                SYNCHRONOUSLY in BOTH modes — never deferred into a silent dead-letter.
GET  /health  — liveness + effective ASR backend + ingest mode.
GET  /metrics — Prometheus text exposition (D9 observability; METRICS_ENABLED).
GET  /continuity              — per-stream break/dup report (ContinuityTracker),
                the check behind "zero silent loss": recording's gap report
                queries it to close the loop across both capture legs. Async /ingest
                ACKs at ACCEPT, so this now also carries `processed` + `dead_lettered`
                so recording tells an in-flight chunk from a lost one.
GET  /continuity/{stream_id}  — one stream's entry (404 unknown).

The core knows nothing about audio/image/video/text: modality behavior lives in
disjoint plugin files under ``processing/processors/`` (see ``processing/``), so a
future session owns a modality by dropping in one file. One chunk MAY yield many
records (e.g. video keyframes); audio/image/text yield a single-element list.

The whole loop runs headless on any box: ASR_BACKEND defaults to `mock` (no GPU),
INGEST_ASYNC defaults off (inline), METRICS_ENABLED is dependency-free.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from . import schemas
from .config import get_settings
from .continuity import ContinuityTracker
from .dedup import DedupStore
from .ingest_core import ProcessingError, process_chunk
from .ingest_queue import IngestQueue, QueueFull
from .metrics import MetricsASGIMiddleware, Metrics
from .models import C1Envelope
from .processing.registry import get_processor
from .storage_client import StorageClient
from .timeutil import now_iso

logger = logging.getLogger("data-processing")

# Prometheus text exposition content type (format version 0.0.4).
_PROM_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _dp_route_template(path: str) -> str:
    """Collapse variable path segments so HTTP-metric label cardinality is bounded to
    one series per ROUTE, not per stream_id."""
    if path.startswith("/continuity/"):
        return "/continuity/{stream_id}"
    return path


def _setup_metrics(app: FastAPI, metrics: Metrics) -> None:
    """Declare the DP metric families + register pull-time gauge sources (queue depth,
    continuity aggregates read live at scrape time)."""
    metrics.declare_counter(
        "dp_ingest_total", "C1 /ingest outcomes.", ["modality", "result"],
    )  # result: accepted | processed | deduped | duplicate | rejected
    metrics.declare_counter("dp_dedup_hits_total", "Redeliveries served from the dedup map.")
    metrics.declare_counter(
        "dp_ingest_retries_total", "Worker transient-failure retries.", ["modality"],
    )
    metrics.declare_counter(
        "dp_dead_letter_total", "Chunks dead-lettered (no C2 written).", ["modality"],
    )
    metrics.declare_counter(
        "dp_vad_empty_total", "Audio chunks whose transcript was empty (VAD-gated silence).",
        ["modality"],
    )
    metrics.declare_histogram(
        "dp_stage_seconds", "Per-stage processing latency (seconds).", ["modality", "stage"],
    )

    # ---- Pull-time gauges: live state owned by the queue + continuity tracker ------
    def _queue_depth():
        q = getattr(app.state, "ingest_queue", None)
        return q.queued() if q is not None else 0

    def _queue_processing():
        q = getattr(app.state, "ingest_queue", None)
        return q.processing() if q is not None else 0

    metrics.add_gauge_source("dp_ingest_queue_depth",
                             "Chunks waiting in the async ingest queue.", _queue_depth)
    metrics.add_gauge_source("dp_ingest_processing",
                             "Chunks currently being processed by a worker.", _queue_processing)

    def _continuity_agg():
        """Aggregate continuity counts across streams (bounded cardinality — totals,
        not per-stream series). Recomputed from live tracker state each scrape."""
        report = app.state.continuity.report()
        streams = report["streams"]

        def _width(runs):
            return sum(hi - lo + 1 for lo, hi in runs)

        return {
            "streams": len(streams),
            "missing": sum(_width(s["missing"]) for s in streams),
            "processed": sum(_width(s["processed"]) for s in streams),
            "dead_lettered": sum(_width(s["dead_lettered"]) for s in streams),
            "duplicate_deliveries": sum(s["duplicate_deliveries"] for s in streams),
            "sequence_conflicts": sum(s["sequence_conflicts"] for s in streams),
        }

    metrics.add_gauge_source("dp_continuity_streams", "Observed C1 streams.",
                             lambda: _continuity_agg()["streams"])
    metrics.add_gauge_source("dp_continuity_missing_total",
                             "Total missing (never-delivered) sequences across streams.",
                             lambda: _continuity_agg()["missing"])
    metrics.add_gauge_source("dp_continuity_processed_total",
                             "Total sequences with a C2 durably written.",
                             lambda: _continuity_agg()["processed"])
    metrics.add_gauge_source("dp_continuity_dead_lettered_total",
                             "Total dead-lettered sequences (accepted, never processed).",
                             lambda: _continuity_agg()["dead_lettered"])
    metrics.add_gauge_source("dp_continuity_duplicate_deliveries_total",
                             "Total at-least-once duplicate deliveries across streams.",
                             lambda: _continuity_agg()["duplicate_deliveries"])
    metrics.add_gauge_source("dp_continuity_sequence_conflicts_total",
                             "Total sequence conflicts (one slot, two chunk_ids).",
                             lambda: _continuity_agg()["sequence_conflicts"])


def create_app() -> FastAPI:
    """App factory. Reads env at call time so tests can point STORAGE_URL / flip
    ASR_BACKEND before construction and inject a mock storage transport after."""
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Async mode only: spin up the worker pool on the RUNNING loop (loop-affinity,
        # like recording's emitter). Inline mode touches nothing here — an early return
        # so `with TestClient(app)` startup+shutdown are pure no-ops (all M0 tests).
        if app.state.ingest_async:
            app.state.dedup.reset_inflight()  # clear any claim from a prior loop reuse
            queue = IngestQueue(
                app,
                workers=settings.ingest_workers,
                maxsize=settings.ingest_queue_max,
                max_retries=settings.ingest_max_retries,
                backoff=settings.ingest_retry_backoff,
            )
            queue.start()
            app.state.ingest_queue = queue
        yield
        if app.state.ingest_async:
            queue = getattr(app.state, "ingest_queue", None)
            if queue is not None:
                await queue.drain_and_close(settings.ingest_drain_timeout)
                app.state.ingest_queue = None

    app = FastAPI(
        title="Nucleus data-processing service",
        version="0.0",
        summary="C1 -> Processor -> C2 capture skeleton for the learn loop.",
        lifespan=lifespan,
    )
    # State constructed HERE (not in lifespan): conftest injects app.state.storage's
    # transport AFTER create_app() but BEFORE the TestClient `with` block, and
    # process_chunk reads app.state.storage per call — so the fake transport is honored.
    app.state.storage = StorageClient(settings.storage_url, timeout=settings.http_timeout)
    app.state.dedup = DedupStore()
    app.state.continuity = ContinuityTracker()
    app.state.ingest_async = settings.ingest_async   # FROZEN at startup (no per-request read)
    app.state.ingest_queue = None
    app.state.metrics = Metrics() if settings.metrics_enabled else None
    if app.state.metrics is not None:
        _setup_metrics(app, app.state.metrics)
        app.add_middleware(MetricsASGIMiddleware, metrics=app.state.metrics,
                           prefix="dp", templatizer=_dp_route_template)

    def _metrics_inc(name: str, labels: dict | None = None) -> None:
        if app.state.metrics is not None:
            app.state.metrics.inc(name, labels)

    @app.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "asr_backend": get_settings().asr_backend,
            "ingest_mode": "async" if app.state.ingest_async else "inline",
        }

    @app.get("/metrics")
    def metrics_endpoint() -> PlainTextResponse:
        if app.state.metrics is None:
            return PlainTextResponse("# metrics disabled\n", media_type=_PROM_CONTENT_TYPE)
        return PlainTextResponse(app.state.metrics.render(), media_type=_PROM_CONTENT_TYPE)

    @app.post("/ingest")
    async def ingest(request: Request) -> JSONResponse:
        settings = get_settings()

        # ---- Parse + validate the incoming C1 envelope (SYNCHRONOUS in both modes) --
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

        # ---- Continuity observation (EVERY schema-valid delivery, at ACCEPT) -------
        # Noted here — after the C1 gate, before any return — so ALL paths count:
        # fresh process, dedup fast path, in-flight dup, async accept. Dedup must never
        # silently absorb a break/duplicate signal; an invalid C1 must never register
        # one. Async ACKs here too, so "seen" no longer implies "processed" — the
        # processed/dead_lettered sets (filled at processing time) carry that.
        request.app.state.continuity.note(
            c1["stream_id"], c1["sequence"], chunk_id,
            user_id=c1["user_id"], device_id=c1["device_id"], modality=modality,
            now_iso=now_iso(),
        )

        # ---- Select the Processor for this modality (SYNCHRONOUS 501, pre-claim) ----
        # C1 schema already restricts modality to the enum; a valid modality with no
        # registered plugin is a clean 501, not a crash / a silent dead-letter.
        try:
            processor = get_processor(modality)
        except KeyError:
            raise HTTPException(
                status_code=501,
                detail={"error": f"no processor registered for modality {modality!r}"},
            )
        pipeline_version = processor.pipeline_version(settings)

        if request.app.state.ingest_async:
            return await _ingest_async(request, c1, settings, processor, pipeline_version)
        return await _ingest_inline(request, c1, settings, processor, pipeline_version)

    async def _ingest_inline(request, c1, settings, processor, pipeline_version) -> JSONResponse:
        """M0 behaviour, byte-identical: process inside the request, return record_ids."""
        dedup: DedupStore = request.app.state.dedup
        chunk_id = c1["chunk_id"]
        metrics = request.app.state.metrics

        # Dedup (fast path): already-processed chunk_id -> prior record_ids.
        prior = dedup.get(chunk_id)
        if prior is not None:
            logger.info("dedup hit (processed) chunk_id=%s -> %s", chunk_id, prior)
            _metrics_inc("dp_dedup_hits_total")
            _metrics_inc("dp_ingest_total", {"modality": c1["modality"], "result": "deduped"})
            return JSONResponse(content={"ok": True, "record_ids": prior})

        # Serialize concurrent redeliveries of the same in-flight chunk_id.
        lock = await dedup.lock_for(chunk_id)
        async with lock:
            prior = dedup.get(chunk_id)
            if prior is not None:  # resolved while we waited on the lock (in-flight)
                logger.info("dedup hit (in-flight) chunk_id=%s -> %s", chunk_id, prior)
                _metrics_inc("dp_dedup_hits_total")
                _metrics_inc("dp_ingest_total", {"modality": c1["modality"], "result": "deduped"})
                return JSONResponse(content={"ok": True, "record_ids": prior})

            try:
                record_ids = await process_chunk(
                    c1=c1, settings=settings, processor=processor,
                    pipeline_version=pipeline_version, storage=request.app.state.storage,
                    dedup=dedup, metrics=metrics,
                )
            except ProcessingError as exc:
                # Map the taxonomy back to the exact M0 HTTP status at the boundary.
                raise HTTPException(status_code=exc.http_status, detail=exc.detail)

            request.app.state.continuity.note_processed(c1["stream_id"], c1["sequence"])
            _metrics_inc("dp_ingest_total", {"modality": c1["modality"], "result": "processed"})
            return JSONResponse(content={"ok": True, "record_ids": record_ids})

    async def _ingest_async(request, c1, settings, processor, pipeline_version) -> JSONResponse:
        """ACK 202 the moment the chunk is claimed; a worker processes it."""
        dedup: DedupStore = request.app.state.dedup
        chunk_id = c1["chunk_id"]

        claim = await dedup.claim_for_async(chunk_id)
        if claim == "done":  # redelivery of a completed chunk -> known record_ids
            record_ids = dedup.get(chunk_id) or []
            logger.info("dedup hit (processed) chunk_id=%s -> %s", chunk_id, record_ids)
            _metrics_inc("dp_dedup_hits_total")
            _metrics_inc("dp_ingest_total", {"modality": c1["modality"], "result": "deduped"})
            return JSONResponse(content={"ok": True, "record_ids": record_ids})
        if claim == "inflight":  # already queued/processing -> don't double-enqueue
            _metrics_inc("dp_ingest_total", {"modality": c1["modality"], "result": "duplicate"})
            return JSONResponse(
                status_code=202,
                content={"ok": True, "accepted": True, "chunk_id": chunk_id, "duplicate": True},
            )

        # claimed by us -> enqueue for a worker.
        queue: IngestQueue | None = getattr(request.app.state, "ingest_queue", None)
        if queue is None:  # async configured but pool not up (no lifespan) — honest 503
            dedup.release_inflight(chunk_id)
            return JSONResponse(status_code=503,
                                content={"ok": False, "error": "ingest queue not ready"})
        try:
            queue.submit({
                "c1": c1, "settings": settings,
                "processor": processor, "pipeline_version": pipeline_version,
            })
        except QueueFull:
            dedup.release_inflight(chunk_id)  # never orphan the claim
            _metrics_inc("dp_ingest_total", {"modality": c1["modality"], "result": "rejected"})
            logger.warning("ingest queue full — 503 backpressure on chunk %s", chunk_id)
            return JSONResponse(status_code=503,
                                content={"ok": False, "error": "ingest queue full"})

        _metrics_inc("dp_ingest_total", {"modality": c1["modality"], "result": "accepted"})
        return JSONResponse(status_code=202,
                            content={"ok": True, "accepted": True, "chunk_id": chunk_id})

    @app.get("/continuity")
    def continuity_report(request: Request) -> dict:
        return request.app.state.continuity.report()

    @app.get("/continuity/{stream_id}")
    def continuity_stream(request: Request, stream_id: str) -> dict:
        entry = request.app.state.continuity.report_stream(stream_id)
        if entry is None:
            raise HTTPException(
                status_code=404,
                detail={"error": f"unknown stream_id {stream_id!r}"},
            )
        return entry

    return app


app = create_app()
