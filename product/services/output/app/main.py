"""Nucleus Output Service (:8082) — standalone C9 relay + static delivery client.

Serve-loop MVP v0.0. This service is intentionally the thinnest one: it moves a
C9 response stream produced by inference to wherever the user is, without
changing it. It is NOT on the web MVP's hot path (the input surface relays the
C9 stream to the browser directly and imports our ``static/c9_reader.js`` to
render it). This service proves the delivery *service boundary* for future
non-web surfaces and the proactive channel.

Endpoints
  GET  /health   -> liveness + a pointer to the browser client / self-test.
  GET  /         -> tiny index describing the service.
  POST /deliver  -> proxy a C9 stream from ``upstream_url`` to the caller,
                    byte-for-byte unchanged, with a delivery-ack header.
  /static/*      -> the browser C9 reader (c9_reader.js) + self-test page.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .relay import build_ack, relay_c9

STATIC_DIR = Path(__file__).parent / "static"
# C9 is UTF-8 answer text + a separator + a JSON end frame; text/plain is correct.
C9_MEDIA_TYPE = "text/plain; charset=utf-8"


class DeliverRequest(BaseModel):
    """Body for POST /deliver — where to pull the C9 stream from + how."""

    upstream_url: str = Field(..., description="Absolute URL emitting a C9 stream (e.g. inference /infer).")
    payload: Optional[Dict[str, Any]] = Field(
        default=None,
        description="JSON body to send upstream (e.g. a C3 UserPrompt). Omit for a GET-style pull.",
    )
    method: str = Field(default="POST", description="HTTP method for the upstream call.")
    turn_id: Optional[str] = Field(default=None, description="Turn id, echoed back in the delivery ack.")


def create_app(client: Optional[httpx.AsyncClient] = None) -> FastAPI:
    """Build the app. Pass ``client`` in tests to inject an httpx.MockTransport."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owns_client = client is None
        app.state.http = client or httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
        try:
            yield
        finally:
            if owns_client:
                await app.state.http.aclose()

    app = FastAPI(
        title="Nucleus Output Service",
        version="0.0",
        summary="C9 delivery relay + browser C9 reader (serve-loop MVP).",
        lifespan=lifespan,
    )

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {
            "status": "ok",
            "service": "output",
            "version": "0.0",
            "role": "c9-relay",
            "consumes": ["C9"],
            "client": "/static/c9_reader.js",
            "selftest": "/static/selftest.html",
        }

    @app.get("/")
    async def index() -> Dict[str, Any]:
        return {
            "service": "output",
            "description": "Delivers the C9 response stream to the user's surface, unchanged.",
            "endpoints": {
                "GET /health": "liveness",
                "POST /deliver": "relay a C9 stream from {upstream_url, payload?, method?, turn_id?}",
                "GET /static/c9_reader.js": "browser-side C9 reader/markdown renderer (ES module)",
                "GET /static/selftest.html": "self-test page for the browser client",
            },
        }

    @app.post("/deliver")
    async def deliver(req: DeliverRequest, request: Request) -> StreamingResponse:
        http: httpx.AsyncClient = request.app.state.http
        delivery_id = uuid4().hex
        headers = build_ack(delivery_id, req.turn_id, req.upstream_url)
        body = relay_c9(
            http,
            req.upstream_url,
            method=req.method,
            payload=req.payload,
            turn_id=req.turn_id,
        )
        return StreamingResponse(body, media_type=C9_MEDIA_TYPE, headers=headers)

    return app


app = create_app()
