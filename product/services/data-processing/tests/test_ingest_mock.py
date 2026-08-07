"""End-to-end mock-loop tests for POST /ingest.

Hermetic: the mock audio dialect (client-level fake, no GPU), storage faked via
an httpx MockTransport. Drives the app in-process with FastAPI's TestClient.
Every assertion is on real behavior: the C1 gate, the emitted C2 v1 (schema-valid
+ provenance carried), record_id determinism, dedup (storage POSTed at most
once), and split-times-within-span.
"""
from __future__ import annotations

from app import schemas
from app.pipeline import compute_record_id
from app.timeutil import parse_rfc3339
from tests.conftest import MOCK_AUDIO_PV, make_c1


# ---- Health ------------------------------------------------------------------

def test_health_reports_dialects(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    # /health is a liveness probe (not a frozen contract); it additively reports the
    # ingest mode + the resolved dialect per modality + fleet ownership.
    assert resp.json() == {
        "ok": True, "ingest_mode": "inline",
        "pipeline_versions": {"audio": MOCK_AUDIO_PV},
        "supervisor": False,
    }


# ---- C1 validation on ingest -------------------------------------------------

def test_ingest_validates_c1_and_writes_c2(client):
    c1 = make_c1(client.fake_storage)
    # Guard the test: the fixture is itself a valid C1.
    assert schemas.validate_c1(c1) == []

    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    # Audio is 1:1 — exactly one record_id in the list-return response.
    record_ids = body["record_ids"]
    assert len(record_ids) == 1
    record_id = record_ids[0]
    assert record_id

    # Exactly one C2 written to storage, and it is schema-valid.
    fs = client.fake_storage
    assert len(fs.record_posts) == 1
    c2 = fs.record_posts[0]
    assert schemas.validate_c2(c2) == []

    # record_id echoed to the caller == the one persisted.
    assert c2["record_id"] == record_id

    # Provenance + time-spine carried from C1; modality is root-level in v1.
    assert c2["contract"] == "C2" and c2["version"] == "1"
    assert c2["user_id"] == c1["user_id"]
    assert c2["modality"] == c1["modality"]
    assert c2["source"] == {
        "device_id": c1["device_id"],
        "stream_id": c1["stream_id"],
        "chunk_id": c1["chunk_id"],
        "blob_ref": c1["blob_ref"],
    }
    assert c2["t_start"] == c1["t_start"]  # carried verbatim
    assert c2["t_end"] == c1["t_end"]

    # The one record is built from slots; the asr slot references the chunk_id.
    assert c2["pipeline_version"] == MOCK_AUDIO_PV
    asr = c2["content"]["slots"]["asr"]
    assert asr["version"] == MOCK_AUDIO_PV
    assert c1["chunk_id"] in asr["value"]
    # Dead concepts never ride the wire.
    for dead in ("enrichments", "discriminator", "processed_at"):
        assert dead not in c2

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
    a = compute_record_id("chunk-xyz", "asr.v1-mock.v1")
    b = compute_record_id("chunk-xyz", "asr.v1-mock.v1")
    assert a == b

    # A pipeline_version bump forks a NEW id (version-forward reprocessing).
    c = compute_record_id("chunk-xyz", "asr.v1-fw.v1")
    assert c != a

    # A different chunk_id also yields a different id.
    d = compute_record_id("chunk-other", "asr.v1-mock.v1")
    assert d != a

    # URL-safe: hex only.
    assert all(ch in "0123456789abcdef" for ch in a)


def test_emitted_record_id_matches_deterministic_function(client):
    c1 = make_c1(client.fake_storage, chunk_id="chunk-determ")
    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 200
    expected = compute_record_id("chunk-determ", MOCK_AUDIO_PV)
    assert resp.json()["record_ids"] == [expected]


# ---- Dedup on chunk_id -------------------------------------------------------

def test_redelivery_same_chunk_id_is_idempotent(client):
    fs = client.fake_storage
    c1 = make_c1(fs, chunk_id="chunk-dup")

    r1 = client.post("/ingest", json=c1)
    r2 = client.post("/ingest", json=c1)  # exact redelivery (at-least-once)
    assert r1.status_code == r2.status_code == 200

    # Same record_ids both times.
    assert r1.json()["record_ids"] == r2.json()["record_ids"]

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
    splits = c2["content"]["slots"]["asr"]["splits"]
    assert len(splits) >= 1

    chunk_start = parse_rfc3339(c1["t_start"])
    chunk_end = parse_rfc3339(c1["t_end"])
    for split in splits:
        s_start = parse_rfc3339(split["t_start"])
        s_end = parse_rfc3339(split["t_end"])
        assert chunk_start <= s_start <= s_end <= chunk_end
