"""Modality-agnostic Processor seam — all four modalities through one core.

Hermetic: mock/stub transforms (no GPU, no vision/LLM), storage faked via an httpx
MockTransport, driven in-process with FastAPI's TestClient. Every assertion is on
real behavior of the loop the verifier will drive:

  * each committed C1 fixture is itself schema-valid;
  * POST /ingest -> 200 with a record_ids LIST of the expected length
    (audio/image/text -> 1; video -> 3, the 1-chunk-many-records case);
  * every emitted C2 validates against the FROZEN C2 schema;
  * content.kind is the right frozen enum value per modality;
  * each record_id == compute_record_id(chunk_id, pipeline_version, discriminator)
    (deterministic; video's are distinct across keyframes);
  * dedup: re-ingesting the same chunk_id returns the same record_ids and writes
    NO duplicate /context records.
"""
from __future__ import annotations

import pytest

from app import schemas
from app.asr import mock as mock_asr
from app.pipeline import compute_record_id
from app.processing.processors import image as image_proc
from app.processing.processors import text as text_proc
from app.processing.processors import video as video_proc
from tests import fixtures

# modality -> (pipeline_version, [discriminator per record, in emit order])
_EXPECT = {
    "audio": (mock_asr.PIPELINE_VERSION, [""]),
    "image": (image_proc.PIPELINE_VERSION, [""]),
    "video": (video_proc.PIPELINE_VERSION, ["0", "1", "2"]),
    "text": (text_proc.PIPELINE_VERSION, [""]),
}


# ---- The fixtures themselves are valid C1 ------------------------------------

@pytest.mark.parametrize("modality", fixtures.MODALITIES)
def test_fixture_c1_is_schema_valid(modality):
    assert schemas.validate_c1(fixtures.load_c1(modality)) == []


# ---- Each modality: valid C2(s), right kind, deterministic record_ids --------

@pytest.mark.parametrize("modality", fixtures.MODALITIES)
def test_modality_emits_valid_c2s(client, modality):
    fs = client.fake_storage
    resp = fixtures.register_and_post(client, modality)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["ok"] is True
    record_ids = body["record_ids"]

    # One record per unit the Processor returned.
    expected_n = fixtures.EXPECTED_RECORDS[modality]
    assert len(record_ids) == expected_n
    assert len(fs.record_posts) == expected_n

    c1 = fixtures.load_c1(modality)
    pipeline_version, discriminators = _EXPECT[modality]

    for c2, record_id, discriminator in zip(fs.record_posts, record_ids, discriminators):
        # C2 validates against the frozen schema.
        assert schemas.validate_c2(c2) == []
        # record_id echoed == persisted.
        assert c2["record_id"] == record_id
        # content.kind is the frozen enum value for this modality.
        assert c2["content"]["kind"] == fixtures.EXPECTED_KIND[modality]
        # record_id is the deterministic function of (chunk_id, pv, discriminator).
        assert record_id == compute_record_id(
            c1["chunk_id"], pipeline_version, discriminator
        )
        # Provenance + time-spine carried verbatim from C1.
        assert c2["source"] == {
            "device_id": c1["device_id"],
            "stream_id": c1["stream_id"],
            "chunk_id": c1["chunk_id"],
            "blob_ref": c1["blob_ref"],
            "modality": modality,
        }
        assert c2["t_start"] == c1["t_start"]
        assert c2["t_end"] == c1["t_end"]
        # enrichments present-but-empty (shape stable for future world-data).
        assert c2["enrichments"] == {
            "speakers": [],
            "faces": [],
            "places": [],
            "objects": [],
        }
        assert c2["pipeline_version"] == pipeline_version
        assert c2["processed_at"]


# ---- Video: one chunk -> MANY records, distinct + deterministic --------------

def test_video_chunk_fans_out_to_many_distinct_records(client):
    fs = client.fake_storage
    resp = fixtures.register_and_post(client, "video")
    assert resp.status_code == 200
    record_ids = resp.json()["record_ids"]

    # More than one record from a single chunk (the seam's headline case).
    assert len(record_ids) == 3
    # All distinct (discriminator folded into each id).
    assert len(set(record_ids)) == 3

    c1 = fixtures.load_c1("video")
    pv = video_proc.PIPELINE_VERSION
    # Exactly the deterministic per-keyframe ids, in order.
    assert record_ids == [compute_record_id(c1["chunk_id"], pv, str(i)) for i in range(3)]

    # All three C2s share the chunk's provenance + span but carry distinct captions.
    captions = [c2["content"]["text"] for c2 in fs.record_posts]
    assert len(set(captions)) == 3
    for c2 in fs.record_posts:
        assert c2["source"]["chunk_id"] == c1["chunk_id"]
        assert c2["t_start"] == c1["t_start"] and c2["t_end"] == c1["t_end"]


# ---- Image: OCR woven into the caption (D8) ----------------------------------

def test_image_weaves_ocr_into_caption(client):
    resp = fixtures.register_and_post(client, "image")
    assert resp.status_code == 200
    c2 = client.fake_storage.record_posts[0]
    assert c2["content"]["kind"] == "caption"
    # The on-screen text is written INTO the description target (D8 decoupling).
    assert "On-screen text:" in c2["content"]["text"]


# ---- Text: normalized-text modality ------------------------------------------

def test_text_modality_normalizes(client):
    resp = fixtures.register_and_post(client, "text")
    assert resp.status_code == 200
    c2 = client.fake_storage.record_posts[0]
    assert c2["content"]["kind"] == "text"
    # Whitespace runs collapsed + NUL stripped by the mock normalizer.
    assert "\x00" not in c2["content"]["text"]
    assert "   " not in c2["content"]["text"]


# ---- Dedup across modalities: re-ingest -> same ids, no dup writes ------------

@pytest.mark.parametrize("modality", fixtures.MODALITIES)
def test_redelivery_is_idempotent_per_modality(client, modality):
    fs = client.fake_storage
    r1 = fixtures.register_and_post(client, modality)
    r2 = fixtures.register_and_post(client, modality)  # exact redelivery
    assert r1.status_code == r2.status_code == 200

    # Same record_ids both times.
    assert r1.json()["record_ids"] == r2.json()["record_ids"]

    # No duplicate /context writes; the blob pulled at most once (fast path skips it).
    n = fixtures.EXPECTED_RECORDS[modality]
    assert len(fs.record_posts) == n
    assert len(fs.blob_gets) == 1
