"""End-to-end mock-loop test: POST a C3 -> read the C9 stream -> assert a C4 landed.

Hermetic: mock backend (no GPU), storage stub on localhost. Drives the inference
app in-process via httpx's ASGI transport; the stub is a real HTTP server so the
C6 resolve and C4 write exercise the real network path.
"""
from __future__ import annotations

import asyncio
import json

import httpx

from app.contracts import validate_contract
from app.main import app
from app.wire import split_stream
from . import storage_stub


def _c3(text: str = "What is the capital of France?") -> dict:
    return {
        "contract": "C3",
        "version": "0",
        "user_id": "user-123",
        "session_id": "sess-abc",
        "turn_id": "turn-0001",
        "created_at": "2026-07-09T05:42:00+00:00",
        "messages": [{"role": "user", "text": text}],
        "client_capabilities": {
            "surface": "computer",
            "modalities": ["text"],
            "can_render_markdown": True,
        },
        "template_version": "qb-text-v0",
    }


async def _post_infer(payload: dict) -> bytes:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://inference") as client:
        resp = await client.post("/infer", json=payload)
        assert resp.status_code == 200
        return resp.content


def test_infer_streams_c9_and_persists_c4(mock_env):
    c3 = _c3()
    body = asyncio.run(_post_infer(c3))

    # ---- C9 wire format: text, then U+001E, then one JSON end frame ----------
    answer_bytes, frame_bytes = split_stream(body)
    answer = answer_bytes.decode("utf-8")
    assert answer.strip(), "answer text must be non-empty"
    assert "[mock model" in answer  # unmistakably the mock backend

    end_frame = json.loads(frame_bytes.decode("utf-8"))
    validate_contract("c9", end_frame)  # conforms to c9_response_stream.v0.json
    assert end_frame["contract"] == "C9"
    assert end_frame["version"] == "0"
    assert end_frame["turn_id"] == c3["turn_id"]
    assert end_frame["adapter"] == "base"
    assert end_frame["model_id"] == storage_stub.RESOLVED_MODEL_ID
    assert end_frame["finished"] is True
    assert "error" not in end_frame
    assert end_frame["usage"]["output_tokens"] > 0
    assert end_frame["usage"]["prompt_tokens"] > 0

    # ---- C4: exactly one turn record persisted to storage, schema-valid ------
    assert len(storage_stub.RECORDED_TURNS) == 1
    record = storage_stub.RECORDED_TURNS[0]
    validate_contract("c4", record)  # includes the nested-C3 $ref
    assert record["turn_id"] == c3["turn_id"]
    assert record["user_id"] == c3["user_id"]
    assert record["session_id"] == c3["session_id"]
    assert record["adapter"] == "base"
    assert record["response_text"] == answer  # persisted text == streamed text
    assert record["user_prompt"] == c3        # full C3 embedded, untruncated
    assert record["tool_traces"] == []
    assert record["mentor_traces"] == []
    assert record["created_at"] and record["completed_at"]


def test_infer_rejects_malformed_c3(mock_env):
    async def _bad() -> int:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://inference") as client:
            # Missing required fields (turn_id, messages, ...) -> C3 invalid.
            resp = await client.post("/infer", json={"contract": "C3", "version": "0"})
            return resp.status_code

    status = asyncio.run(_bad())
    assert status == 422
    # A rejected request must not persist a turn.
    assert storage_stub.RECORDED_TURNS == []


def test_health_reports_backend(mock_env):
    async def _get() -> dict:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://inference") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            return resp.json()

    body = asyncio.run(_get())
    assert body["status"] == "ok"
    assert body["service"] == "inference"
    assert body["backend"] == "mock"
