"""A tiny in-process stand-in for the storage service, used by the tests.

Serves the two endpoints inference calls:
  * GET  /model-directory/resolve?user_id=...  -> a valid C6 resolve (base model)
  * POST /sessions/turns                        -> records the C4 body in memory

Because it runs in the SAME process as the test (uvicorn on a thread), the test
can import RECORDED_TURNS and assert on it directly.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request

# Every C4 turn record POSTed to /sessions/turns lands here, in order.
RECORDED_TURNS: list[dict[str, Any]] = []

# The model id the stub resolves everyone to (matches the v0 base model).
RESOLVED_MODEL_ID = "Qwen/Qwen3-VL-32B-Instruct"

stub = FastAPI(title="storage-stub")


@stub.get("/model-directory/resolve")
async def resolve(user_id: str):
    # C6 resolve v0: base model, no adapter.
    return {"model_id": RESOLVED_MODEL_ID, "adapter": "base", "adapter_path": None}


@stub.post("/sessions/turns")
async def write_turn(request: Request):
    body = await request.json()
    RECORDED_TURNS.append(body)
    return {"ok": True, "turn_id": body.get("turn_id")}


def reset() -> None:
    RECORDED_TURNS.clear()
