"""Async /ingest (INGEST_ASYNC=1): 202 ACK + worker pool, drain, dead-letter, seam.

Hermetic + headless (mock ASR, MockTransport fake storage). The TestClient runs a real
event loop in a portal thread, so background worker tasks advance concurrently with the
test thread — we poll on real state (``_wait``) or exit the ``with`` block (which drains
the queue) before asserting side effects. Covers the design memo's load-bearing points:
sync rejections stay sync, dedup/claim races, transient-retry vs terminal dead-letter,
bounded-queue 503, graceful drain, and the /continuity processed/dead_lettered seam.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.processing.base import ProcessedContent, ProcessedUnit, Processor
from tests.fake_storage import FakeStorage
from tests.conftest import make_c1


def _wait(pred, timeout: float = 5.0, interval: float = 0.01) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


@pytest.fixture()
def async_client(monkeypatch, fake_storage):
    """A data-processing app in ASYNC ingest mode, storage bound to the fake."""
    monkeypatch.setenv("ASR_BACKEND", "mock")
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("INGEST_ASYNC", "1")
    monkeypatch.setenv("INGEST_WORKERS", "3")
    monkeypatch.setenv("INGEST_RETRY_BACKOFF", "0")  # no real backoff sleep in tests
    from app.main import create_app

    app = create_app()
    app.state.storage._transport = fake_storage.transport()
    with TestClient(app) as c:
        c.fake_storage = fake_storage  # type: ignore[attr-defined]
        yield c


# ---- Sync rejections stay synchronous in async mode --------------------------

def test_bad_c1_still_422_before_any_claim(async_client):
    fs = async_client.fake_storage
    bad = make_c1(fs, chunk_id="c-bad")
    del bad["blob_ref"]
    r = async_client.post("/ingest", json=bad)
    assert r.status_code == 422
    # A deterministic rejection is never deferred into the queue/dead-letter.
    assert fs.record_posts == []


def test_bad_json_still_400(async_client):
    r = async_client.post("/ingest", content=b"not json",
                          headers={"content-type": "application/json"})
    assert r.status_code == 400


# ---- Accept 202 + background processing + done-dedup -------------------------

def test_accept_202_then_processed_then_dedup_returns_ids(async_client):
    fs = async_client.fake_storage
    c1 = make_c1(fs, chunk_id="a-async-1")
    r = async_client.post("/ingest", json=c1)
    assert r.status_code == 202
    assert r.json() == {"ok": True, "accepted": True, "chunk_id": "a-async-1"}

    assert _wait(lambda: len(fs.record_posts) == 1), "worker never wrote the C2"
    c2 = fs.record_posts[0]
    from app import schemas
    assert schemas.validate_c2(c2) == []

    # Redelivery of a DONE chunk returns its known record_ids synchronously (200).
    r2 = async_client.post("/ingest", json=c1)
    assert r2.status_code == 200
    assert r2.json()["record_ids"] == [c2["record_id"]]
    # No second /context write — dedup held.
    assert len(fs.record_posts) == 1


def test_continuity_processed_populated_after_processing(async_client):
    fs = async_client.fake_storage
    c1 = make_c1(fs, chunk_id="a-cont-1", stream_id="stream-CONT", sequence=0)
    async_client.post("/ingest", json=c1)
    # Seen-at-accept is immediate; processed follows once the worker writes the C2.
    entry = async_client.get("/continuity/stream-CONT").json()
    assert entry["max_sequence"] == 0 and entry["received"] >= 1
    assert _wait(lambda: async_client.get("/continuity/stream-CONT").json()["processed"] == [[0, 0]])
    assert async_client.get("/continuity/stream-CONT").json()["dead_lettered"] == []


def test_graceful_drain_processes_everything(monkeypatch, fake_storage):
    """Posting N chunks then closing the app drains every one (shutdown join)."""
    monkeypatch.setenv("ASR_BACKEND", "mock")
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("INGEST_ASYNC", "1")
    from app.main import create_app

    app = create_app()
    app.state.storage._transport = fake_storage.transport()
    with TestClient(app) as c:
        for i in range(12):
            r = c.post("/ingest", json=make_c1(fake_storage, chunk_id=f"drain-{i}",
                                               stream_id="s-drain", sequence=i))
            assert r.status_code == 202
    # Block exited -> drain_and_close joined the queue: all 12 written.
    assert len(fake_storage.record_posts) == 12


# ---- Terminal failure -> dead-letter (visible, never silent) ----------------

def test_missing_blob_dead_letters_and_is_visible(async_client):
    fs = async_client.fake_storage
    # No blob registered -> get_blob 404 -> TERMINAL -> dead-letter (not retried forever).
    c1 = make_c1(fs, chunk_id="dl-1", stream_id="s-dl", sequence=0,
                 blob_ref="raw/never.wav", register_blob=False)
    r = async_client.post("/ingest", json=c1)
    assert r.status_code == 202  # accepted; failure surfaces on the worker

    assert _wait(lambda: async_client.get("/continuity/s-dl").json()["dead_lettered"] == [[0, 0]]), \
        "terminal failure never surfaced as dead_lettered"
    entry = async_client.get("/continuity/s-dl").json()
    assert entry["processed"] == []  # nothing written
    assert fs.record_posts == []
    # The dead-letter counter is exposed for the dashboard.
    metrics = async_client.get("/metrics").text
    assert 'dp_dead_letter_total{modality="audio"} 1' in metrics


class _FlakyProcessor(Processor):
    """Raises an UNEXPECTED (non-ProcessingError) error the first N process() calls, then
    succeeds — models an infra hiccup (model cold-load 503, CUDA OOM, ffmpeg RuntimeError)."""

    modality = "audio"
    content_kind = "transcript"

    def __init__(self, fail_times: int) -> None:
        self._left = fail_times
        self.calls = 0

    def pipeline_version(self, settings) -> str:
        return "asr-mock-v0"

    def process(self, c1, blob, settings, span_seconds) -> list[ProcessedUnit]:
        self.calls += 1
        if self._left > 0:
            self._left -= 1
            raise RuntimeError("model still warming up (503)")  # NOT a ProcessingError
        return [ProcessedUnit(content=ProcessedContent(kind="transcript", text="ok"))]


def test_unexpected_processor_error_is_retried_not_dead_lettered(monkeypatch, fake_storage):
    """Fix (review #2): an infra error out of the processor is TRANSIENT — inline mode 500s
    → recording retries, so the async worker retries too rather than dead-lettering the
    first blip. Only after INGEST_MAX_RETRIES does it dead-letter."""
    monkeypatch.setenv("ASR_BACKEND", "mock")
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("INGEST_ASYNC", "1")
    monkeypatch.setenv("INGEST_RETRY_BACKOFF", "0")
    monkeypatch.setenv("INGEST_MAX_RETRIES", "3")
    flaky = _FlakyProcessor(fail_times=2)  # fails twice, succeeds on the 3rd attempt
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "get_processor", lambda modality: flaky)
    app = main_mod.create_app()
    app.state.storage._transport = fake_storage.transport()
    with TestClient(app) as c:
        c.post("/ingest", json=make_c1(fake_storage, chunk_id="flaky-1", stream_id="s-fl"))
        assert _wait(lambda: len(fake_storage.record_posts) == 1), "never succeeded after retries"
        entry = c.get("/continuity/s-fl").json()
        assert entry["processed"] == [[0, 0]] and entry["dead_lettered"] == []
        assert flaky.calls == 3  # 2 transient failures + 1 success
        metrics = c.get("/metrics").text
    assert 'dp_ingest_retries_total{modality="audio"} 2' in metrics
    assert "dp_dead_letter_total" not in metrics  # never dead-lettered


def test_transient_blob_failure_retries_then_succeeds(async_client):
    fs = async_client.fake_storage
    c1 = make_c1(fs, chunk_id="tr-1", stream_id="s-tr", sequence=0,
                 blob_ref="raw/flaky.wav")
    fs.fail_blob_next(c1["blob_ref"], 2)  # first 2 GETs 503, then serves the bytes
    r = async_client.post("/ingest", json=c1)
    assert r.status_code == 202

    assert _wait(lambda: len(fs.record_posts) == 1), "transient retry never succeeded"
    entry = async_client.get("/continuity/s-tr").json()
    assert entry["processed"] == [[0, 0]] and entry["dead_lettered"] == []
    metrics = async_client.get("/metrics").text
    assert 'dp_ingest_retries_total{modality="audio"} 2' in metrics


# ---- Concurrency: in-flight duplicate + bounded-queue 503 -------------------

class _GatedProcessor(Processor):
    """A processor that blocks in process() until an Event is set — lets a test hold
    workers busy to exercise in-flight dedup + queue-full backpressure deterministically."""

    modality = "audio"
    content_kind = "transcript"

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.started = 0
        self._lock = threading.Lock()

    def pipeline_version(self, settings) -> str:
        return "asr-mock-v0"

    def process(self, c1, blob, settings, span_seconds) -> list[ProcessedUnit]:
        with self._lock:
            self.started += 1
        self.gate.wait(timeout=10)
        return [ProcessedUnit(content=ProcessedContent(kind="transcript",
                                                       text=f"gated {c1['chunk_id']}"))]


def _gated_app(monkeypatch, fake_storage, *, workers: int, queue_max: int):
    monkeypatch.setenv("ASR_BACKEND", "mock")
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("INGEST_ASYNC", "1")
    monkeypatch.setenv("INGEST_WORKERS", str(workers))
    monkeypatch.setenv("INGEST_QUEUE_MAX", str(queue_max))
    gated = _GatedProcessor()
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "get_processor", lambda modality: gated)
    app = main_mod.create_app()
    app.state.storage._transport = fake_storage.transport()
    return app, gated


def test_inflight_redelivery_acks_202_duplicate_no_double_process(monkeypatch, fake_storage):
    app, gated = _gated_app(monkeypatch, fake_storage, workers=2, queue_max=16)
    with TestClient(app) as c:
        c1 = make_c1(fake_storage, chunk_id="inflight-1")
        r1 = c.post("/ingest", json=c1)
        assert r1.status_code == 202 and r1.json().get("duplicate") is None
        assert _wait(lambda: gated.started == 1)  # worker picked it up, now gate-blocked
        # Redeliver the SAME chunk while it's still in-flight.
        r2 = c.post("/ingest", json=c1)
        assert r2.status_code == 202 and r2.json()["duplicate"] is True
        gated.gate.set()  # release -> the one in-flight completes
    # Only ONE record written despite the redelivery.
    assert len(fake_storage.record_posts) == 1
    assert gated.started == 1


def test_queue_full_returns_503_backpressure(monkeypatch, fake_storage):
    # 1 worker, queue capacity 1: worker holds one, queue holds one, the 3rd is 503.
    app, gated = _gated_app(monkeypatch, fake_storage, workers=1, queue_max=1)
    with TestClient(app) as c:
        a = c.post("/ingest", json=make_c1(fake_storage, chunk_id="qf-a", sequence=0))
        assert a.status_code == 202
        assert _wait(lambda: gated.started == 1)  # 'a' is in the worker (gate-blocked)
        b = c.post("/ingest", json=make_c1(fake_storage, chunk_id="qf-b", sequence=1))
        assert b.status_code == 202  # queued (capacity 1)
        assert _wait(lambda: app.state.ingest_queue.queued() == 1)
        cc = c.post("/ingest", json=make_c1(fake_storage, chunk_id="qf-c", sequence=2))
        assert cc.status_code == 503  # queue full -> honest backpressure
        assert cc.json() == {"ok": False, "error": "ingest queue full"}
        gated.gate.set()  # drain
    assert len(fake_storage.record_posts) == 2  # a + b (c was rejected)
