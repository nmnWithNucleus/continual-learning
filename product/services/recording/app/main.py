"""Recording service HTTP surface (FastAPI, :8084).

POST /capture/run  — run one headless capture session: carve a continuous audio
                     source into chunks and, per chunk (blob-first), PUT bytes to
                     storage /raw then push a C1 envelope to data-processing /ingest.
                     Returns {stream_id, chunks_emitted, chunk_ids, sequences, record_ids}.
GET  /health       — liveness.

Also drivable as a module CLI (see app/cli.py): `python -m app.cli ...`.
"""
from __future__ import annotations

from fastapi import FastAPI

from . import capturer
from .config import get_settings
from .models import CaptureRunRequest, CaptureRunResponse, Health

app = FastAPI(
    title="Nucleus recording service",
    version="0.0",
    summary="Continuous life-stream capture -> /raw blob + C1 push (learn-loop M0).",
)


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
        source=req.source,
        chunk_seconds=req.chunk_seconds,
        base_wallclock=req.base_wallclock,
        user_id=req.user_id,
        device_id=req.device_id,
        sample_seconds=req.sample_seconds,
    )
    return CaptureRunResponse(**result)
