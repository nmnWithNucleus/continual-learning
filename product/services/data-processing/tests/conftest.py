"""Shared fixtures: the mock ASR loop wired to a MockTransport fake storage, and
a C1 envelope + blob builder that keeps blob_sha256/blob_bytes self-consistent.
"""
from __future__ import annotations

import hashlib
from typing import Any

import pytest
from fastapi.testclient import TestClient

from .fake_storage import FakeStorage

# Arbitrary bytes standing in for a raw audio chunk. The mock ASR ignores the
# bytes entirely, so real audio isn't needed to exercise the loop.
SAMPLE_AUDIO = b"RIFF\x00\x00\x00\x00WAVEfmt mock-audio-chunk-bytes"


@pytest.fixture()
def fake_storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture()
def client(monkeypatch, fake_storage, tmp_path) -> TestClient:
    """A fresh data-processing app on the mock backend, with its storage client
    bound to the MockTransport fake (env read at create_app() time). DP_VAR_DIR is
    isolated per test so the durable journal (var/dp.db) never leaks state between
    tests or into the repo tree."""
    monkeypatch.setenv("ASR_BACKEND", "mock")
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / "var"))
    from app.main import create_app

    app = create_app()
    # Inject the fake-storage transport into the (already constructed) client.
    app.state.storage._transport = fake_storage.transport()
    with TestClient(app) as c:
        c.fake_storage = fake_storage  # type: ignore[attr-defined]
        yield c


def make_c1(
    fake_storage: FakeStorage,
    *,
    chunk_id: str = "chunk-ULID-0001",
    stream_id: str = "stream-ULID-AAAA",
    sequence: int = 0,
    user_id: str = "pilot-user",
    device_id: str = "computer-mic-1",
    modality: str = "audio",
    codec: str = "audio/wav",
    t_start: str = "2026-07-09T12:00:00Z",
    t_end: str = "2026-07-09T12:00:05Z",
    blob_ref: str = "raw/pilot-user/chunk-ULID-0001.wav",
    audio: bytes = SAMPLE_AUDIO,
    register_blob: bool = True,
) -> dict[str, Any]:
    """Build a valid C1 envelope AND register its blob in the fake storage, so the
    declared blob_sha256/blob_bytes match what /raw/blobs will serve."""
    if register_blob:
        fake_storage.add_blob(blob_ref, audio)
    return {
        "contract": "C1",
        "version": "0",
        "user_id": user_id,
        "device_id": device_id,
        "stream_id": stream_id,
        "sequence": sequence,
        "chunk_id": chunk_id,
        "modality": modality,
        "codec": codec,
        "t_start": t_start,
        "t_end": t_end,
        "blob_ref": blob_ref,
        "blob_sha256": hashlib.sha256(audio).hexdigest(),
        "blob_bytes": len(audio),
    }
