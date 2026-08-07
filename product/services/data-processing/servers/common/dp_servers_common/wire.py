"""Request/response schemas — the one wire contract all model servers speak.

Inputs ride base64 in JSON (the v0 OCR service's proven posture); results are
server-specific JSON under a common envelope. `params` carries the per-call
operation parameters a stage pins in its own code (Stage C) — the server never
reads behavior from env (L4).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class InferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_b64: str | None = None   # the chunk bytes (audio/image), base64
    codec: str | None = None       # e.g. "audio/webm", "image/jpeg" — how to decode input_b64
    params: dict = Field(default_factory=dict)  # operation params, pinned in stage code


class InferOk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    result: dict


class InferError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = False
    error: str
    transient: bool  # True -> a retry on another replica may succeed


class HealthReady(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    status: str = "ready"
    server: str
    identity: dict  # model_name, weights{...}, frameworks{...}, device — the L4 hook


class HealthNotReady(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = False
    status: str  # "warming" | "load_failed"
    server: str
    error: str | None = None
