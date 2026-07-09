"""Shared fixtures: a TestClient bound to a throwaway SQLite DB + blob dir, and
C3/C4/C2 builders."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    """A fresh app + empty temp DB + empty temp /raw dir per test (env read at
    create_app() time)."""
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("STORAGE_RAW_DIR", str(tmp_path / "raw_store"))
    # Import inside the fixture so the env vars are set before create_app() runs.
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_id_for(chunk_id: str, pipeline_version: str) -> str:
    """Model data-processing's deterministic record_id = f(chunk_id, pipeline_version).

    Storage treats record_id as an opaque idempotency key; this just gives tests a stable,
    URL-safe id that is byte-identical across reprocesses of the same chunk+version.
    """
    return hashlib.sha256(f"{chunk_id}|{pipeline_version}".encode("utf-8")).hexdigest()[:32]


def make_c3(
    user_id: str,
    session_id: str,
    turn_id: str,
    text: str = "What is the capital of France?",
    created_at: str | None = None,
) -> dict[str, Any]:
    return {
        "contract": "C3",
        "version": "0",
        "user_id": user_id,
        "session_id": session_id,
        "turn_id": turn_id,
        "created_at": created_at or _now(),
        "messages": [{"role": "user", "text": text}],
        "client_capabilities": {
            "surface": "computer",
            "modalities": ["text"],
            "can_render_markdown": True,
        },
        "template_version": "qb-text-v0",
    }


def make_c4(
    user_id: str = "user-1",
    session_id: str = "sess-1",
    turn_id: str | None = None,
    response_text: str = "The capital of France is Paris.",
    created_at: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any]:
    turn_id = turn_id or f"turn-{uuid.uuid4().hex[:8]}"
    created_at = created_at or _now()
    completed_at = completed_at or created_at
    return {
        "contract": "C4",
        "version": "0",
        "user_id": user_id,
        "session_id": session_id,
        "turn_id": turn_id,
        "user_prompt": make_c3(user_id, session_id, turn_id, created_at=created_at),
        "response_text": response_text,
        "model_id": "Qwen/Qwen3-VL-32B-Instruct",
        "adapter": "base",
        "created_at": created_at,
        "completed_at": completed_at,
        "tool_traces": [],
        "mentor_traces": [],
    }


def make_c2(
    user_id: str = "user-1",
    record_id: str | None = None,
    chunk_id: str | None = None,
    stream_id: str = "stream-01",
    device_id: str = "dev-mic-1",
    blob_ref: str = "ab/cd/abcd0123456789",
    t_start: str | None = None,
    t_end: str | None = None,
    text: str = "hello world this is a test transcript",
    pipeline_version: str = "asr-mock-v0",
) -> dict[str, Any]:
    """Build a valid C2 processed record (v0: one ASR transcript per audio chunk)."""
    chunk_id = chunk_id or f"chunk-{uuid.uuid4().hex[:8]}"
    record_id = record_id or record_id_for(chunk_id, pipeline_version)
    t_start = t_start or _now()
    t_end = t_end or t_start
    return {
        "contract": "C2",
        "version": "0",
        "record_id": record_id,
        "user_id": user_id,
        "source": {
            "device_id": device_id,
            "stream_id": stream_id,
            "chunk_id": chunk_id,
            "blob_ref": blob_ref,
            "modality": "audio",
        },
        "t_start": t_start,
        "t_end": t_end,
        "content": {
            "kind": "transcript",
            "text": text,
            "language": "en",
            "segments": [
                {"t_start": t_start, "t_end": t_end, "text": text, "speaker": None},
            ],
        },
        "enrichments": {"speakers": [], "faces": [], "places": [], "objects": []},
        "pipeline_version": pipeline_version,
        "processed_at": _now(),
    }
