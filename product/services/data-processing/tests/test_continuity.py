"""Continuity detector — tracker unit tests + /continuity endpoints via /ingest.

Unit tests drive ``ContinuityTracker.note`` directly (gap / leading-gap /
out-of-order interval merge / duplicate / conflict / identity). Endpoint tests
drive the same TestClient fixtures as the ingest suite and prove the wiring:
every schema-valid delivery is tracked — the dedup fast path included — an
invalid C1 is NOT, and the reports come back in the pinned shape.
"""
from __future__ import annotations

from app.continuity import ContinuityTracker
from tests.conftest import make_c1


def _note(
    tracker: ContinuityTracker,
    sequence: int,
    chunk_id: str,
    *,
    stream_id: str = "stream-A",
    at: str = "2026-07-18T12:00:00+00:00",
) -> None:
    tracker.note(
        stream_id,
        sequence,
        chunk_id,
        user_id="pilot-user",
        device_id="computer-mic-1",
        modality="audio",
        now_iso=at,
    )


# ---- Tracker unit tests ------------------------------------------------------

def test_dense_stream_has_no_missing():
    t = ContinuityTracker()
    for seq in range(5):
        _note(t, seq, f"chunk-{seq}")
    entry = t.report_stream("stream-A")
    assert entry["max_sequence"] == 4
    assert entry["received"] == 5
    assert entry["missing"] == []
    assert entry["duplicate_deliveries"] == 0
    assert entry["sequence_conflicts"] == 0


def test_gaps_below_max_are_missing():
    t = ContinuityTracker()
    for seq in (0, 1, 3, 7):
        _note(t, seq, f"chunk-{seq}")
    entry = t.report_stream("stream-A")
    assert entry["max_sequence"] == 7
    assert entry["missing"] == [[2, 2], [4, 6]]


def test_leading_gap_when_first_seen_above_zero():
    # Per C1, sequences start at 0 — a stream first seen at 3 has lost [0, 2].
    t = ContinuityTracker()
    _note(t, 3, "chunk-3")
    _note(t, 4, "chunk-4")
    assert t.report_stream("stream-A")["missing"] == [[0, 2]]


def test_out_of_order_arrival_merges_intervals():
    t = ContinuityTracker()
    for seq in (0, 2, 1, 5, 4):  # 1 bridges [0,0]+[2,2]; 4 extends [5,5] down
        _note(t, seq, f"chunk-{seq}")
    entry = t.report_stream("stream-A")
    assert entry["missing"] == [[3, 3]]
    _note(t, 3, "chunk-3")  # closes the last hole
    assert t.report_stream("stream-A")["missing"] == []


def test_duplicate_delivery_same_chunk_id_counted():
    t = ContinuityTracker()
    _note(t, 0, "chunk-0")
    _note(t, 1, "chunk-1")
    _note(t, 0, "chunk-0")  # at-least-once redelivery
    entry = t.report_stream("stream-A")
    assert entry["received"] == 3
    assert entry["duplicate_deliveries"] == 1
    assert entry["sequence_conflicts"] == 0
    assert entry["missing"] == []


def test_sequence_conflict_different_chunk_id_counted_with_sample():
    t = ContinuityTracker()
    _note(t, 0, "chunk-first", at="2026-07-18T12:00:00+00:00")
    _note(t, 0, "chunk-imposter", at="2026-07-18T12:00:01+00:00")
    entry = t.report_stream("stream-A")
    assert entry["sequence_conflicts"] == 1
    assert entry["duplicate_deliveries"] == 0
    assert t.conflict_samples("stream-A") == [
        {
            "sequence": 0,
            "first_chunk_id": "chunk-first",
            "conflicting_chunk_id": "chunk-imposter",
            "at": "2026-07-18T12:00:01+00:00",
        }
    ]


def test_identity_and_first_last_seen():
    t = ContinuityTracker()
    _note(t, 0, "chunk-0", at="2026-07-18T12:00:00+00:00")
    _note(t, 1, "chunk-1", at="2026-07-18T12:00:05+00:00")
    entry = t.report_stream("stream-A")
    assert entry["stream_id"] == "stream-A"
    assert entry["user_id"] == "pilot-user"
    assert entry["device_id"] == "computer-mic-1"
    assert entry["modality"] == "audio"
    assert entry["first_seen"] == "2026-07-18T12:00:00+00:00"
    assert entry["last_seen"] == "2026-07-18T12:00:05+00:00"


def test_streams_are_independent_and_unknown_is_none():
    t = ContinuityTracker()
    _note(t, 0, "chunk-a0", stream_id="stream-A")
    _note(t, 2, "chunk-b2", stream_id="stream-B")
    assert t.report_stream("stream-A")["missing"] == []
    assert t.report_stream("stream-B")["missing"] == [[0, 1]]
    assert t.report_stream("stream-nope") is None
    assert [s["stream_id"] for s in t.report()["streams"]] == ["stream-A", "stream-B"]


# ---- Endpoint tests through /ingest ------------------------------------------

def test_continuity_endpoints_report_dense_stream(client):
    fs = client.fake_storage
    for seq in range(3):
        c1 = make_c1(fs, chunk_id=f"chunk-dense-{seq}", sequence=seq)
        assert client.post("/ingest", json=c1).status_code == 200

    body = client.get("/continuity").json()
    assert len(body["streams"]) == 1
    entry = body["streams"][0]
    assert entry["stream_id"] == "stream-ULID-AAAA"
    assert entry["modality"] == "audio"
    assert entry["user_id"] == "pilot-user"
    assert entry["device_id"] == "computer-mic-1"
    assert entry["max_sequence"] == 2
    assert entry["received"] == 3
    assert entry["missing"] == []
    assert entry["duplicate_deliveries"] == 0
    assert entry["sequence_conflicts"] == 0
    assert entry["first_seen"] and entry["last_seen"]

    # The per-stream endpoint returns the same entry.
    single = client.get("/continuity/stream-ULID-AAAA")
    assert single.status_code == 200
    assert single.json() == entry


def test_continuity_counts_dedup_fast_path_deliveries(client):
    fs = client.fake_storage
    c1 = make_c1(fs, chunk_id="chunk-redeliver", sequence=0)
    assert client.post("/ingest", json=c1).status_code == 200
    assert client.post("/ingest", json=c1).status_code == 200  # dedup fast path

    # Dedup DID absorb the reprocessing (one C2 write) ...
    assert len(fs.record_posts) == 1
    # ... but NOT the continuity signal: both deliveries counted, one as a dup.
    entry = client.get("/continuity/stream-ULID-AAAA").json()
    assert entry["received"] == 2
    assert entry["duplicate_deliveries"] == 1
    assert entry["sequence_conflicts"] == 0


def test_continuity_reports_gap_and_leading_gap(client):
    fs = client.fake_storage
    # Stream A: sequence 1 never arrives.
    for seq in (0, 2):
        c1 = make_c1(fs, chunk_id=f"chunk-gap-{seq}", stream_id="stream-gap", sequence=seq)
        assert client.post("/ingest", json=c1).status_code == 200
    # Stream B: first delivery at 2 -> chunks [0, 1] were lost before we saw any.
    c1 = make_c1(fs, chunk_id="chunk-lead-2", stream_id="stream-lead", sequence=2)
    assert client.post("/ingest", json=c1).status_code == 200

    assert client.get("/continuity/stream-gap").json()["missing"] == [[1, 1]]
    assert client.get("/continuity/stream-lead").json()["missing"] == [[0, 1]]


def test_continuity_flags_sequence_conflict_via_ingest(client):
    fs = client.fake_storage
    a = make_c1(fs, chunk_id="chunk-slot-a", stream_id="stream-conflict", sequence=0)
    b = make_c1(fs, chunk_id="chunk-slot-b", stream_id="stream-conflict", sequence=0)
    assert client.post("/ingest", json=a).status_code == 200
    assert client.post("/ingest", json=b).status_code == 200

    entry = client.get("/continuity/stream-conflict").json()
    assert entry["received"] == 2
    assert entry["sequence_conflicts"] == 1
    assert entry["duplicate_deliveries"] == 0


def test_invalid_c1_is_not_tracked(client):
    bad = make_c1(client.fake_storage, chunk_id="chunk-bad", stream_id="stream-bad")
    del bad["blob_ref"]  # fails the C1 schema gate -> 422
    assert client.post("/ingest", json=bad).status_code == 422

    assert client.get("/continuity").json() == {"streams": []}
    assert client.get("/continuity/stream-bad").status_code == 404


def test_unknown_stream_is_404(client):
    resp = client.get("/continuity/stream-never-seen")
    assert resp.status_code == 404
