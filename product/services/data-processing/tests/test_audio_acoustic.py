"""Acoustic-event stage — the ACOUSTIC_BACKEND=off|mock|ast switch — and the shared
caption builder. Plus a combined all-stages-on composition check.

Hermetic: mock ASR + mock acoustic (no GPU, no transformers). Proves:
  * mock acoustic appends ONE extra discriminator="acoustic" caption record beside the
    untouched primary; it is schema-valid, kind='caption', no segments, distinct id,
    shares (does not fork) pipeline_version;
  * off is a no-op (one record);
  * the caption builder drops speech-family tags, thresholds, joins, and falls back;
  * diarize + translate + acoustic together yield 3 distinct records, the primary
    diarized and version-forked, all three sharing the forked pv.
"""
from __future__ import annotations

from app import schemas
from app.asr import mock as mock_asr
from app.audio.acoustic.caption import FALLBACK, caption_from_tags
from app.pipeline import compute_record_id
from tests.conftest import make_c1

_PV = mock_asr.PIPELINE_VERSION            # "asr-mock-v0"
_DIAR_PV = _PV + "+diar-mock-v1"


# ---- mock acoustic: primary + one caption sidecar ----------------------------

def test_mock_acoustic_appends_caption_record(client, monkeypatch):
    monkeypatch.setenv("ACOUSTIC_BACKEND", "mock")
    c1 = make_c1(client.fake_storage, chunk_id="ac-on")
    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 200

    record_ids = resp.json()["record_ids"]
    assert record_ids == [
        compute_record_id("ac-on", _PV, ""),
        compute_record_id("ac-on", _PV, "acoustic"),
    ]

    primary, acoustic = client.fake_storage.record_posts
    assert schemas.validate_c2(acoustic) == []
    assert acoustic["content"]["kind"] == "caption"
    assert "acoustic" in acoustic["content"]["text"].lower()
    assert "dishes" in acoustic["content"]["text"].lower()      # non-speech tag survived
    assert acoustic["content"].get("segments") is None          # clip-level, no segments
    assert acoustic["pipeline_version"] == _PV                  # shares pv; NOT forked
    assert acoustic["enrichments"] == {
        "speakers": [], "faces": [], "places": [], "objects": [],
    }
    # Primary untouched.
    assert primary["content"]["kind"] == "transcript"


def test_acoustic_off_by_default(client, monkeypatch):
    monkeypatch.delenv("ACOUSTIC_BACKEND", raising=False)
    c1 = make_c1(client.fake_storage, chunk_id="ac-off")
    assert len(client.post("/ingest", json=c1).json()["record_ids"]) == 1


# ---- caption builder unit ----------------------------------------------------

def test_caption_drops_speech_and_joins():
    tags = [("Speech", 0.95), ("Dog", 0.6), ("Music", 0.5)]
    caption = caption_from_tags(tags, top_k=3, threshold=0.1)
    assert "speech" not in caption.lower()
    assert caption == "Dog and music."


def test_caption_thresholds_and_caps_top_k():
    tags = [("Dog", 0.6), ("Music", 0.5), ("Wind", 0.4), ("Rain", 0.05)]
    # Rain below threshold is dropped; top_k caps at 2.
    assert caption_from_tags(tags, top_k=2, threshold=0.1) == "Dog and music."


def test_caption_fallback_when_nothing_survives():
    assert caption_from_tags([("Speech", 0.99)], top_k=3, threshold=0.1) == FALLBACK
    assert caption_from_tags([], top_k=3, threshold=0.1) == FALLBACK


# ---- composition: all three stages on ----------------------------------------

def test_all_stages_compose_into_three_forked_records(client, monkeypatch):
    monkeypatch.setenv("DIARIZE_BACKEND", "mock")
    monkeypatch.setenv("TRANSLATE_BACKEND", "mock")
    monkeypatch.setenv("TRANSLATE_TARGET", "fr")
    monkeypatch.setenv("ACOUSTIC_BACKEND", "mock")

    c1 = make_c1(client.fake_storage, chunk_id="all-on")
    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 200

    record_ids = resp.json()["record_ids"]
    # Diarization forks the pv, and the whole chunk shares it -> all three carry _DIAR_PV.
    assert record_ids == [
        compute_record_id("all-on", _DIAR_PV, ""),
        compute_record_id("all-on", _DIAR_PV, "translation"),
        compute_record_id("all-on", _DIAR_PV, "acoustic"),
    ]
    assert len(set(record_ids)) == 3

    primary, translation, acoustic = client.fake_storage.record_posts
    for c2 in (primary, translation, acoustic):
        assert schemas.validate_c2(c2) == []
        assert c2["pipeline_version"] == _DIAR_PV
    # Primary is diarized; sidecars are not (their own empty enrichments).
    assert [s["speaker"] for s in primary["content"]["segments"]] == ["spk_0", "spk_1"]
    assert primary["enrichments"]["speakers"]  # populated
    assert translation["enrichments"]["speakers"] == []
    assert acoustic["enrichments"]["speakers"] == []
    assert translation["content"]["kind"] == "transcript"
    assert acoustic["content"]["kind"] == "caption"
