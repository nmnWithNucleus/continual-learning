"""Capture-wire server behaviour (WS-C): segment upload -> demux -> ledger -> gap
report. Prefix /capture/* (renamed from /ingest/* 2026-07-18 so /ingest stays
uniquely data-processing's C1 receiver; no alias — the absence is asserted at
the bottom of this module).

Same fake pattern as test_capture.py (httpx MockTransport via the clients.async_client
seam) plus the DP M1 continuity surface (FakeDataProcessingM1). ffmpeg-dependent tests
generate one tiny real A/V segment per pytest session and skip cleanly when ffmpeg is
absent; everything ledger/report-shaped runs on garbage bytes (those segments simply
end 'failed', which the assertions never depend on).

RECORDING_VAR_DIR always points into tmp_path so no var/ state touches the repo.
"""
from __future__ import annotations

import hashlib
import io
import shutil
import sqlite3
import subprocess
import time
import wave
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import clients, contracts, timeutil
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


def span(seq: int) -> tuple[str, str]:
    """Deterministic 10s wall-clock span for segment ``seq``."""
    start = BASE + timedelta(seconds=10 * seq)
    return timeutil.rfc3339(start), timeutil.rfc3339(start + timedelta(seconds=10))


# ------------------------------------------------------------------ media fixtures

@pytest.fixture(scope="session")
def av_segment_bytes(tmp_path_factory) -> bytes:
    """~2s self-contained A/V mp4 (testsrc2 + sine), generated once per session."""
    path = tmp_path_factory.mktemp("media") / "av.mp4"
    subprocess.run(
        [FFMPEG_BIN, "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=192x108:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
         "-t", "2", "-c:v", "mpeg4", "-c:a", "aac", str(path)],
        check=True, capture_output=True,
    )
    return path.read_bytes()


@pytest.fixture(scope="session")
def audio_segment_bytes(tmp_path_factory) -> bytes:
    """~2s audio-only mp4 — the camera-toggled-off segment shape."""
    path = tmp_path_factory.mktemp("media") / "audio.mp4"
    subprocess.run(
        [FFMPEG_BIN, "-v", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
         "-t", "2", "-c:a", "aac", str(path)],
        check=True, capture_output=True,
    )
    return path.read_bytes()


# ------------------------------------------------------------------ wiring fixtures

@dataclass
class IngestWiring:
    client: TestClient
    storage: FakeStorage
    dp: FakeDataProcessingM1
    events: list
    var: Path

    def post_segment(
        self,
        session_id: str,
        seq: int,
        data: bytes,
        *,
        mime: str = "video/mp4",
        sha256: str | None = None,
        user_id: str = "beta-user",
        device_id: str = "phone-web-test",
    ) -> httpx.Response:
        t_start, t_end = span(seq)
        params = {
            "session_id": session_id,
            "seq": seq,
            "user_id": user_id,
            "device_id": device_id,
            "t_start": t_start,
            "t_end": t_end,
            "mime": mime,
            "sha256": hashlib.sha256(data).hexdigest() if sha256 is None else sha256,
        }
        return self.client.post(
            "/capture/segments",
            params=params,
            content=data,
            headers={"content-type": "application/octet-stream"},
        )

    def end(self, session_id: str, last_seq: int) -> httpx.Response:
        return self.client.post(f"/capture/sessions/{session_id}/end", json={"last_seq": last_seq})

    def report(self, session_id: str) -> dict:
        resp = self.client.get(f"/capture/sessions/{session_id}/report")
        assert resp.status_code == 200, resp.text
        return resp.json()

    def db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.var / "ledger.db")
        conn.row_factory = sqlite3.Row
        return conn


def _make_ingest_wiring(monkeypatch, tmp_path, *, sync: bool = True) -> IngestWiring:
    var = tmp_path / "var"
    monkeypatch.setenv("RECORDING_VAR_DIR", str(var))
    monkeypatch.setenv("RECORDING_INGEST_SYNC", "1" if sync else "0")
    monkeypatch.setenv("RECORDING_RETRY_BACKOFF", "0")
    monkeypatch.setenv("STORAGE_URL", STORAGE_URL)
    monkeypatch.setenv("DP_URL", DP_URL)

    events: list = []
    storage = FakeStorage(events)
    dp = FakeDataProcessingM1(events)

    def fake_async_client(base_url: str, timeout: float) -> httpx.AsyncClient:
        handler = storage if "storage" in base_url else dp
        return httpx.AsyncClient(
            base_url=base_url, timeout=timeout, transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(clients, "async_client", fake_async_client)

    from app.main import app

    return IngestWiring(client=TestClient(app), storage=storage, dp=dp, events=events, var=var)


@pytest.fixture()
def ingest(monkeypatch, tmp_path) -> IngestWiring:
    """Sync-mode wiring (the endpoint awaits demux+emit); lifespan runs (with-block)."""
    w = _make_ingest_wiring(monkeypatch, tmp_path, sync=True)
    with w.client:
        yield w


@pytest.fixture()
def ingest_async(monkeypatch, tmp_path) -> IngestWiring:
    """Async-mode wiring: ack immediately, emit in the background."""
    w = _make_ingest_wiring(monkeypatch, tmp_path, sync=False)
    with w.client:
        yield w


def _wait(pred, timeout: float = 10.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


# ------------------------------------------------------------- routing / surface

def test_root_redirects_to_client(ingest):
    resp = ingest.client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/client/"


def test_report_unknown_session_404(ingest):
    assert ingest.client.get("/capture/sessions/nope/report").status_code == 404
    assert ingest.client.post("/capture/sessions/nope/retry").status_code == 404
    assert ingest.client.post("/capture/sessions/nope/end", json={"last_seq": 0}).status_code == 404


# --------------------------------------------------------- upload validation legs

def test_empty_body_400(ingest):
    resp = ingest.post_segment(new_ulid(), 0, b"")
    assert resp.status_code == 400


def test_sha256_mismatch_400(ingest):
    sid = new_ulid()
    resp = ingest.post_segment(sid, 0, b"not-a-real-segment", sha256="0" * 64)
    assert resp.status_code == 400
    assert "sha256 mismatch" in resp.text
    # Nothing was recorded: the same seq can still be delivered correctly.
    resp = ingest.post_segment(sid, 0, b"not-a-real-segment")
    assert resp.status_code == 200
    assert resp.json()["status"] == "received"


def test_server_computes_sha_when_param_empty(ingest):
    sid = new_ulid()
    data = b"opaque-bytes-the-client-could-not-hash"
    resp = ingest.post_segment(sid, 0, data, sha256="")
    assert resp.status_code == 200
    with ingest.db() as conn:
        row = conn.execute(
            "SELECT sha256, bytes FROM segments WHERE session_id=? AND seq=0", (sid,)
        ).fetchone()
    assert row["sha256"] == hashlib.sha256(data).hexdigest()
    assert row["bytes"] == len(data)


def test_same_seq_different_sha_409(ingest):
    sid = new_ulid()
    assert ingest.post_segment(sid, 0, b"first-delivery").status_code == 200
    resp = ingest.post_segment(sid, 0, b"DIFFERENT-bytes")
    assert resp.status_code == 409


# ------------------------------------------------------------------- idempotency

@needs_ffmpeg
def test_duplicate_delivery_is_counted_not_reemitted(ingest, av_segment_bytes):
    sid = new_ulid()
    assert ingest.post_segment(sid, 0, av_segment_bytes).json()["status"] == "received"
    puts, posts = ingest.storage.put_count, ingest.dp.post_count
    assert puts > 0 and posts > 0  # the first delivery emitted

    resp = ingest.post_segment(sid, 0, av_segment_bytes)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "session_id": sid, "seq": 0, "status": "duplicate"}
    # No re-spool, no re-emit: downstream call counts are unchanged.
    assert (ingest.storage.put_count, ingest.dp.post_count) == (puts, posts)

    report = ingest.report(sid)
    assert report["client_leg"]["duplicate_deliveries"] == 1


# ------------------------------------------------------------- demux -> C1 streams

@needs_ffmpeg
def test_av_segments_demux_to_two_dense_streams(ingest, av_segment_bytes):
    sid = new_ulid()
    for seq in (0, 1):
        assert ingest.post_segment(sid, seq, av_segment_bytes).status_code == 200
    assert ingest.end(sid, last_seq=1).json() == {"ok": True}

    report = ingest.report(sid)
    assert report["ended"] is True
    assert report["expected_segments"] == 2
    assert report["received_segments"] == 2
    assert report["segment_states"] == {"received": 0, "emitted": 2, "failed": 0}
    assert report["client_leg"] == {
        "missing_seqs": [], "missing_count": 0, "duplicate_deliveries": 0,
        "unterminated": False,
    }
    assert [leg["modality"] for leg in report["emit_leg"]] == ["audio", "video"]
    for leg in report["emit_leg"]:
        assert leg["chunks_emitted"] == 2
        assert leg["last_sequence"] == 1
        assert leg["pending"] == 0 and leg["failed"] == 0
        assert leg["dp"] == {
            "checked": True, "max_sequence": 1, "missing": [],
            "missing_unacked": [], "duplicate_deliveries": 0,
        }
    assert report["verdict"] == "clean"

    # The emitted C1s: schema-valid, dense per stream, segment spans carried through.
    by_stream: dict[str, list[dict]] = {}
    for env in ingest.dp.unique_envelopes():
        assert contracts.c1_errors(env) == [], env
        by_stream.setdefault(env["stream_id"], []).append(env)
    assert len(by_stream) == 2
    codecs = set()
    for envs in by_stream.values():
        assert [e["sequence"] for e in envs] == [0, 1]
        assert [(e["t_start"], e["t_end"]) for e in envs] == [span(0), span(1)]
        codecs.add(envs[0]["codec"])
    assert codecs == {"audio/wav", "video/mp4"}

    # Audio chunks really are 16 kHz mono s16le WAV; video chunks are real MP4.
    for env in ingest.dp.unique_envelopes():
        blob = ingest.storage.contents[env["chunk_id"]]
        if env["codec"] == "audio/wav":
            with wave.open(io.BytesIO(blob)) as wav:
                assert wav.getframerate() == 16000
                assert wav.getnchannels() == 1
                assert wav.getsampwidth() == 2
        else:
            assert blob[4:8] == b"ftyp"

    # Spool + demux scratch are cleaned up after emit (keep_spool defaults off).
    assert list((ingest.var / "spool" / sid).glob("*")) == []
    assert [p for p in (ingest.var / "chunks").rglob("*") if p.is_file()] == []


@needs_ffmpeg
def test_audio_only_segment_makes_single_stream(ingest, audio_segment_bytes, monkeypatch):
    monkeypatch.setenv("RECORDING_KEEP_SPOOL", "1")
    sid = new_ulid()
    assert ingest.post_segment(sid, 0, audio_segment_bytes, mime="audio/mp4").status_code == 200
    ingest.end(sid, last_seq=0)

    report = ingest.report(sid)
    assert report["verdict"] == "clean"
    assert [leg["modality"] for leg in report["emit_leg"]] == ["audio"]
    assert report["emit_leg"][0]["codec"] == "audio/wav"
    assert report["emit_leg"][0]["chunks_emitted"] == 1
    # keep_spool honored: the segment file survives its emit (consent-gate seam).
    assert list((ingest.var / "spool" / sid).glob("0.*")) != []


# ------------------------------------------------- end marker / client-leg gaps

def test_end_marker_idempotent_and_sets_expected(ingest):
    sid = new_ulid()
    ingest.post_segment(sid, 0, b"garbage-segment")
    assert ingest.end(sid, last_seq=0).status_code == 200
    assert ingest.end(sid, last_seq=0).status_code == 200  # idempotent

    sessions = ingest.client.get("/capture/sessions").json()["sessions"]
    entry = next(s for s in sessions if s["session_id"] == sid)
    assert entry["ended"] is True
    assert entry["expected_segments"] == 1
    assert entry["received_segments"] == 1


def test_missing_seqs_hole_and_tail(ingest):
    sid = new_ulid()
    ingest.post_segment(sid, 0, b"garbage-a")
    ingest.post_segment(sid, 2, b"garbage-b")   # seq 1 lost client-side

    report = ingest.report(sid)                  # still recording: hole visible, verdict honest
    assert report["client_leg"]["missing_seqs"] == [1]
    assert report["client_leg"]["unterminated"] is True
    assert report["verdict"] == "recording"

    ingest.end(sid, last_seq=3)                  # seq 3 was captured but never arrived
    report = ingest.report(sid)
    assert report["client_leg"]["missing_seqs"] == [1, 3]
    assert report["client_leg"]["unterminated"] is False
    assert report["verdict"] == "gaps"


def test_sessions_list_covers_all_sessions(ingest):
    a, b = new_ulid(), new_ulid()
    ingest.post_segment(a, 0, b"garbage-1")
    ingest.post_segment(b, 0, b"garbage-2", user_id="u-2")
    ids = {s["session_id"] for s in ingest.client.get("/capture/sessions").json()["sessions"]}
    assert {a, b} <= ids


# --------------------------------------------------- failure, retry, chunk reuse

@needs_ffmpeg
def test_failed_emit_marked_and_retry_reuses_chunk_id(ingest, av_segment_bytes, monkeypatch):
    monkeypatch.setenv("RECORDING_RETRY_ATTEMPTS", "1")   # no client-level retry budget
    ingest.dp.fail_times = 1                              # first /ingest POST -> 503

    sid = new_ulid()
    resp = ingest.post_segment(sid, 0, av_segment_bytes)
    assert resp.status_code == 200                        # ack stands: durably received
    assert resp.json()["status"] == "received"

    with ingest.db() as conn:
        seg = conn.execute(
            "SELECT state, error FROM segments WHERE session_id=? AND seq=0", (sid,)
        ).fetchone()
        chunk = conn.execute(
            "SELECT chunk_id, sequence, dp_acked FROM chunks"
            " WHERE session_id=? AND seq=0 AND modality='audio'", (sid,)
        ).fetchone()
    assert seg["state"] == "failed" and seg["error"]
    # chunk identity was persisted BEFORE the failed attempt.
    assert chunk["dp_acked"] == 0
    chunk_id_before = chunk["chunk_id"]

    ingest.end(sid, last_seq=0)
    report = ingest.report(sid)
    assert report["verdict"] == "gaps"
    audio_leg = next(l for l in report["emit_leg"] if l["modality"] == "audio")
    assert audio_leg["failed"] == 1 and audio_leg["chunks_emitted"] == 0

    # DP recovers; /retry re-enqueues the failure and re-emits with the SAME identity.
    resp = ingest.client.post(f"/capture/sessions/{sid}/retry")
    assert resp.json() == {"ok": True, "session_id": sid, "retried": 1}

    report = ingest.report(sid)
    assert report["verdict"] == "clean"
    with ingest.db() as conn:
        chunk = conn.execute(
            "SELECT chunk_id, sequence, dp_acked FROM chunks"
            " WHERE session_id=? AND seq=0 AND modality='audio'", (sid,)
        ).fetchone()
    assert chunk["chunk_id"] == chunk_id_before
    assert chunk["dp_acked"] == 1
    # Both deliveries of the audio chunk carried the same chunk_id and sequence 0,
    # so DP deduped: one record, no fabricated gap.
    audio_deliveries = [e for e in ingest.dp.envelopes if e["chunk_id"] == chunk_id_before]
    assert len(audio_deliveries) == 2
    assert {e["sequence"] for e in audio_deliveries} == {0}
    assert len(ingest.dp.records[chunk_id_before]) == 1

    resp = ingest.client.post(f"/capture/sessions/{sid}/retry")   # nothing left to retry
    assert resp.json()["retried"] == 0


@needs_ffmpeg
def test_restart_reenqueues_received_and_reuses_chunk_ids(monkeypatch, tmp_path, av_segment_bytes):
    """Ack-then-crash drill: a segment acked (spool+ledger) but never emitted is
    re-enqueued by the next startup's lifespan and re-emits with the persisted ids."""
    w = _make_ingest_wiring(monkeypatch, tmp_path, sync=True)
    monkeypatch.setenv("RECORDING_RETRY_ATTEMPTS", "1")
    sid = new_ulid()

    with w.client:
        w.dp.fail_times = 1
        assert w.post_segment(sid, 0, av_segment_bytes).status_code == 200
        with w.db() as conn:
            chunk_id_before = conn.execute(
                "SELECT chunk_id FROM chunks WHERE session_id=? AND seq=0 AND modality='audio'",
                (sid,),
            ).fetchone()["chunk_id"]
        # Simulate crashing AFTER the ack but BEFORE processing finished: the row
        # goes back to 'received' exactly as an interrupted worker leaves it.
        with w.db() as conn:
            conn.execute(
                "UPDATE segments SET state='received', error=NULL WHERE session_id=?", (sid,)
            )

    w.dp.fail_times = 0
    with TestClient(w.client.app):  # "restart": lifespan re-enqueues pending segments
        def emitted() -> bool:
            with w.db() as conn:
                row = conn.execute(
                    "SELECT state FROM segments WHERE session_id=? AND seq=0", (sid,)
                ).fetchone()
            return row["state"] == "emitted"
        assert _wait(emitted), "reenqueued segment never emitted"

    with w.db() as conn:
        chunk = conn.execute(
            "SELECT chunk_id, dp_acked FROM chunks WHERE session_id=? AND seq=0 AND modality='audio'",
            (sid,),
        ).fetchone()
    assert chunk["chunk_id"] == chunk_id_before   # minted once, reused across restart
    assert chunk["dp_acked"] == 1
    assert len(w.dp.records[chunk_id_before]) == 1


# ------------------------------------------------------------------- async mode

@needs_ffmpeg
def test_async_mode_acks_then_emits_in_background(ingest_async, av_segment_bytes):
    w = ingest_async
    sid = new_ulid()
    for seq in (0, 1):
        resp = w.post_segment(sid, seq, av_segment_bytes)
        assert resp.status_code == 200
        assert resp.json()["status"] == "received"
    w.end(sid, last_seq=1)

    def clean() -> bool:
        return w.report(sid)["verdict"] == "clean"
    assert _wait(clean), f"session never became clean: {w.report(sid)}"

    report = w.report(sid)
    for leg in report["emit_leg"]:
        assert leg["chunks_emitted"] == 2
        assert leg["pending"] == 0 and leg["failed"] == 0


# ------------------------------------------------------------- DP continuity merge

@needs_ffmpeg
def test_report_merges_live_dp_continuity(ingest, av_segment_bytes):
    sid = new_ulid()
    ingest.post_segment(sid, 0, av_segment_bytes)
    ingest.end(sid, last_seq=0)

    report = ingest.report(sid)
    assert report["verdict"] == "clean"
    audio_leg = next(l for l in report["emit_leg"] if l["modality"] == "audio")
    stream_id = audio_leg["stream_id"]
    assert audio_leg["dp"] == {
        "checked": True, "max_sequence": 0, "missing": [],
        "missing_unacked": [], "duplicate_deliveries": 0,
    }

    # DP amnesia (its in-memory tracker restarted mid-session) reports our
    # delivered-and-ACKED sequence as a leading-gap run. The ledger holds the ack
    # receipt, so the report must reconcile — NOT fabricate a permanent 'gaps'
    # verdict for chunks that provably landed.
    ingest.dp.continuity_overrides[stream_id] = {
        "stream_id": stream_id, "max_sequence": 3, "missing": [[0, 0], [2, 3]],
        "duplicate_deliveries": 0,
    }
    report = ingest.report(sid)
    audio_leg = next(l for l in report["emit_leg"] if l["modality"] == "audio")
    # seq 0 is dp_acked in the ledger -> subtracted; 2..3 exceed anything the
    # ledger ever allocated (bogus claim) -> clipped. Nothing truly missing.
    assert audio_leg["dp"] == {
        "checked": True, "max_sequence": 3, "missing": [[0, 0], [2, 3]],
        "missing_unacked": [], "duplicate_deliveries": 0,
    }
    assert report["verdict"] == "clean"

    # DP unreachable: checked:false, and an unchecked stream never fabricates a gap.
    del ingest.dp.continuity_overrides[stream_id]
    ingest.dp.continuity_down = True
    report = ingest.report(sid)
    for leg in report["emit_leg"]:
        assert leg["dp"] == {"checked": False}
    assert report["verdict"] == "clean"


# --------------------------------------------------- review-fix regressions (M1)

@needs_ffmpeg
def test_duplicate_of_lost_spool_self_heals(ingest, av_segment_bytes):
    """Ack contract under a spool-write crash: the retry (a 'duplicate') must land
    the bytes and emit, never ack bytes that exist nowhere server-side."""
    sid = new_ulid()
    assert ingest.post_segment(sid, 0, av_segment_bytes).status_code == 200

    # Simulate the crash aftermath: the ledger row survived but the spooled bytes
    # are gone and the segment was never processed (state forced back to received).
    with ingest.db() as conn:
        row = conn.execute(
            "SELECT spool_path FROM segments WHERE session_id = ? AND seq = 0", (sid,)
        ).fetchone()
        conn.execute(
            "UPDATE segments SET state = 'received' WHERE session_id = ? AND seq = 0",
            (sid,),
        )
    Path(row["spool_path"]).unlink(missing_ok=True)  # gone either way post-"crash"

    resp = ingest.post_segment(sid, 0, av_segment_bytes)  # the client's retry
    assert resp.json()["status"] == "duplicate"
    ingest.end(sid, last_seq=0)
    report = ingest.report(sid)
    assert report["segment_states"]["emitted"] == 1     # re-spooled AND emitted
    assert report["verdict"] == "clean"


def test_segment_body_cap_413(ingest, monkeypatch):
    monkeypatch.setenv("RECORDING_MAX_SEGMENT_MB", "0.001")  # ~1 KB cap
    resp = ingest.post_segment(new_ulid(), 0, b"x" * 4096)
    assert resp.status_code == 413


def test_seq_bound_rejected(ingest):
    resp = ingest.post_segment(new_ulid(), 10_000_000, b"data")
    assert resp.status_code == 422


def test_missing_list_capped_count_exact(ingest):
    """A far-future seq must not balloon the report: the list is capped, the count
    exact, and the report stays fast (the walk is O(received))."""
    sid = new_ulid()
    ingest.post_segment(sid, 0, b"garbage-a")
    ingest.post_segment(sid, 50_000, b"garbage-b")
    report = ingest.report(sid)
    leg = report["client_leg"]
    assert leg["missing_count"] == 49_999
    assert len(leg["missing_seqs"]) == 1000
    assert leg["missing_seqs"][0] == 1 and leg["missing_seqs"][-1] == 1000


@needs_ffmpeg
def test_late_segment_reopens_stale_end_marker(ingest, av_segment_bytes):
    """A pagehide beacon mid-session must not freeze 'expected': segments arriving
    past the marker reopen the session; a newer end marker closes it honestly."""
    sid = new_ulid()
    ingest.post_segment(sid, 0, av_segment_bytes)
    ingest.end(sid, last_seq=0)                     # stale beacon: claims 1 segment
    assert ingest.report(sid)["ended"] is True

    ingest.post_segment(sid, 1, av_segment_bytes)   # recording actually continued
    report = ingest.report(sid)
    assert report["ended"] is False                  # reopened — verdict can't be
    assert report["verdict"] == "recording"          # 'clean' against stale expected

    ingest.end(sid, last_seq=1)                      # the real end marker
    report = ingest.report(sid)
    assert report["ended"] is True
    assert report["expected_segments"] == 2
    assert report["verdict"] == "clean"

    ingest.end(sid, last_seq=0)                      # a LATE stale beacon replays
    assert ingest.report(sid)["expected_segments"] == 2   # monotonic — not lowered


@needs_ffmpeg
def test_partial_emit_failure_keeps_sequence_order(ingest, av_segment_bytes):
    """All of a segment's chunks are allocated before any emits: a mid-emit failure
    plus later segments plus /retry must keep sequence order == capture order."""
    sid = new_ulid()
    ingest.post_segment(sid, 0, av_segment_bytes)
    # Segment 1: the audio chunk's DP push fails terminally (video never reached),
    # but BOTH its chunks must already hold their sequence allocations.
    ingest.dp.fail_times = 10                       # > retry budget
    ingest.post_segment(sid, 1, av_segment_bytes)
    ingest.dp.fail_times = 0
    ingest.post_segment(sid, 2, av_segment_bytes)   # later segment keeps emitting
    ingest.end(sid, last_seq=2)
    assert ingest.report(sid)["verdict"] == "gaps"  # the failure is visible

    resp = ingest.client.post(f"/capture/sessions/{sid}/retry")
    assert resp.json()["retried"] == 1
    report = ingest.report(sid)
    assert report["verdict"] == "clean"
    # Per stream: sequences must follow segment seq order (0,1,2 -> 0,1,2).
    with ingest.db() as conn:
        for stream_id in [l["stream_id"] for l in report["emit_leg"]]:
            rows = conn.execute(
                "SELECT seq, sequence FROM chunks WHERE stream_id = ? ORDER BY seq",
                (stream_id,),
            ).fetchall()
            assert [r["sequence"] for r in rows] == sorted(r["sequence"] for r in rows)
            assert [r["seq"] for r in rows] == [0, 1, 2]


# --------------------------------------------- /ingest is NOT ours (invariant)

def test_no_ingest_routes_exist_on_recording(ingest):
    """/ingest is uniquely data-processing's C1 receiver: recording must serve
    NOTHING under it (a one-day transitional alias was removed 2026-07-19)."""
    paths = ingest.client.get("/openapi.json").json()["paths"]
    assert "/capture/segments" in paths
    assert "/capture/sessions/{session_id}/report" in paths
    assert not any(p.startswith("/ingest") for p in paths)
    assert ingest.client.post("/ingest/segments").status_code == 404
    assert ingest.client.get("/ingest/sessions").status_code == 404
