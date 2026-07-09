"""/raw blob leg (C1): PUT->GET round-trip, sha256 integrity, idempotency on chunk_id, 404s.

Mirrors recording's blob-first write: recording PUTs the chunk bytes (with the integrity
metadata it computed) and gets back an opaque blob_ref; data-processing later GETs the bytes
by that ref for ASR.
"""
from __future__ import annotations

import hashlib

BYTES = b"\x00\x01RIFF....fake wav chunk bytes....\xff\xfe" * 8


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _put(client, data: bytes, *, chunk_id: str, user_id: str = "user-1", **overrides):
    params = {
        "user_id": user_id,
        "device_id": "dev-mic-1",
        "chunk_id": chunk_id,
        "codec": "audio/wav",
        "sha256": _sha(data),
        "bytes": len(data),
    }
    params.update(overrides)
    return client.put(
        "/raw/blobs",
        params=params,
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )


def test_put_get_roundtrip_and_sha(client):
    resp = _put(client, BYTES, chunk_id="chunk-A")
    assert resp.status_code == 200
    ack = resp.json()
    assert ack["bytes"] == len(BYTES)
    assert ack["sha256"] == _sha(BYTES)
    assert ack["blob_ref"]  # opaque, non-empty
    assert "/" in ack["blob_ref"]  # ref may (and here does) contain '/'

    # GET the bytes back by ref (ref is a query param, NOT a path segment).
    got = client.get("/raw/blobs", params={"ref": ack["blob_ref"]})
    assert got.status_code == 200
    assert got.content == BYTES
    assert _sha(got.content) == ack["sha256"]


def test_idempotent_on_chunk_id_same_ref_no_dup(client):
    r1 = _put(client, BYTES, chunk_id="chunk-dup")
    r2 = _put(client, BYTES, chunk_id="chunk-dup")
    assert r1.status_code == r2.status_code == 200
    # Re-PUT of the same chunk_id -> same blob_ref (idempotent), no second blob.
    assert r1.json()["blob_ref"] == r2.json()["blob_ref"]

    store = client.app.state.store
    with store._connect() as conn:
        n_rows = conn.execute(
            "SELECT COUNT(*) c FROM raw_blobs WHERE chunk_id = ?", ("chunk-dup",)
        ).fetchone()["c"]
    assert n_rows == 1  # exactly one index row for the chunk
    # And exactly one file on disk for that ref.
    ref = r1.json()["blob_ref"]
    assert (store.raw_root / ref).exists()


def test_distinct_chunks_get_distinct_refs(client):
    a = _put(client, BYTES, chunk_id="chunk-1").json()
    b = _put(client, b"different bytes entirely", chunk_id="chunk-2").json()
    assert a["blob_ref"] != b["blob_ref"]


def test_sha256_mismatch_is_422(client):
    # Declare a wrong sha256 -> integrity check fails, nothing is stored.
    resp = _put(client, BYTES, chunk_id="chunk-bad", sha256="deadbeef" * 8)
    assert resp.status_code == 422
    assert "sha256" in str(resp.json()["detail"]).lower()


def test_bytes_mismatch_is_422(client):
    resp = _put(client, BYTES, chunk_id="chunk-badlen", bytes=len(BYTES) + 1)
    assert resp.status_code == 422


def test_get_unknown_ref_404(client):
    resp = client.get("/raw/blobs", params={"ref": "zz/zz/never-minted"})
    assert resp.status_code == 404


def test_get_since_deleted_blob_404(client):
    # blob_ref is durable in the index, but consumers must tolerate a since-deleted blob
    # (delete-last-N / right-to-be-forgotten). Delete the file; GET must 404, not 500.
    ack = _put(client, BYTES, chunk_id="chunk-del").json()
    store = client.app.state.store
    (store.raw_root / ack["blob_ref"]).unlink()
    resp = client.get("/raw/blobs", params={"ref": ack["blob_ref"]})
    assert resp.status_code == 404


def test_put_requires_core_params(client):
    # chunk_id / user_id / sha256 are required query params.
    assert client.put(
        "/raw/blobs",
        params={"user_id": "u", "chunk_id": "c"},  # no sha256
        content=BYTES,
    ).status_code == 422
