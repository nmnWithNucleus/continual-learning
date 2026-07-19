"""Recording's half of the async /ingest seam (decided jointly with data-processing).

When DP runs async /ingest it ACKs ``202 {ok, accepted:true, chunk_id}`` with NO
record_ids — provenance is optional-at-accept. Recording must:
  * tolerate the ack-without-record_ids (no crash; empty provenance);
  * record it as ACCEPTED (dp_acked=0, dp_state='accepted'), NOT confirmed — so the gap
    report's ``dp_acked=1 ⇔ C2 written`` invariant survives;
  * read the session as 'recording' (in-flight) while chunks are accepted-unconfirmed,
    'clean' once DP's /continuity reports them processed, and 'gaps' if DP dead-letters
    one — never a silent 'clean' for a lost chunk.

Uses ffmpeg to build a tiny real audio segment (single stream) and drives it through the
real capture router in SYNC mode, so by the time post_segment returns the chunk is
finalized — deterministic, no polling.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import clients
from app.ids import new_ulid
from tests.fakes import FakeDataProcessingM1, FakeStorage

STORAGE_URL = "http://storage.mock"
DP_URL = "http://dp.mock"
FFMPEG_BIN = shutil.which("ffmpeg")
FFPROBE_BIN = shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(
    FFMPEG_BIN is None or FFPROBE_BIN is None, reason="ffmpeg/ffprobe not on PATH"
)
BASE = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


def _span(seq: int) -> tuple[str, str]:
    start = BASE + timedelta(seconds=10 * seq)
    return start.isoformat().replace("+00:00", "Z"), \
        (start + timedelta(seconds=10)).isoformat().replace("+00:00", "Z")


class FakeDataProcessingAsync(FakeDataProcessingM1):
    """DP running ASYNC /ingest: 202 accept (no record_ids). /continuity derives from
    received envelopes but is overridable (set processed / dead_lettered per stream).

    ``done`` flips /ingest to the DONE-dedup-hit shape (200 + record_ids) — modelling a
    chunk DP already processed on its worker, so a re-push short-circuits (D16 re-drive)."""

    done = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.startswith("/continuity/"):
            return self._continuity(request.url.path.rsplit("/", 1)[-1])
        envelope = json.loads(request.content)
        chunk_id = envelope["chunk_id"]
        self.post_count += 1
        self.events.append(("POST", chunk_id))
        self.envelopes.append(envelope)
        if self.done:  # done-claim short-circuit: 200 + deterministic record_ids
            self.records.setdefault(chunk_id, [f"{chunk_id}-rec"])
            return httpx.Response(200, json={"ok": True, "record_ids": self.records[chunk_id]})
        return httpx.Response(202, json={"ok": True, "accepted": True, "chunk_id": chunk_id})


@pytest.fixture(scope="session")
def audio_segment_bytes(tmp_path_factory) -> bytes:
    path = tmp_path_factory.mktemp("media") / "audio.mp4"
    subprocess.run(
        [FFMPEG_BIN, "-v", "error", "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:sample_rate=16000",
         "-t", "2", "-c:a", "aac", str(path)],
        check=True, capture_output=True,
    )
    return path.read_bytes()


@pytest.fixture()
def wiring(monkeypatch, tmp_path):
    var = tmp_path / "var"
    monkeypatch.setenv("RECORDING_VAR_DIR", str(var))
    monkeypatch.setenv("RECORDING_INGEST_SYNC", "1")   # deterministic: emit before ack
    monkeypatch.setenv("RECORDING_RETRY_BACKOFF", "0")
    monkeypatch.setenv("STORAGE_URL", STORAGE_URL)
    monkeypatch.setenv("DP_URL", DP_URL)

    events: list = []
    storage = FakeStorage(events)
    dp = FakeDataProcessingAsync(events)

    def fake_async_client(base_url: str, timeout: float) -> httpx.AsyncClient:
        handler = storage if "storage" in base_url else dp
        return httpx.AsyncClient(base_url=base_url, timeout=timeout,
                                 transport=httpx.MockTransport(handler))

    monkeypatch.setattr(clients, "async_client", fake_async_client)
    from app.main import app
    client = TestClient(app)
    with client:
        yield client, storage, dp, var


def _post_audio(client, sid, seq, data):
    t_start, t_end = _span(seq)
    return client.post("/capture/segments", params={
        "session_id": sid, "seq": seq, "user_id": "beta-user", "device_id": "phone",
        "t_start": t_start, "t_end": t_end, "mime": "audio/mp4",
        "sha256": hashlib.sha256(data).hexdigest(),
    }, content=data, headers={"content-type": "application/octet-stream"})


def _report(client, sid) -> dict:
    r = client.get(f"/capture/sessions/{sid}/report")
    assert r.status_code == 200, r.text
    return r.json()


@needs_ffmpeg
def test_202_accept_recorded_as_accepted_not_confirmed(wiring, audio_segment_bytes):
    client, storage, dp, var = wiring
    sid = new_ulid()
    assert _post_audio(client, sid, 0, audio_segment_bytes).status_code == 200
    client.post(f"/capture/sessions/{sid}/end", json={"last_seq": 0})

    # Ledger: the chunk is ACCEPTED, not confirmed — invariant dp_acked=1 ⇔ C2 written.
    conn = sqlite3.connect(var / "ledger.db"); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT dp_acked, dp_state, record_ids FROM chunks").fetchone()
    assert row["dp_acked"] == 0 and row["dp_state"] == "accepted"
    assert json.loads(row["record_ids"]) == []      # provenance optional-at-accept

    # Report: in-flight -> 'recording', never a premature 'clean'.
    report = _report(client, sid)
    assert report["verdict"] == "recording"
    leg = report["emit_leg"][0]
    assert leg["accepted_unconfirmed"] == 1
    assert leg["chunks_emitted"] == 0                # not yet CONFIRMED
    # The public dp block keeps its frozen 5-key shape (no new keys leak in).
    assert set(leg["dp"]) == {"checked", "max_sequence", "missing",
                              "missing_unacked", "duplicate_deliveries"}


@needs_ffmpeg
def test_processed_confirmation_flips_to_clean(wiring, audio_segment_bytes):
    client, storage, dp, var = wiring
    sid = new_ulid()
    _post_audio(client, sid, 0, audio_segment_bytes)
    client.post(f"/capture/sessions/{sid}/end", json={"last_seq": 0})

    stream_id = _report(client, sid)["emit_leg"][0]["stream_id"]
    # DP now reports the chunk PROCESSED (C2 written).
    dp.continuity_overrides[stream_id] = {
        "stream_id": stream_id, "max_sequence": 0, "missing": [],
        "processed": [[0, 0]], "dead_lettered": [], "duplicate_deliveries": 0,
    }
    report = _report(client, sid)
    assert report["verdict"] == "clean"
    assert report["emit_leg"][0]["accepted_unconfirmed"] == 0
    assert report["emit_leg"][0]["chunks_emitted"] == 1

    # Confirmation persisted (survives a DP restart that empties its processed set).
    conn = sqlite3.connect(var / "ledger.db"); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT dp_acked, dp_state FROM chunks").fetchone()
    assert row["dp_acked"] == 1 and row["dp_state"] == "processed"
    dp.continuity_overrides.clear()  # DP "restarted" — processed set gone
    assert _report(client, sid)["verdict"] == "clean"  # still clean (durable receipt)


@pytest.fixture()
def inline_wiring(monkeypatch, tmp_path):
    """DP in INLINE mode: /ingest returns 200 {record_ids} (the pre-slice shape)."""
    var = tmp_path / "var"
    monkeypatch.setenv("RECORDING_VAR_DIR", str(var))
    monkeypatch.setenv("RECORDING_INGEST_SYNC", "1")
    monkeypatch.setenv("RECORDING_RETRY_BACKOFF", "0")
    monkeypatch.setenv("STORAGE_URL", STORAGE_URL)
    monkeypatch.setenv("DP_URL", DP_URL)
    events: list = []
    storage = FakeStorage(events)
    dp = FakeDataProcessingM1(events)  # 200 + record_ids

    def fake_async_client(base_url, timeout):
        handler = storage if "storage" in base_url else dp
        return httpx.AsyncClient(base_url=base_url, timeout=timeout,
                                 transport=httpx.MockTransport(handler))

    monkeypatch.setattr(clients, "async_client", fake_async_client)
    from app.main import app
    client = TestClient(app)
    with client:
        yield client, storage, dp, var


@needs_ffmpeg
def test_inline_ack_is_confirmed_and_processed_report_is_noop(inline_wiring, audio_segment_bytes):
    client, storage, dp, var = inline_wiring
    sid = new_ulid()
    _post_audio(client, sid, 0, audio_segment_bytes)
    client.post(f"/capture/sessions/{sid}/end", json={"last_seq": 0})

    # Inline 200 -> CONFIRMED immediately (dp_acked=1, dp_state='processed').
    conn = sqlite3.connect(var / "ledger.db"); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT dp_acked, dp_state FROM chunks").fetchone()
    assert row["dp_acked"] == 1 and row["dp_state"] == "processed"

    report = _report(client, sid)
    assert report["verdict"] == "clean"
    assert report["emit_leg"][0]["accepted_unconfirmed"] == 0

    # A later /continuity that also lists it 'processed' must be a confirm_chunk NO-OP
    # (confirm only touches dp_state='accepted' rows) — verdict stays clean, no error.
    stream_id = report["emit_leg"][0]["stream_id"]
    dp.continuity_overrides[stream_id] = {
        "stream_id": stream_id, "max_sequence": 0, "missing": [],
        "processed": [[0, 0]], "dead_lettered": [], "duplicate_deliveries": 0,
    }
    assert _report(client, sid)["verdict"] == "clean"
    conn = sqlite3.connect(var / "ledger.db"); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT dp_acked, dp_state FROM chunks").fetchone()
    assert row["dp_acked"] == 1 and row["dp_state"] == "processed"


@needs_ffmpeg
def test_redrive_confirms_accepted_chunks(wiring, audio_segment_bytes):
    """D16 ratification condition: the re-drive path for accepted-unconfirmed chunks is
    named + drilled. Accept a chunk (202 -> 'recording'); DP then processes it on its
    worker; POST /redrive re-pushes the original C1, hits DP's done-claim (200+record_ids),
    and CONFIRMS it -> 'clean'. This is the documented way back from a post-queue-loss
    'recording' verdict before M7's durable journal lands."""
    client, storage, dp, var = wiring
    sid = new_ulid()
    _post_audio(client, sid, 0, audio_segment_bytes)
    client.post(f"/capture/sessions/{sid}/end", json={"last_seq": 0})
    assert _report(client, sid)["verdict"] == "recording"  # accepted, unconfirmed

    # DP finished processing on its worker; a re-push now short-circuits to a done-claim.
    dp.done = True
    resp = client.post(f"/capture/sessions/{sid}/redrive")
    assert resp.status_code == 200
    body = resp.json()
    assert body["redriven"] == 1 and body["confirmed"] == 1 and body["still_accepted"] == 0

    # Ledger confirmed; verdict now clean.
    conn = sqlite3.connect(var / "ledger.db"); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT dp_acked, dp_state FROM chunks").fetchone()
    assert row["dp_acked"] == 1 and row["dp_state"] == "processed"
    assert _report(client, sid)["verdict"] == "clean"


@needs_ffmpeg
def test_redrive_leaves_still_pending_accepted(wiring, audio_segment_bytes):
    """A re-drive while DP is STILL processing (re-ACKs 202) leaves the chunk accepted —
    verdict stays 'recording', no false confirm."""
    client, storage, dp, var = wiring
    sid = new_ulid()
    _post_audio(client, sid, 0, audio_segment_bytes)
    client.post(f"/capture/sessions/{sid}/end", json={"last_seq": 0})
    # dp.done stays False -> re-push re-ACKs 202.
    body = client.post(f"/capture/sessions/{sid}/redrive").json()
    assert body["redriven"] == 1 and body["confirmed"] == 0 and body["still_accepted"] == 1
    assert _report(client, sid)["verdict"] == "recording"


@needs_ffmpeg
def test_dead_letter_reads_as_gaps(wiring, audio_segment_bytes):
    client, storage, dp, var = wiring
    sid = new_ulid()
    _post_audio(client, sid, 0, audio_segment_bytes)
    client.post(f"/capture/sessions/{sid}/end", json={"last_seq": 0})

    stream_id = _report(client, sid)["emit_leg"][0]["stream_id"]
    # DP dead-lettered the chunk (accepted, then processing failed terminally).
    dp.continuity_overrides[stream_id] = {
        "stream_id": stream_id, "max_sequence": 0, "missing": [],
        "processed": [], "dead_lettered": [[0, 0]], "duplicate_deliveries": 0,
    }
    report = _report(client, sid)
    assert report["verdict"] == "gaps"           # visible loss, never silent 'clean'
    assert report["emit_leg"][0]["dead_lettered"] == [0]
