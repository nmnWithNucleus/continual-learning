"""Shared fixtures: a TestClient bound to a throwaway SQLite DB, and C3/C4 builders."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    """A fresh app + empty temp DB per test (env read at create_app() time)."""
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "test.db"))
    # Import inside the fixture so the env var is set before create_app() runs.
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
