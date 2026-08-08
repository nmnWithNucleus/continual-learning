"""The two-component record id (L3) + the C2 v1 assembler + the mirror.

record_id = sha256(chunk_id NUL pipeline_version), lowercase hex, exactly two
components — the discriminator is gone with the multi-record model (L2/L3).

build_c2 assembles the ONE record: slots map, root-level modality, source
verbatim from C1 minus modality + transport fields, the D17 trio (incl.
device_clock) riding source{} verbatim, t_start/t_end carried as VERBATIM C1
strings (carried verbatim), no processed_at (ruled 2026-08-06), no enrichments, no
discriminator, no content.kind.

The mirror moves as one change: contract file + schemas.py validator
+ models.py pydantic mirror + these tests.
"""
from __future__ import annotations

import hashlib
import dataclasses

import pytest

from app import schemas
from app.config import Settings, get_settings
from app.models import C2RecordV1
from app.pipeline import build_c2, compute_record_id

C1 = {
    "contract": "C1", "version": "0",
    "user_id": "nmn", "device_id": "dev-mic", "stream_id": "stream-1",
    "sequence": 7, "chunk_id": "chunk-abc", "modality": "audio",
    "codec": "audio/webm", "t_start": "2026-07-19T17:04:10Z",
    "t_end": "2026-07-19T17:04:22Z", "blob_ref": "raw/aa/bb/cc",
    "blob_sha256": "0" * 64, "blob_bytes": 123,
}

PV = "acoustic.v1-ast.v1+asr.v2-fw.v3+diarize.v1-pyannote.v1"

SLOTS = {
    "asr": {"version": "asr.v2-fw.v3", "language": "en", "value": "hello world",
            "splits": [{"t_start": "2026-07-19T17:04:10Z",
                        "t_end": "2026-07-19T17:04:12Z", "value": "hello world"}]},
    "diarization": {"version": "diarize.v1-pyannote.v1",
                    "splits": [{"t_start": "2026-07-19T17:04:10Z",
                                "t_end": "2026-07-19T17:04:12Z",
                                "speaker": "speaker-0"}]},
    "acoustic": {"version": "acoustic.v1-ast.v1", "values": ["keyboard typing"],
                 "confidence": 0.87},
}


# ---------------------------------------------------------------------------
# L3 — the two-component id
# ---------------------------------------------------------------------------

def test_record_id_is_sha256_of_nul_joined_pair():
    expected = hashlib.sha256(b"chunk-abc\x00" + PV.encode()).hexdigest()
    assert compute_record_id("chunk-abc", PV) == expected


def test_record_id_takes_exactly_two_components():
    """The discriminator is dead (L3) — the signature admits no third component."""
    with pytest.raises(TypeError):
        compute_record_id("c", "pv", "discriminator")  # type: ignore[call-arg]


def test_nul_join_prevents_concatenation_collisions():
    assert compute_record_id("ab", "c") != compute_record_id("a", "bc")


# ---------------------------------------------------------------------------
# The v1 assembler
# ---------------------------------------------------------------------------

def test_build_c2_v1_shape():
    record = build_c2(C1, SLOTS, PV)
    assert record == {
        "contract": "C2", "version": "1",
        "record_id": compute_record_id("chunk-abc", PV),
        "user_id": "nmn",
        "modality": "audio",
        "source": {
            "device_id": "dev-mic", "stream_id": "stream-1",
            "chunk_id": "chunk-abc", "blob_ref": "raw/aa/bb/cc",
        },
        "t_start": "2026-07-19T17:04:10Z",
        "t_end": "2026-07-19T17:04:22Z",
        "pipeline_version": PV,
        "content": {"slots": SLOTS},
    }


def test_dead_concepts_do_not_ride_the_record():
    record = build_c2(C1, SLOTS, PV)
    for dead in ("processed_at", "enrichments", "discriminator"):
        assert dead not in record
    assert "kind" not in record["content"] and "text" not in record["content"]
    assert "modality" not in record["source"]  # lifted to root in v1


def test_transport_fields_stay_off_the_record():
    record = build_c2(C1, SLOTS, PV)
    for transport in ("sequence", "codec", "blob_sha256", "blob_bytes"):
        assert transport not in record["source"]


def test_d17_trio_and_location_ride_source_verbatim():
    c1 = {**C1, "device_tz": "Asia/Tokyo", "device_utc_offset_minutes": 540,
          "device_clock": "synced",
          "device_location": {"lat": 35.0, "lon": 139.0, "accuracy_m": 10.0}}
    src = build_c2(c1, SLOTS, PV)["source"]
    assert src["device_tz"] == "Asia/Tokyo"
    assert src["device_utc_offset_minutes"] == 540
    assert src["device_clock"] == "synced"
    assert src["device_location"] == {"lat": 35.0, "lon": 139.0, "accuracy_m": 10.0}


def test_absent_d17_fields_are_omitted_never_null():
    src = build_c2(C1, SLOTS, PV)["source"]
    for f in ("device_tz", "device_utc_offset_minutes", "device_clock",
              "device_location"):
        assert f not in src


def test_spans_carried_verbatim_never_reformatted():
    """The verbatim-span rule: whatever RFC3339 spelling C1 used rides through
    char-for-char — fractional seconds, offset spelling, all of it."""
    c1 = {**C1, "t_start": "2026-07-19T17:04:10.123456+00:00",
          "t_end": "2026-07-19T17:04:22.000001Z"}
    record = build_c2(c1, SLOTS, PV)
    assert record["t_start"] == "2026-07-19T17:04:10.123456+00:00"
    assert record["t_end"] == "2026-07-19T17:04:22.000001Z"


def test_empty_slots_map_is_legal():
    record = build_c2(C1, {}, "asr.v1-mock.v1")
    assert record["content"] == {"slots": {}}
    assert schemas.validate_c2(record) == []


# ---------------------------------------------------------------------------
# The mirror: schema + pydantic move together (one change, four parts)
# ---------------------------------------------------------------------------

def test_assembled_record_passes_the_v1_contract():
    assert schemas.validate_c2(build_c2(C1, SLOTS, PV)) == []


def test_assembled_record_passes_the_pydantic_mirror():
    C2RecordV1.model_validate(build_c2(C1, SLOTS, PV))


def test_v0_shaped_record_fails_the_v1_gate():
    v0ish = build_c2(C1, SLOTS, PV)
    v0ish["content"] = {"kind": "transcript", "text": "hi"}
    assert schemas.validate_c2(v0ish) != []


def test_unknown_slot_fails_closed():
    record = build_c2(C1, {"mystery": {"version": "m.v1-x.v1", "value": ""}}, PV)
    assert schemas.validate_c2(record) != []


def test_transcript_slot_speaker_is_required_nullable():
    slots = {"transcript": {"version": "speaker_align.v1-builtin.v1",
                            "splits": [{"t_start": "t", "t_end": "t",
                                        "value": "hi", "speaker": None}]}}
    record = build_c2(C1, slots, "speaker_align.v1-builtin.v1")
    assert schemas.validate_c2(record) == []
    C2RecordV1.model_validate(record)
    # speaker MISSING (not null) fails closed
    del slots["transcript"]["splits"][0]["speaker"]
    assert schemas.validate_c2(build_c2(C1, slots, PV)) != []


def test_ran_and_empty_ocr_is_valid():
    slots = {"ocr": {"version": "screentext.v1-ppocr.v1", "value": ""}}
    record = build_c2({**C1, "modality": "video"}, slots, "screentext.v1-ppocr.v1")
    assert schemas.validate_c2(record) == []
    C2RecordV1.model_validate(record)


# ---------------------------------------------------------------------------
# Config shrink (L4): every output-affecting knob is dead
# ---------------------------------------------------------------------------

DEAD_KNOBS = (
    "asr_backend", "asr_model", "asr_device", "asr_compute_type",
    "asr_beam_size", "asr_language", "asr_vad",
    "ingest_isolation", "ingest_subproc_start",
)


@pytest.mark.parametrize("knob", DEAD_KNOBS)
def test_output_affecting_knobs_are_gone_from_settings(knob):
    assert knob not in {f.name for f in dataclasses.fields(Settings)}


def test_surviving_settings_are_operational_only():
    fields = {f.name for f in dataclasses.fields(Settings)}
    assert fields == {
        "storage_url", "http_timeout", "verify_blob_sha256",
        "ingest_async", "ingest_workers", "ingest_queue_max",
        "ingest_max_retries", "ingest_retry_backoff", "ingest_drain_timeout",
        "dp_var_dir", "redrive_max_attempts", "ingest_modality_limits",
        "metrics_enabled",
    }


def test_get_settings_works_with_no_env(monkeypatch):
    for var in ("ASR_BACKEND", "ASR_MODEL", "ASR_LANGUAGE", "INGEST_ISOLATION"):
        monkeypatch.delenv(var, raising=False)
    settings = get_settings()
    assert settings.storage_url  # defaults still resolve
