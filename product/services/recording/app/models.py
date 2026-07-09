"""Pydantic models mirroring C1 + the /capture/run request/response shapes.

The C1 JSON Schema remains the authoritative gate on the emit path (see contracts.py);
``C1Envelope`` is a field-for-field mirror (``extra="forbid"`` == ``additionalProperties:
false``) giving typed construction + a second, independent check that our shape lines up,
exactly as storage/inference mirror their contracts.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Modality = Literal["audio", "image", "video", "text"]
DeviceClock = Literal["synced", "unsynced"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeviceLocation(_Strict):
    lat: float | None = None
    lon: float | None = None
    accuracy_m: float | None = None


class C1Envelope(_Strict):
    """C1 Raw-stream envelope v0 (the envelope leg: recording -> data-processing)."""

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
    device_location: DeviceLocation | None = None
    device_clock: DeviceClock | None = None


class CaptureRunRequest(_Strict):
    """POST /capture/run body — run one capture session, headless.

    ``storage_url`` / ``dp_url`` are required so the integrator wires the live ports;
    everything else falls back to service defaults (config.Settings).
    """

    storage_url: str = Field(min_length=1)
    dp_url: str = Field(min_length=1)
    source: str | None = None            # path to a .wav; None -> synthetic sample
    chunk_seconds: float | None = None
    base_wallclock: str | None = None    # RFC3339; pins frame-0 wall-clock (determinism)
    user_id: str | None = None
    device_id: str | None = None
    sample_seconds: float | None = None  # duration of the synthetic sample (source omitted)


class CaptureRunResponse(_Strict):
    stream_id: str
    chunks_emitted: int
    chunk_ids: list[str]
    sequences: list[int]
    record_ids: list[str]                # data-processing's C2 record_id per chunk (provenance)


class Health(_Strict):
    ok: bool
