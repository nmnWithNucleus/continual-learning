"""Blob-leg edge cases: integrity mismatch and a since-deleted blob.

Both must fail cleanly (502, no C2 written) rather than persisting a corrupt or
phantom record — and, crucially, must NOT mark the chunk_id done, so an
at-least-once retry can still reprocess it once the blob is healthy.
"""
from __future__ import annotations

from tests.conftest import make_c1


def test_blob_sha256_mismatch_is_502_no_record(client):
    fs = client.fake_storage
    c1 = make_c1(fs, chunk_id="chunk-tamper", blob_ref="raw/tamper.wav")
    # Tamper: serve different bytes than the C1's declared sha256.
    fs.blobs[c1["blob_ref"]] = b"totally-different-bytes"

    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 502
    assert "mismatch" in resp.json()["detail"]["error"]
    assert fs.record_posts == []  # nothing written on an integrity failure


def test_missing_blob_is_502_and_retryable(client):
    fs = client.fake_storage
    # Build C1 but do NOT register the blob (simulates a since-deleted / not-landed ref).
    c1 = make_c1(fs, chunk_id="chunk-gone", blob_ref="raw/gone.wav", register_blob=False)

    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 502
    assert resp.json()["detail"]["status"] == 404
    assert fs.record_posts == []

    # Not marked done: once the blob is healthy, a retry processes it (idempotent).
    fs.add_blob(c1["blob_ref"], b"now-here-bytes")
    c1["blob_sha256"] = __import__("hashlib").sha256(b"now-here-bytes").hexdigest()
    c1["blob_bytes"] = len(b"now-here-bytes")
    retry = client.post("/ingest", json=c1)
    assert retry.status_code == 200
    assert len(fs.record_posts) == 1
