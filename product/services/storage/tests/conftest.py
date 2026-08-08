"""Shared fixtures: a TestClient bound to a throwaway SQLite DB + blob dir, and
C3/C4/C2 builders. C2 builders speak **v1** (D24): one record per chunk whose content
is a slots map. Storage validates and renders v1 exclusively."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    """A fresh app + empty temp DB + empty temp /raw dir + empty temp reservoir per test
    (env read at create_app() time)."""
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("STORAGE_RAW_DIR", str(tmp_path / "raw_store"))
    # The C14 reservoir is a filesystem store like /raw and needs the same redirection, or
    # a test would admit corpora into the repo's own app/reservoir dir.
    monkeypatch.setenv("STORAGE_RESERVOIR_DIR", str(tmp_path / "reservoir"))
    # STORAGE_RECIPES_DIR / STORAGE_POLICIES_DIR are deliberately NOT redirected: the C13
    # registry serves the service's own committed, read-only artifacts, and the tests want
    # the real ones. Modules that need a synthetic registry override them locally.
    # Import inside the fixture so the env vars are set before create_app() runs.
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_id_for(chunk_id: str, pipeline_version: str) -> str:
    """DP's L3 identity, restated: sha256(chunk_id NUL pipeline_version), lowercase hex.

    Storage treats record_id as an opaque idempotency key, but the v1 contract pins the
    64-hex shape, so the builders derive it exactly as data-processing does — NUL-joined
    so neither component can smuggle a separator into the other, byte-identical across
    reprocesses of the same (chunk_id, pipeline_version).
    """
    return hashlib.sha256(f"{chunk_id}\x00{pipeline_version}".encode("utf-8")).hexdigest()


# --- C2 v1 slot builders (the contract's six pinned slot types) --------------------


def slot_asr(text: str, *, version: str = "asr.v1-mock.v1", language: str = "en",
             splits: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """An `asr` slot. `value` == "" is the honest empty claim (VAD-gated silence)."""
    slot: dict[str, Any] = {"version": version, "language": language, "value": text}
    if splits is not None:
        slot["splits"] = splits
    return slot


def slot_transcript(splits: list[dict[str, Any]],
                    *, version: str = "speaker_align.v1-mock.v1") -> dict[str, Any]:
    """A `transcript` slot — speaker-aligned splits ({t_start, t_end, value, speaker});
    `speaker` is required-nullable."""
    return {"version": version, "splits": splits}


def slot_caption(text: str, *, version: str = "clipcap.v1-mock.v1") -> dict[str, Any]:
    return {"version": version, "value": text}


def slot_ocr(text: str, *, version: str = "screentext.v1-mock.v1") -> dict[str, Any]:
    """An `ocr` slot. `value` == "" is ran-and-empty, NOT a hole (L11)."""
    return {"version": version, "value": text}


def slot_acoustic(values: list[str], *, version: str = "acoustic.v1-mock.v1",
                  confidence: Optional[float] = None) -> dict[str, Any]:
    slot: dict[str, Any] = {"version": version, "values": values}
    if confidence is not None:
        slot["confidence"] = confidence
    return slot


def make_c2(
    user_id: str = "user-1",
    record_id: Optional[str] = None,
    chunk_id: Optional[str] = None,
    stream_id: str = "stream-01",
    device_id: str = "dev-mic-1",
    blob_ref: str = "ab/cd/abcd0123456789",
    t_start: Optional[str] = None,
    t_end: Optional[str] = None,
    text: str = "hello world this is a test transcript",
    pipeline_version: str = "asr.v1-mock.v1",
    modality: str = "audio",
    slots: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build a valid C2 **v1** record (one record per chunk, content is a slots map).

    Default: an audio record with one `asr` slot carrying `text` and a single split
    spanning the chunk. Pass `slots={}` for the legal all-optional-failure record, or a
    map built from the slot_* helpers above.
    """
    chunk_id = chunk_id or f"chunk-{uuid.uuid4().hex[:8]}"
    record_id = record_id or record_id_for(chunk_id, pipeline_version)
    t_start = t_start or _now()
    t_end = t_end or t_start
    if slots is None:
        slots = {"asr": slot_asr(
            text, splits=[{"t_start": t_start, "t_end": t_end, "value": text}])}
    return {
        "contract": "C2",
        "version": "1",
        "record_id": record_id,
        "user_id": user_id,
        "modality": modality,
        "source": {
            "device_id": device_id,
            "stream_id": stream_id,
            "chunk_id": chunk_id,
            "blob_ref": blob_ref,
        },
        "t_start": t_start,
        "t_end": t_end,
        "pipeline_version": pipeline_version,
        "content": {"slots": slots},
    }


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
