"""End-to-end capture-session behaviour, driven through POST /capture/run.

Every downstream call hits the in-process fakes (conftest). These assert the frozen
C1 delivery semantics: blob-first, dense zero-based sequence, one stream_id, at-least-
once retry (reusing chunk_id, not advancing sequence), and exactly-once-via-chunk_id
under an injected transient failure.
"""
from __future__ import annotations

import math

import pytest

from app import contracts
from tests.fakes import deterministic_record_id


# --------------------------------------------------------------------------- health

def test_health(wiring):
    resp = wiring.client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ------------------------------------------------------ response shape / stream_id

def test_capture_response_shape_and_single_stream_id(wiring):
    # 12s synthetic @ 5s chunks -> 3 chunks (2 full + 1 short).
    out = wiring.run(sample_seconds=12, chunk_seconds=5)

    assert out["chunks_emitted"] == 3
    assert len(out["chunk_ids"]) == 3
    assert out["sequences"] == [0, 1, 2]
    assert len(out["record_ids"]) == 3

    # ONE globally-unique stream_id for the whole session, on every emitted envelope.
    assert isinstance(out["stream_id"], str) and len(out["stream_id"]) == 26
    stream_ids = {env["stream_id"] for env in wiring.dp.envelopes}
    assert stream_ids == {out["stream_id"]}

    # chunk_ids are unique and are exactly what the envelopes carried, in order.
    assert len(set(out["chunk_ids"])) == 3
    assert [env["chunk_id"] for env in wiring.dp.envelopes] == out["chunk_ids"]

    # record_ids are the (deterministic) ids data-processing returned per chunk.
    assert out["record_ids"] == [
        deterministic_record_id(env) for env in wiring.dp.envelopes
    ]


# ------------------------------------------------------------ C1 schema conformance

def test_emitted_c1_validates_against_schema(wiring):
    wiring.run(sample_seconds=12, chunk_seconds=5)
    assert wiring.dp.envelopes, "expected emitted envelopes"
    for env in wiring.dp.envelopes:
        assert contracts.c1_errors(env) == [], env


def test_emitted_c1_fields(wiring):
    out = wiring.run(sample_seconds=10, chunk_seconds=5, user_id="u-42", device_id="mic-7")
    for seq, env in enumerate(wiring.dp.envelopes):
        assert env["contract"] == "C1"
        assert env["version"] == "0"
        assert env["user_id"] == "u-42"
        assert env["device_id"] == "mic-7"
        assert env["modality"] == "audio"
        assert env["codec"] == "audio/wav"
        assert env["sequence"] == seq
        assert env["stream_id"] == out["stream_id"]
        assert env["chunk_id"] == out["chunk_ids"][seq]
        # blob leg carried through: opaque ref + integrity fields.
        assert env["blob_ref"].endswith(f"{env['chunk_id']}.wav")
        assert env["blob_bytes"] > 0
        assert len(env["blob_sha256"]) == 64  # sha-256 hex


# ---------------------------------------------------------- dense zero-based sequence

def test_sequence_is_dense_zero_based_plus_one(wiring):
    out = wiring.run(sample_seconds=27, chunk_seconds=5)   # ceil(27/5) = 6
    assert out["chunks_emitted"] == 6
    seqs = [env["sequence"] for env in wiring.dp.envelopes]
    assert seqs == [0, 1, 2, 3, 4, 5]                       # dense, zero-based, +1


# ------------------------------------------------------------- blob-first ordering

def test_blob_first_ordering(wiring):
    wiring.run(sample_seconds=12, chunk_seconds=5)
    # Shared timeline across both services: exactly PUT then POST per chunk, in order.
    expected: list = []
    for cid in wiring.storage.blobs:   # dict preserves insert order == chunk order
        expected.append(("PUT", cid))
        expected.append(("POST", cid))
    assert wiring.events == expected
    # And per chunk, the /raw PUT strictly precedes the /ingest POST (blob-first).
    for cid in wiring.storage.blobs:
        assert wiring.events.index(("PUT", cid)) < wiring.events.index(("POST", cid))


# ------------------------------------------------------- ceil(N/K) chunk count

@pytest.mark.parametrize(
    "n_seconds,chunk_seconds",
    [(10, 5), (12, 5), (7, 3), (5, 5), (1, 5), (20, 4), (13, 4)],
)
def test_chunk_count_is_ceil(wiring, n_seconds, chunk_seconds):
    out = wiring.run(sample_seconds=n_seconds, chunk_seconds=chunk_seconds)
    assert out["chunks_emitted"] == math.ceil(n_seconds / chunk_seconds)


# ----------------------------------------------------- wall-clock time-spine

def test_wallclock_spans_are_dense_and_contiguous(wiring):
    wiring.run(sample_seconds=12, chunk_seconds=5, base_wallclock="2026-07-09T12:00:00Z")
    envs = wiring.dp.envelopes
    assert envs[0]["t_start"] == "2026-07-09T12:00:00Z"      # frame-0 == base
    assert [(e["t_start"], e["t_end"]) for e in envs] == [
        ("2026-07-09T12:00:00Z", "2026-07-09T12:00:05Z"),
        ("2026-07-09T12:00:05Z", "2026-07-09T12:00:10Z"),
        ("2026-07-09T12:00:10Z", "2026-07-09T12:00:12Z"),   # final chunk shorter (2s)
    ]
    # Contiguous: each chunk's end is the next chunk's start (recording carves, no gaps).
    for a, b in zip(envs, envs[1:]):
        assert a["t_end"] == b["t_start"]


# ------------------------------- at-least-once: retry reuses chunk_id, holds sequence

def test_retry_reuses_chunk_id_and_holds_sequence(make_wiring):
    w = make_wiring(storage_fail_first=True)   # first /raw PUT 503s (after storing)
    out = w.run(sample_seconds=12, chunk_seconds=5)

    # The transient failure forced exactly one extra PUT (a retry), no extra chunk.
    assert out["chunks_emitted"] == 3
    assert w.storage.put_count == 4            # 3 chunks + 1 retry
    assert len(w.storage.blobs) == 3           # idempotent: no duplicate blob

    # The retry re-issued chunk 0's PUT: same chunk_id twice, back-to-back, before its POST.
    first_cid = out["chunk_ids"][0]
    assert w.events[0] == ("PUT", first_cid)
    assert w.events[1] == ("PUT", first_cid)   # retry reuses the SAME chunk_id
    assert w.events[2] == ("POST", first_cid)  # blob-first still holds

    # Sequence did NOT advance across the retry: chunk 0 is still sequence 0.
    assert [e["sequence"] for e in w.dp.unique_envelopes()] == [0, 1, 2]
    assert out["sequences"] == [0, 1, 2]


# ------------------------- loss/dup drill: exactly-once effect downstream via chunk_id

def test_dup_drill_storage_exactly_once(make_wiring):
    """A transient /raw failure -> retry -> storage dedups on chunk_id: exactly one blob."""
    w = make_wiring(storage_fail_first=True)
    out = w.run(sample_seconds=15, chunk_seconds=5)   # 3 chunks

    assert out["chunks_emitted"] == 3
    assert w.storage.put_count == 4                    # a retry happened
    assert set(w.storage.blobs.keys()) == set(out["chunk_ids"])
    assert len(w.storage.blobs) == 3                    # exactly-once: no dup blob
    # data-processing saw each chunk exactly once (the storage retry didn't re-POST).
    assert w.dp.post_count == 3
    assert len(w.dp.records) == 3


def test_dup_drill_dataprocessing_exactly_once(make_wiring):
    """A transient /ingest failure -> retry -> data-processing dedups on chunk_id."""
    w = make_wiring(dp_fail_first=True)
    out = w.run(sample_seconds=15, chunk_seconds=5)    # 3 chunks

    assert out["chunks_emitted"] == 3
    assert w.dp.post_count == 4                          # 3 chunks + 1 retry
    assert len(w.dp.envelopes) == 4                      # the retry re-sent one envelope
    assert len(w.dp.records) == 3                        # exactly-once: no dup record

    # The retried chunk kept a single, stable record_id; the response carries it.
    first_cid = out["chunk_ids"][0]
    dup_envs = [e for e in w.dp.envelopes if e["chunk_id"] == first_cid]
    assert len(dup_envs) == 2                            # sent twice
    assert {e["sequence"] for e in dup_envs} == {0}      # same sequence, not advanced
    assert out["record_ids"][0] == w.dp.records[first_cid]
    # No duplicate blob either (storage untouched by the /ingest retry).
    assert w.storage.put_count == 3


# ------------------------------------------------------ integrity: sha256 / bytes

def test_blob_sha256_matches_bytes(wiring):
    wiring.run(sample_seconds=10, chunk_seconds=5)
    # Each envelope's blob_sha256 is the sha256 of the stored blob (storage verified size).
    for cid, blob in wiring.storage.blobs.items():
        env = next(e for e in wiring.dp.envelopes if e["chunk_id"] == cid)
        assert env["blob_sha256"] == blob["sha256"]
        assert env["blob_bytes"] == blob["bytes"]
