"""End-to-end mock-loop tests for POST /ingest.

Hermetic: mock ASR (no GPU), storage faked via an httpx MockTransport. Drives the
app in-process with FastAPI's TestClient. Every assertion is on real behavior:
the C1 gate, the emitted C2 (schema-valid + provenance carried), record_id
determinism, dedup (storage POSTed at most once), and segment-times-within-span.
"""
from __future__ import annotations

from app import schemas
from app.asr import mock as mock_asr
from app.pipeline import compute_record_id
from app.timeutil import parse_rfc3339
from tests.conftest import make_c1


# ---- Health ------------------------------------------------------------------

def test_health_reports_mock_backend(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "asr_backend": "mock"}


# ---- C1 validation on ingest -------------------------------------------------

def test_ingest_validates_c1_and_writes_c2(client):
    c1 = make_c1(client.fake_storage)
    # Guard the test: the fixture is itself a valid C1.
    assert schemas.validate_c1(c1) == []

    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    record_id = body["record_id"]
    assert record_id

    # Exactly one C2 written to storage, and it is schema-valid.
    fs = client.fake_storage
    assert len(fs.record_posts) == 1
    c2 = fs.record_posts[0]
    assert schemas.validate_c2(c2) == []

    # record_id echoed to the caller == the one persisted.
    assert c2["record_id"] == record_id

    # Provenance + time-spine carried from C1.
    assert c2["contract"] == "C2" and c2["version"] == "0"
    assert c2["user_id"] == c1["user_id"]
    assert c2["source"] == {
        "device_id": c1["device_id"],
        "stream_id": c1["stream_id"],
        "chunk_id": c1["chunk_id"],
        "blob_ref": c1["blob_ref"],
        "modality": c1["modality"],
    }
    assert c2["t_start"] == c1["t_start"]  # carried verbatim
    assert c2["t_end"] == c1["t_end"]

    # Content is an ASR transcript that references the chunk_id; enrichments empty.
    assert c2["content"]["kind"] == "transcript"
    assert c1["chunk_id"] in c2["content"]["text"]
    assert c2["content"]["language"] == "en"
    assert c2["enrichments"] == {"speakers": [], "faces": [], "places": [], "objects": []}
    assert c2["pipeline_version"] == mock_asr.PIPELINE_VERSION
    assert c2["processed_at"]

    # The blob was pulled by ref (query param), exactly once.
    assert fs.blob_gets == [c1["blob_ref"]]


def test_bad_c1_rejected_422_and_nothing_written(client):
    fs = client.fake_storage

    # Missing a required field (blob_ref).
    bad = make_c1(fs, chunk_id="c-missing")
    del bad["blob_ref"]
    r = client.post("/ingest", json=bad)
    assert r.status_code == 422

    # Wrong const.
    bad2 = make_c1(fs, chunk_id="c-const")
    bad2["contract"] = "C2"
    assert client.post("/ingest", json=bad2).status_code == 422

    # Unknown/extra field (additionalProperties: false).
    bad3 = make_c1(fs, chunk_id="c-extra")
    bad3["surprise"] = "nope"
    assert client.post("/ingest", json=bad3).status_code == 422

    # Bad enum (modality).
    bad4 = make_c1(fs, chunk_id="c-enum")
    bad4["modality"] = "hologram"
    assert client.post("/ingest", json=bad4).status_code == 422

    # Negative sequence (minimum: 0).
    bad5 = make_c1(fs, chunk_id="c-seq")
    bad5["sequence"] = -1
    assert client.post("/ingest", json=bad5).status_code == 422

    # A rejected C1 touches neither the blob store nor /context.
    assert fs.blob_gets == []
    assert fs.record_posts == []


# ---- record_id determinism ---------------------------------------------------

def test_record_id_determinism_and_version_sensitivity():
    # Same (chunk_id, pipeline_version) -> byte-identical id, every time.
    a = compute_record_id("chunk-xyz", "asr-mock-v0")
    b = compute_record_id("chunk-xyz", "asr-mock-v0")
    assert a == b

    # A pipeline_version bump forks a NEW id (version-forward reprocessing).
    c = compute_record_id("chunk-xyz", "asr-fw-v0")
    assert c != a

    # A different chunk_id also yields a different id.
    d = compute_record_id("chunk-other", "asr-mock-v0")
    assert d != a

    # URL-safe: hex only.
    assert all(ch in "0123456789abcdef" for ch in a)


def test_emitted_record_id_matches_deterministic_function(client):
    c1 = make_c1(client.fake_storage, chunk_id="chunk-determ")
    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 200
    expected = compute_record_id("chunk-determ", mock_asr.PIPELINE_VERSION)
    assert resp.json()["record_id"] == expected


# ---- Dedup on chunk_id -------------------------------------------------------

def test_redelivery_same_chunk_id_is_idempotent(client):
    fs = client.fake_storage
    c1 = make_c1(fs, chunk_id="chunk-dup")

    r1 = client.post("/ingest", json=c1)
    r2 = client.post("/ingest", json=c1)  # exact redelivery (at-least-once)
    assert r1.status_code == r2.status_code == 200

    # Same record_id both times.
    assert r1.json()["record_id"] == r2.json()["record_id"]

    # Storage POSTed at most once; the blob pulled at most once (fast path skips it).
    assert len(fs.record_posts) == 1
    assert len(fs.blob_gets) == 1


# ---- Segment times fall within the chunk span --------------------------------

def test_segment_times_within_chunk_span(client):
    c1 = make_c1(
        client.fake_storage,
        chunk_id="chunk-span",
        t_start="2026-07-09T12:00:00Z",
        t_end="2026-07-09T12:00:05Z",
    )
    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 200

    c2 = client.fake_storage.record_posts[0]
    segments = c2["content"]["segments"]
    assert len(segments) >= 1

    chunk_start = parse_rfc3339(c1["t_start"])
    chunk_end = parse_rfc3339(c1["t_end"])
    for seg in segments:
        seg_start = parse_rfc3339(seg["t_start"])
        seg_end = parse_rfc3339(seg["t_end"])
        assert chunk_start <= seg_start <= seg_end <= chunk_end
        assert seg["speaker"] is None  # required-nullable, always null in v0
