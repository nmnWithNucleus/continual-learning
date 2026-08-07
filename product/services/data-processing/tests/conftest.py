"""Shared fixtures for the v1 suite.

The service under test runs MOCK DIALECTS: stage sets whose backends are
client-level fakes, selected the same way real backends are — in code, named in
the version string (mock-dialect rule). ``mock_registry`` installs the default mock audio
set into a clean stage registry (a test module can override the fixture to
install its own set); ``client`` builds the app on top of it with the
MockTransport fake storage and an isolated journal dir.

``FakeGraphProcessor`` is the processor-seam fake for the queue/journal/fairness
suites: canned slots produced by a SYNC callable run in the threadpool, so prior
blocking-gate test idioms (threading.Event) keep working without deadlocking the
event loop.
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable, Optional

import pytest
from fastapi.testclient import TestClient
from starlette.concurrency import run_in_threadpool

from app.stagegraph import stage as stage_mod
from app.stagegraph.executor import GraphResult
from app.stagegraph.stage import Backend, Stage, StageOutput

from .fake_storage import FakeStorage

# Arbitrary bytes standing in for a raw audio chunk. Mock backends ignore the
# bytes entirely, so real audio isn't needed to exercise the loop.
SAMPLE_AUDIO = b"RIFF\x00\x00\x00\x00WAVEfmt mock-audio-chunk-bytes"

# The default mock audio dialect (one stage). Mock fixtures are deliberately
# DISTINCT from any real-backend golden (the carry-over).
MOCK_AUDIO_PV = "asr.v1-mock.v1"


class MockAsrStage(Stage):
    """The default mock asr: canned transcript + one split spanning the chunk —
 a pure function of the C1 envelope (L1), no client, no server."""

    name = "asr"
    modality = "audio"
    stage_version = 1
    backend = Backend("mock", 1)
    byte_budget = 8192
    consumer = "speculative:test_fixture"

    def run_sync(self, ctx) -> StageOutput:
        return StageOutput(value={
            "language": "zx",
            "value": f"mock transcript for {ctx.c1['chunk_id']}",
            "splits": [{
                "t_start": ctx.c1["t_start"],
                "t_end": ctx.c1["t_end"],
                "value": f"mock transcript for {ctx.c1['chunk_id']}",
            }],
        })


def install_mock_audio_registry(monkeypatch) -> dict:
    """Empty the stage registry (discovery disabled — real stage files must never
 leak into a mock dialect) and register the default mock audio set."""
    monkeypatch.setattr(stage_mod, "_REGISTRY", {})
    monkeypatch.setattr(stage_mod, "_discovered", True)
    stage_mod.register_stage(MockAsrStage)
    return stage_mod._REGISTRY


@pytest.fixture()
def mock_registry(monkeypatch) -> dict:
    """Override this fixture in a test module to run a different mock dialect."""
    return install_mock_audio_registry(monkeypatch)


@pytest.fixture()
def fake_storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture()
def client(monkeypatch, fake_storage, tmp_path, mock_registry) -> TestClient:
    """A fresh data-processing app on the mock dialect, with its storage client
 bound to the MockTransport fake (env read at create_app() time). DP_VAR_DIR is
 isolated per test so the durable journal (var/dp.db) never leaks state between
 tests or into the repo tree."""
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / "var"))
    from app.main import create_app

    app = create_app()
    # Inject the fake-storage transport into the (already constructed) client.
    app.state.storage._transport = fake_storage.transport()
    with TestClient(app) as c:
        c.fake_storage = fake_storage  # type: ignore[attr-defined]
        yield c


class FakeGraphProcessor:
    """A processor-seam fake (the object ``process_chunk`` drives): canned slots
 from a sync callable, run in the threadpool — prior gate/fail-injection test
 idioms carry over unchanged. The slot map defaults to one mock asr slot."""

    def __init__(
        self,
        modality: str = "audio",
        pv: str = MOCK_AUDIO_PV,
        run: Optional[Callable[[dict, bytes, float], dict]] = None,
    ) -> None:
        self.modality = modality
        self._pv = pv
        self._run = run or (lambda c1, blob, span: {
            "asr": {"version": self._pv.split("+")[0],
                    "value": f"ok {c1['chunk_id']}"}
        })

    def pipeline_version(self) -> str:
        return self._pv

    async def process_async(self, c1, blob, span_seconds, *,
                            clients=None, metrics=None) -> GraphResult:
        slots = await run_in_threadpool(self._run, c1, blob, span_seconds)
        return GraphResult(slots=slots, statuses={k: "ok" for k in slots},
                           pipeline_version=self._pv)


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
