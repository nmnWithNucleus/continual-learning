"""Pydantic models mirroring the frozen JSON Schemas in product/contracts/.

The JSON Schemas remain the SOURCE OF TRUTH — ``schemas.py`` validates the wire
payloads against them directly, and the ingest path runs that gate first. These
models are a secondary, structural mirror (``extra="forbid"`` matches the schemas'
``additionalProperties: false``) used for parse-time sanity, exactly as the
serve-loop services mirror their contracts.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Modality = Literal["audio", "image", "video", "text"]


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
    device_location: Optional[DeviceLocation] = None
    device_clock: Optional[Literal["synced", "unsynced"]] = None


# ---- C2 processed record (produced) ------------------------------------------

class C2Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str = Field(min_length=1)
    stream_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    blob_ref: str = Field(min_length=1)
    modality: Modality


class C2Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    t_start: str
    t_end: str
    text: str
    speaker: Optional[str]  # required-nullable; always null in v0 (no diarization)


class C2Content(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["transcript", "caption", "ocr", "text"]
    text: str
    language: Optional[str] = None
    segments: Optional[list[C2Segment]] = None


class C2Enrichments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speakers: list[Any] = Field(default_factory=list)
    faces: list[Any] = Field(default_factory=list)
    places: list[Any] = Field(default_factory=list)
    objects: list[Any] = Field(default_factory=list)


class C2Record(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract: Literal["C2"] = "C2"
    version: Literal["0"] = "0"
    record_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    source: C2Source
    t_start: str
    t_end: str
    content: C2Content
    enrichments: C2Enrichments
    pipeline_version: str
    processed_at: str
