"""Diarization stage — the DIARIZE_BACKEND=off|mock|pyannote switch.

Hermetic: mock ASR + mock diarization (no GPU, no pyannote), storage faked. Proves:
  * OFF (default) is byte-identical — speakers null, enrichments empty, pipeline_version
    unforked, one record (this is also what the 38-test baseline relies on);
  * mock diarization fills segments[].speaker (max-overlap) + enrichments.speakers, forks
    pipeline_version to 'asr-mock-v0+diar-mock-v1', still ONE schema-valid record;
  * the fork is real: the diarized record_id differs from the undiarized one;
  * an UNRECOGNIZED backend value resolves to off in BOTH the tag and the stage (the
    invariant that stops a diarized record colliding with the pristine primary id);
  * the assignment helper itself (max-overlap, tie-break, no-overlap -> None, aggregation).
"""
from __future__ import annotations

from app import schemas
from app.asr import mock as mock_asr
from app.asr.result import AsrSegment
from app.audio.config import get_audio_config
from app.audio.diarize import version_tag
from app.audio.diarize.assign import assign_speakers
from app.audio.diarize.result import DiarizationResult, SpeakerTurn
from app.pipeline import compute_record_id
from tests.conftest import make_c1

_BASE_PV = mock_asr.PIPELINE_VERSION            # "asr-mock-v0"
_DIAR_PV = _BASE_PV + "+diar-mock-v1"


# ---- OFF (default): byte-identical, no fork ----------------------------------

def test_diarize_off_is_byte_identical(client, monkeypatch):
    monkeypatch.delenv("DIARIZE_BACKEND", raising=False)
    c1 = make_c1(client.fake_storage, chunk_id="diar-off")
    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 200
    assert resp.json()["record_ids"] == [compute_record_id("diar-off", _BASE_PV)]

    c2 = client.fake_storage.record_posts[0]
    assert c2["pipeline_version"] == _BASE_PV
    assert c2["enrichments"]["speakers"] == []
    for seg in c2["content"]["segments"]:
        assert seg["speaker"] is None


def test_unknown_backend_resolves_off_everywhere(client, monkeypatch):
    # A typo must NOT fork (tag) or diarize (stage) — both derive from one resolver.
    monkeypatch.setenv("DIARIZE_BACKEND", "pyannoteX")
    assert version_tag(get_audio_config()) == ""
    c1 = make_c1(client.fake_storage, chunk_id="diar-typo")
    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 200
    assert resp.json()["record_ids"] == [compute_record_id("diar-typo", _BASE_PV)]
    c2 = client.fake_storage.record_posts[0]
    assert c2["pipeline_version"] == _BASE_PV
    assert c2["enrichments"]["speakers"] == []


# ---- MOCK diarization: speakers filled, version forked -----------------------

def test_mock_diarization_fills_speakers_and_forks_version(client, monkeypatch):
    monkeypatch.setenv("DIARIZE_BACKEND", "mock")  # DIARIZE_SPEAKERS defaults to 2
    c1 = make_c1(
        client.fake_storage,
        chunk_id="diar-on",
        t_start="2026-07-09T12:00:00Z",
        t_end="2026-07-09T12:00:05Z",
    )
    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 200

    # Still 1:1 (no translation/acoustic) — but a DIFFERENT (forked) record_id.
    record_ids = resp.json()["record_ids"]
    assert record_ids == [compute_record_id("diar-on", _DIAR_PV)]
    assert record_ids != [compute_record_id("diar-on", _BASE_PV)]  # fork is real

    c2 = client.fake_storage.record_posts[0]
    assert schemas.validate_c2(c2) == []
    assert c2["pipeline_version"] == _DIAR_PV

    # Two mock ASR segments over a 5s chunk -> two 2.5s speakers (spk_0, then spk_1).
    speakers = [seg["speaker"] for seg in c2["content"]["segments"]]
    assert speakers == ["spk_0", "spk_1"]

    assert c2["enrichments"]["speakers"] == [
        {"speaker": "spk_0", "total_speech_s": 2.5, "segment_count": 1},
        {"speaker": "spk_1", "total_speech_s": 2.5, "segment_count": 1},
    ]
    # The other enrichment arrays stay present-but-empty.
    for key in ("faces", "places", "objects"):
        assert c2["enrichments"][key] == []


def test_mock_diarization_single_speaker(client, monkeypatch):
    monkeypatch.setenv("DIARIZE_BACKEND", "mock")
    monkeypatch.setenv("DIARIZE_SPEAKERS", "1")
    c1 = make_c1(client.fake_storage, chunk_id="diar-1spk")
    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 200
    c2 = client.fake_storage.record_posts[0]
    assert {seg["speaker"] for seg in c2["content"]["segments"]} == {"spk_0"}
    assert [s["speaker"] for s in c2["enrichments"]["speakers"]] == ["spk_0"]


# ---- assign_speakers unit: overlap / tie-break / no-overlap / aggregation -----

def test_assign_speakers_max_overlap_and_aggregation():
    asr = [AsrSegment(0.0, 2.0, "a"), AsrSegment(2.0, 4.0, "b"), AsrSegment(4.0, 6.0, "c")]
    out = [{"speaker": None} for _ in asr]
    turns = DiarizationResult(turns=[
        SpeakerTurn(0.0, 3.0, "spk_0"),   # covers a fully, b partly (2.0-3.0 = 1.0)
        SpeakerTurn(3.0, 6.0, "spk_1"),   # covers b partly (3.0-4.0 = 1.0), c fully
    ])
    speakers = assign_speakers(asr, out, turns)

    # a -> spk_0 (only overlap); b -> tie 1.0 vs 1.0 -> lowest label spk_0; c -> spk_1.
    assert [o["speaker"] for o in out] == ["spk_0", "spk_0", "spk_1"]
    assert speakers == [
        {"speaker": "spk_0", "total_speech_s": 4.0, "segment_count": 2},
        {"speaker": "spk_1", "total_speech_s": 2.0, "segment_count": 1},
    ]


def test_assign_speakers_no_overlap_is_none():
    asr = [AsrSegment(10.0, 11.0, "x")]
    out = [{"speaker": None}]
    turns = DiarizationResult(turns=[SpeakerTurn(0.0, 5.0, "spk_0")])
    speakers = assign_speakers(asr, out, turns)
    assert out[0]["speaker"] is None
    assert speakers == []


def test_assign_speakers_empty_turns_leaves_null():
    asr = [AsrSegment(0.0, 1.0, "x")]
    out = [{"speaker": None}]
    speakers = assign_speakers(asr, out, DiarizationResult(turns=[]))
    assert out[0]["speaker"] is None
    assert speakers == []
