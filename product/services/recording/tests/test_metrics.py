"""D9 /metrics for recording: baseline HTTP + capture-health (ledger-derived) +
downstream retry counter + segment emit latency. Integration through the real router."""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app import clients
from app.ids import new_ulid
from app.metrics import METRICS
from tests.fakes import FakeDataProcessingM1, FakeStorage

FFMPEG_BIN = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG_BIN is None, reason="ffmpeg not on PATH")
BASE = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def audio_bytes(tmp_path_factory) -> bytes:
    path = tmp_path_factory.mktemp("m") / "a.mp4"
    subprocess.run([FFMPEG_BIN, "-v", "error", "-y", "-f", "lavfi",
                    "-i", "sine=frequency=440:sample_rate=16000", "-t", "2",
                    "-c:a", "aac", str(path)], check=True, capture_output=True)
    return path.read_bytes()


def _wire(monkeypatch, tmp_path, *, storage_fail_first=False):
    monkeypatch.setenv("RECORDING_VAR_DIR", str(tmp_path / "var"))
    monkeypatch.setenv("RECORDING_INGEST_SYNC", "1")
    monkeypatch.setenv("RECORDING_RETRY_BACKOFF", "0")
    monkeypatch.setenv("STORAGE_URL", "http://storage.mock")
    monkeypatch.setenv("DP_URL", "http://dp.mock")
    events: list = []
    storage = FakeStorage(events, fail_first=storage_fail_first)
    dp = FakeDataProcessingM1(events)

    def fake_async_client(base_url, timeout):
        handler = storage if "storage" in base_url else dp
        return httpx.AsyncClient(base_url=base_url, timeout=timeout,
                                 transport=httpx.MockTransport(handler))

    monkeypatch.setattr(clients, "async_client", fake_async_client)
    from app.main import app
    return app


def _post(client, sid, seq, data):
    start = BASE + timedelta(seconds=10 * seq)
    return client.post("/capture/segments", params={
        "session_id": sid, "seq": seq, "user_id": "u", "device_id": "d",
        "t_start": start.isoformat().replace("+00:00", "Z"),
        "t_end": (start + timedelta(seconds=10)).isoformat().replace("+00:00", "Z"),
        "mime": "audio/mp4", "sha256": hashlib.sha256(data).hexdigest(),
    }, content=data, headers={"content-type": "application/octet-stream"})


@needs_ffmpeg
def test_metrics_reports_http_and_capture_health(monkeypatch, tmp_path, audio_bytes):
    METRICS.reset()
    app = _wire(monkeypatch, tmp_path)
    with TestClient(app) as client:
        sid = new_ulid()
        assert _post(client, sid, 0, audio_bytes).status_code == 200
        text = client.get("/metrics").text

    # Baseline HTTP metrics (route-templated, not per-session).
    assert "# TYPE rec_http_requests_total counter" in text
    assert 'path="/capture/segments"' in text
    # Capture-health, ledger-derived (one audio segment emitted -> one audio chunk).
    assert 'rec_segments{state="emitted"} 1' in text
    assert 'rec_chunks{modality="audio"} 1' in text
    assert "rec_sessions_total 1" in text
    # dp_state gauge present (inline ack -> processed).
    assert 'rec_chunks_dp_state{dp_state="processed"} 1' in text
    # Emit latency histogram recorded.
    assert "rec_segment_emit_latency_seconds_count 1" in text


@needs_ffmpeg
def test_metrics_survives_a_broken_ledger_source(monkeypatch, tmp_path, audio_bytes):
    """Invariant 6: a gauge source that raises must NOT break the scrape — the HTTP
    families (and every other source) still render, /metrics stays 200."""
    METRICS.reset()
    app = _wire(monkeypatch, tmp_path)
    with TestClient(app) as client:
        _post(client, new_ulid(), 0, audio_bytes)
        import app.main as main_mod
        from app import ledger as ledger_mod

        def _boom(self):
            raise RuntimeError("ledger unavailable")

        monkeypatch.setattr(ledger_mod.Ledger, "metrics_snapshot", _boom)
        main_mod._snap_cache.clear()  # force the broken source to actually run

        resp = client.get("/metrics")
        assert resp.status_code == 200
        # HTTP metrics still render; the broken ledger-derived families are simply absent.
        assert "rec_http_requests_total" in resp.text
        assert "rec_segments" not in resp.text


@needs_ffmpeg
def test_metrics_counts_downstream_retry(monkeypatch, tmp_path, audio_bytes):
    METRICS.reset()
    # Storage 503s once (stored, ack lost) -> the client retries -> counter increments.
    app = _wire(monkeypatch, tmp_path, storage_fail_first=True)
    with TestClient(app) as client:
        _post(client, new_ulid(), 0, audio_bytes)
        text = client.get("/metrics").text
    assert 'rec_downstream_retries_total{service="storage"} 1' in text
