"""Recording service HTTP surface (FastAPI, :8084).

POST /capture/run  — run one headless capture session: carve a continuous audio
                     source into chunks and, per chunk (blob-first), PUT bytes to
                     storage /raw then push a C1 envelope to data-processing /ingest.
                     Returns {stream_id, chunks_emitted, chunk_ids, sequences, record_ids}.
/capture/*         — client segment upload, demux, continuity ledger + gap report
                     (phone web / extension / mac CLI; see app/capture_web.py for the
                     wire; renamed from /ingest/* 2026-07-18 so /ingest stays uniquely
                     data-processing's C1 receiver — no alias, refresh loaded pages).
/client/           — the phone web client (clients/web/, static); GET / redirects here.
GET  /health       — liveness.

Also drivable as a module CLI (see app/cli.py): `python -m app.cli ...`.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import capture_web, capturer, emitter, ledger
from .config import get_settings
from .metrics import METRICS, MetricsASGIMiddleware
from .models import CaptureRunRequest, CaptureRunResponse, Health

_PROM_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _rec_route_template(path: str) -> str:
    """Collapse variable path segments so HTTP-metric cardinality is one series per
    ROUTE, not per session_id."""
    if path.startswith("/capture/sessions/"):
        rest = path[len("/capture/sessions/"):]
        tail = rest.split("/", 1)
        suffix = f"/{tail[1]}" if len(tail) > 1 else ""
        return "/capture/sessions/{session_id}" + suffix
    if path.startswith("/client/"):
        return "/client/*"
    return path


# One /metrics scrape invokes all seven ledger-derived gauge sources back-to-back; without
# this each would re-open sqlite + re-walk sessions on the event loop under the metrics lock.
# Memoize the snapshot per (var_dir) with a short TTL so a scrape does ONE ledger pass while
# staying fresh across scrapes. Keyed on var_dir so a test's fresh tmp ledger never reads a
# prior test's cached snapshot.
_SNAP_TTL_S = 1.0
_snap_cache: dict[str, tuple[float, dict]] = {}


def _register_metric_sources() -> None:
    """Ledger-derived pull-time gauges (read at scrape time from the current var_dir)."""
    def snap():
        settings = get_settings()
        key = settings.var_dir
        now = time.monotonic()
        cached = _snap_cache.get(key)
        if cached is None or now - cached[0] > _SNAP_TTL_S:
            value = ledger.for_settings(settings).metrics_snapshot()
            _snap_cache[key] = (now, value)
            return value
        return cached[1]

    METRICS.add_gauge_source(
        "rec_segments", "Client segments by ledger state.",
        lambda: [((st,), n) for st, n in snap()["segments_by_state"].items()],
        labelnames=["state"],
    )
    METRICS.add_gauge_source(
        "rec_chunks", "Emitted C1 chunks by modality.",
        lambda: [((m,), n) for m, n in snap()["chunks_by_modality"].items()],
        labelnames=["modality"],
    )
    METRICS.add_gauge_source(
        "rec_chunks_dp_state", "Chunks by downstream DP state (accepted/processed/unemitted).",
        lambda: [((s,), n) for s, n in snap()["chunks_by_dp_state"].items()],
        labelnames=["dp_state"],
    )
    METRICS.add_gauge_source("rec_sessions_total", "Capture sessions seen.",
                             lambda: snap()["sessions_total"])
    METRICS.add_gauge_source("rec_sessions_active", "Capture sessions still recording.",
                             lambda: snap()["sessions_active"])
    METRICS.add_gauge_source("rec_client_missing_total",
                             "Client-leg missing segments (dropped before the server).",
                             lambda: snap()["client_missing_total"])
    METRICS.add_gauge_source("rec_client_duplicate_deliveries_total",
                             "Client-leg duplicate segment deliveries (at-least-once).",
                             lambda: snap()["client_duplicate_deliveries_total"])

# The phone web client (WS-B), served same-origin. Anchored to THIS file so the
# mount works regardless of the process's cwd.
WEB_CLIENT_DIR = Path(__file__).resolve().parents[1] / "clients" / "web"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Ack-then-crash must not silently lose segments: whatever the ledger still
    # holds as 'received' (acked, never emitted) is re-enqueued now, reusing the
    # chunk_ids persisted before the crash. 'failed' stays failed until /retry.
    emitter.reenqueue_pending(app)
    yield
    await emitter.shutdown(app)


app = FastAPI(
    title="Nucleus recording service",
    version="0.1",
    summary="Continuous life-stream capture -> /raw blob + C1 push (learn-loop M0/M1).",
    lifespan=lifespan,
)

# D9 observability: request/latency/error metrics + capture-health (ledger-derived) +
# downstream retry + emit-latency, exposed at /metrics (Prometheus text). Emission side
# only; platform scrapes it (ARCHITECTURE.md §Observability).
_register_metric_sources()
app.add_middleware(MetricsASGIMiddleware, metrics=METRICS, prefix="rec",
                   templatizer=_rec_route_template)

app.include_router(capture_web.router, prefix="/capture")


@app.get("/metrics", include_in_schema=False)
async def metrics_endpoint() -> PlainTextResponse:
    return PlainTextResponse(METRICS.render(), media_type=_PROM_CONTENT_TYPE)

# check_dir=False: the client directory ships with the repo, but its absence must
# not be a startup hard-dependency (e.g. a capture-only deployment).
app.mount("/client", StaticFiles(directory=WEB_CLIENT_DIR, html=True, check_dir=False), name="client")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/client/")


@app.get("/health", response_model=Health)
async def health() -> Health:
    return Health(ok=True)


@app.post("/capture/run", response_model=CaptureRunResponse)
async def capture_run(req: CaptureRunRequest) -> CaptureRunResponse:
    settings = get_settings()
    result = await capturer.run_session(
        settings=settings,
        storage_url=req.storage_url,
        dp_url=req.dp_url,
        modality=req.modality,
        source=req.source,
        chunk_seconds=req.chunk_seconds,
        base_wallclock=req.base_wallclock,
        user_id=req.user_id,
        device_id=req.device_id,
        sample_seconds=req.sample_seconds,
    )
    return CaptureRunResponse(**result)
