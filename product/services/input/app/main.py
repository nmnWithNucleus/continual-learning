"""Input service (:8081) — computer text chat surface + /api/turn (C3 producer + C9 relay).

Serve-loop MVP v0.0. Flow:

    browser  --POST /api/turn {text, session_id?}-->  input
    input    --QueryBuilder builds+validates C3------>  inference :8010 /infer
    inference --streams C9 (text chunks, U+001E, end frame)-->  input
    input    --relays the C9 stream UNCHANGED----------------->  browser

Input owns the C3 UserPrompt and the browser surface; it does NOT parse or rewrite
the C9 body — it is a straight pass-through so output's C9 reader (WS-C) sees the
inference stream byte-for-byte.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import AsyncIterator, Optional

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.query_builder import QueryBuilder, validate_c3

# ---------------------------------------------------------------------------
# Config (env)
# ---------------------------------------------------------------------------
INFERENCE_URL = os.environ.get("INFERENCE_URL", "http://localhost:8010").rstrip("/")
STATIC_DIR = Path(__file__).resolve().parent / "static"

# C9 wire-format record separator (U+001E). Input only needs it for the
# unreachable-inference fallback frame below; the happy path never inspects it.
SEP = "\u001e"

app = FastAPI(title="Nucleus input service", version="0.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

qb = QueryBuilder()


class TurnRequest(BaseModel):
    text: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None  # MVP: no auth; defaults to the dev user.


# ---------------------------------------------------------------------------
# Inference call (streaming relay)
# ---------------------------------------------------------------------------
def _client_factory() -> httpx.AsyncClient:
    """Factory for the httpx client used to call inference.

    Kept as a seam so tests can stub the inference stream without a live server.
    ``timeout=None`` because a generation can stream for a while.
    """
    return httpx.AsyncClient(timeout=httpx.Timeout(None))


def _error_stream(turn_id: str, message: str) -> bytes:
    """A minimal, C9-conformant end frame for when inference is unreachable.

    Only used as a fallback: an empty answer, the U+001E separator, then a
    single end frame carrying ``error``. Keeps the browser contract intact
    (it always gets a parseable C9 stream) instead of a bare 500.
    """
    frame = {
        "contract": "C9",
        "version": "0",
        "turn_id": turn_id,
        "model_id": "unknown",
        "adapter": "base",
        "finished": True,
        "error": message,
    }
    return (SEP + json.dumps(frame)).encode("utf-8")


async def _iter_inference(c3: dict) -> AsyncIterator[bytes]:
    """Stream inference's C9 response and relay it upstream unchanged."""
    client = _client_factory()
    try:
        async with client.stream("POST", f"{INFERENCE_URL}/infer", json=c3) as resp:
            async for chunk in resp.aiter_bytes():
                yield chunk
    except httpx.HTTPError as exc:
        yield _error_stream(c3["turn_id"], f"inference unreachable: {exc}")
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "input", "inference_url": INFERENCE_URL}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.post("/api/turn")
async def api_turn(req: TurnRequest):
    text = (req.text or "").strip()
    if not text:
        return JSONResponse(status_code=400, content={"error": "text must be non-empty"})

    session_id = req.session_id or qb.new_session_id()
    turn_id = qb.new_turn_id()

    prompt = qb.build(
        text,
        user_id=req.user_id,
        session_id=session_id,
        turn_id=turn_id,
    )
    c3 = prompt.model_dump()
    validate_c3(c3)  # never emit a non-conformant C3

    # Ids travel in headers so the surface can keep the session across turns
    # WITHOUT us touching the C9 body (which we relay byte-for-byte).
    headers = {
        "X-Session-Id": session_id,
        "X-Turn-Id": turn_id,
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # ask proxies not to buffer the stream
    }
    return StreamingResponse(
        _iter_inference(c3),
        media_type="application/octet-stream",
        headers=headers,
    )
