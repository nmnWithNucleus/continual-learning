"""Recording service HTTP surface (FastAPI, :8084).

POST /capture/run  — run one headless capture session: carve a continuous audio
                     source into chunks and, per chunk (blob-first), PUT bytes to
                     storage /raw then push a C1 envelope to data-processing /ingest.
                     Returns {stream_id, chunks_emitted, chunk_ids, sequences, record_ids}.
/capture/*         — client segment upload, demux, continuity ledger + gap report
                     (phone web / extension / mac CLI; see app/capture_web.py for the
                     wire). /ingest/* is a HIDDEN DEPRECATED ALIAS of the same routes
                     (renamed 2026-07-18 so /ingest stays uniquely data-processing's
                     C1 receiver; the alias keeps already-loaded phone pages working).
/client/           — the phone web client (clients/web/, static); GET / redirects here.
GET  /health       — liveness.

Also drivable as a module CLI (see app/cli.py): `python -m app.cli ...`.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import capture_web, capturer, emitter
from .config import get_settings
from .models import CaptureRunRequest, CaptureRunResponse, Health

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

app.include_router(capture_web.router, prefix="/capture")
# Deprecated alias: identical handlers, hidden from the OpenAPI schema. Kept so
# a phone page loaded before the rename keeps uploading until its next refresh.
app.include_router(capture_web.router, prefix="/ingest", include_in_schema=False)

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
