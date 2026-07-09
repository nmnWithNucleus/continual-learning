"""Inference service HTTP surface (FastAPI, :8010).

POST /infer  — body = a C3 UserPrompt. Resolve (C6) -> generate (mock|vllm) ->
               stream the answer as the C9 wire format -> persist a C4 turn
               record to storage.
GET  /health — liveness + effective config.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import backends
from .config import get_settings
from .contracts import ValidationError, validate_contract
from .models import C4TurnRecord, C9EndFrame, Usage
from .prompt import extract_user_text
from .storage_client import resolve_model, write_turn
from .wire import RECORD_SEPARATOR_BYTES

logger = logging.getLogger("inference")

app = FastAPI(title="Nucleus Inference Service", version="0.0")


def _now_iso() -> str:
    """RFC3339 / ISO-8601 UTC timestamp, e.g. 2026-07-09T05:42:00.123456+00:00."""
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
async def health():
    settings = get_settings()
    return {
        "status": "ok",
        "service": "inference",
        "backend": settings.model_backend,
        "model_id": settings.model_id,
        "storage_url": settings.storage_url,
    }


@app.post("/infer")
async def infer(request: Request):
    settings = get_settings()

    # ---- Parse + validate the incoming C3 UserPrompt --------------------------
    try:
        c3 = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"error": "body is not valid JSON"})

    try:
        validate_contract("c3", c3)
    except ValidationError as exc:
        # A malformed C3 has no dependable turn_id, so we cannot build a valid C9
        # end frame — return a plain client error instead of a stream.
        return JSONResponse(
            status_code=422,
            content={"error": f"C3 validation failed: {exc.message}"},
        )

    user_id = c3["user_id"]
    session_id = c3["session_id"]
    turn_id = c3["turn_id"]
    created_at = _now_iso()  # turn opened
    user_text = extract_user_text(c3)

    # ---- Resolve the model (C6, base fallback on failure) ---------------------
    resolution = await resolve_model(settings, user_id)
    model_id = resolution["model_id"]
    adapter = resolution["adapter"]
    system_prompt = settings.system_prompt
    backend = backends.select(settings)

    async def stream_body():
        usage_out: dict = {}
        parts: list[str] = []
        error: str | None = None

        # 1) Answer text chunks.
        try:
            async for chunk in backend.stream(
                settings, system_prompt, user_text, usage_out
            ):
                parts.append(chunk)
                yield chunk.encode("utf-8")
        except Exception as exc:  # noqa: BLE001
            error = f"generation failed: {exc}"
            logger.exception("generation failed for turn %s", turn_id)

        answer = "".join(parts)

        # 2) Separator, then the single JSON end frame.
        yield RECORD_SEPARATOR_BYTES
        usage = None
        if usage_out.get("prompt_tokens") is not None or usage_out.get("output_tokens") is not None:
            usage = Usage(
                prompt_tokens=usage_out.get("prompt_tokens"),
                output_tokens=usage_out.get("output_tokens"),
            )
        end_frame = C9EndFrame(
            turn_id=turn_id,
            model_id=model_id,
            adapter=adapter,
            usage=usage,
            finished=True,
            error=error,
        )
        yield json.dumps(end_frame.model_dump(exclude_none=True)).encode("utf-8")

        # 3) Persist the turn (C4). Best-effort: the answer is already delivered.
        record = C4TurnRecord(
            user_id=user_id,
            session_id=session_id,
            turn_id=turn_id,
            user_prompt=c3,
            response_text=answer,
            model_id=model_id,
            adapter=adapter,
            created_at=created_at,
            completed_at=_now_iso(),
        )
        try:
            await write_turn(settings, record.model_dump())
        except Exception as exc:  # noqa: BLE001
            logger.warning("C4 write failed for turn %s: %s", turn_id, exc)

    return StreamingResponse(stream_body(), media_type="application/octet-stream")
