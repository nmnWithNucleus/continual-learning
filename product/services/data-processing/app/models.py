"""Pydantic models mirroring the frozen JSON Schemas in product/contracts/.

The JSON Schemas remain the SOURCE OF TRUTH — ``schemas.py`` validates the wire
payloads against them directly, and the ingest path runs that gate first. These
models are a secondary, structural mirror (``extra="forbid"`` matches the
schemas' ``additionalProperties: false``) used for parse-time sanity.

Mirrors must move with the schema (the trap D17 hit): a contract edit is ONE
change with four parts — the contract file, ``schemas.py``, this mirror, and the
tests that hold them together. The C2 side mirrors
``c2_processed_record.v1.json`` (D24): one record per
chunk, a slots map, no enrichments / discriminator / content.kind. Adding a slot
type is an additive edit to the schema file AND the slots model here, together.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Modality = Literal["audio", "image", "video", "text"]

# The producing stage's dialect segment, exactly as in pipeline_version
# (contract $defs.slot_version).
_SEGMENT_PATTERN = r"^[a-z0-9_]+\.v[0-9]+-[a-z0-9_]+\.v[0-9]+(\.exp-[a-z0-9_]+)?$"


# ---- C1 raw-stream envelope (consumed) ---------------------------------------

class DeviceLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lat: Optional[float] = None
    lon: Optional[float] = None
    accuracy_m: Optional[float] = None


class C1Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: Literal["C1"]
    version: Literal["0"]
    user_id: str = Field(min_length=1)
    device_id: str = Field(min_length=1)
    stream_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    chunk_id: str = Field(min_length=1)
    modality: Modality
    codec: str
    t_start: str
    t_end: str
    blob_ref: str = Field(min_length=1)
    blob_sha256: str
    blob_bytes: int = Field(ge=0)
    # D17 civil-time context (optional-additive). extra="forbid" above means an
    # envelope carrying these would be REJECTED if they weren't declared here.
    device_tz: Optional[str] = Field(default=None, min_length=1)
    device_utc_offset_minutes: Optional[int] = Field(default=None, ge=-1080, le=1080)
    device_location: Optional[DeviceLocation] = None
    device_clock: Optional[Literal["synced", "unsynced"]] = None


# ---- C2 v1 processed record (produced) ----------------------------------------

class C2SourceV1(BaseModel):
    """Provenance verbatim from C1, minus modality (root-level in v1) and minus
    the transport-only fields (sequence, codec, blob_sha256, blob_bytes)."""

    model_config = ConfigDict(extra="forbid")
    device_id: str = Field(min_length=1)
    stream_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    blob_ref: str = Field(min_length=1)
    # D17 trio + location: carried verbatim; omitted, never null, when absent.
    device_tz: Optional[str] = Field(default=None, min_length=1)
    device_utc_offset_minutes: Optional[int] = Field(default=None, ge=-1080, le=1080)
    device_clock: Optional[Literal["synced", "unsynced"]] = None
    device_location: Optional[DeviceLocation] = None


class AsrSplit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    t_start: str
    t_end: str
    value: str


class AsrSlot(BaseModel):
    """From the asr stage (thin client over the whisper server)."""

    model_config = ConfigDict(extra="forbid")
    version: str = Field(pattern=_SEGMENT_PATTERN)
    language: Optional[str] = None
    value: str  # "" = the honest empty claim (VAD-gated silence, L11)
    splits: Optional[list[AsrSplit]] = None


class DiarizationSplit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    t_start: str
    t_end: str
    speaker: str


class DiarizationSlot(BaseModel):
    """From the diarize stage: raw speaker turns only — the aligned transcript
    is the separate transcript slot (no stage edits another's slot, L5)."""

    model_config = ConfigDict(extra="forbid")
    version: str = Field(pattern=_SEGMENT_PATTERN)
    splits: list[DiarizationSplit]


class TranscriptSplit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    t_start: str
    t_end: str
    value: str
    speaker: Optional[str]  # required-nullable: null where alignment found no turn


class TranscriptSlot(BaseModel):
    """From the speaker_align stage — the derived speaker-aligned view (L5)."""

    model_config = ConfigDict(extra="forbid")
    version: str = Field(pattern=_SEGMENT_PATTERN)
    splits: list[TranscriptSplit]


class AcousticSlot(BaseModel):
    """From the acoustic stage (thin client over the ast server)."""

    model_config = ConfigDict(extra="forbid")
    version: str = Field(pattern=_SEGMENT_PATTERN)
    values: list[str]  # [] = ran, nothing detected (honest empty claim)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)


class CaptionSlot(BaseModel):
    """From the clipcap stage (one multi-image VLM call)."""

    model_config = ConfigDict(extra="forbid")
    version: str = Field(pattern=_SEGMENT_PATTERN)
    value: str


class OcrSlot(BaseModel):
    """From the screentext stage (thin client over the ocr server)."""

    model_config = ConfigDict(extra="forbid")
    version: str = Field(pattern=_SEGMENT_PATTERN)
    value: str  # "" = OCR ran, screen carried no legible text (L11)


class C2SlotsV1(BaseModel):
    """The slots map. extra='forbid' = unknown slot names fail closed; a new
    slot type is an additive edit to the contract file and here, together."""

    model_config = ConfigDict(extra="forbid")
    asr: Optional[AsrSlot] = None
    diarization: Optional[DiarizationSlot] = None
    transcript: Optional[TranscriptSlot] = None
    acoustic: Optional[AcousticSlot] = None
    caption: Optional[CaptionSlot] = None
    ocr: Optional[OcrSlot] = None


class C2ContentV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slots: C2SlotsV1


class C2RecordV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract: Literal["C2"] = "C2"
    version: Literal["1"] = "1"
    record_id: str = Field(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)
    user_id: str = Field(min_length=1)
    modality: Modality
    source: C2SourceV1
    t_start: str
    t_end: str
    pipeline_version: str = Field(
        min_length=1,
        pattern=(r"^[a-z0-9_]+\.v[0-9]+-[a-z0-9_]+\.v[0-9]+(\.exp-[a-z0-9_]+)?"
                 r"(\+[a-z0-9_]+\.v[0-9]+-[a-z0-9_]+\.v[0-9]+(\.exp-[a-z0-9_]+)?)*$"),
    )
    content: C2ContentV1
