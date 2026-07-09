"""Pydantic models mirroring the frozen contract shapes (C3 nested, C4, C6).

These mirror the JSON Schemas in ``product/contracts/`` field-for-field
(``extra="forbid"`` == ``additionalProperties: false``). The JSON Schemas remain the
authoritative gate on the write path; these give typed access + a second, independent
check that our shapes still line up.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["user", "system"]
Surface = Literal["computer", "extension", "mobile", "wearable"]
Modality = Literal["text", "speech", "image", "video"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Message(_Strict):
    role: Role
    text: str


class ClientCapabilities(_Strict):
    surface: Surface
    modalities: list[Modality]
    can_render_markdown: bool


class UserPrompt(_Strict):
    """C3 UserPrompt v0 (text-only) — nested inside a C4 turn record."""

    contract: Literal["C3"]
    version: Literal["0"]
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    created_at: str
    messages: list[Message] = Field(min_length=1)
    client_capabilities: ClientCapabilities
    template_version: str


class TurnRecord(_Strict):
    """C4 turn record v0 — the unit persisted in ``/sessions``."""

    contract: Literal["C4"]
    version: Literal["0"]
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    user_prompt: UserPrompt
    response_text: str
    model_id: str
    adapter: str
    created_at: str
    completed_at: str
    tool_traces: list[Any]
    mentor_traces: list[Any]


class ResolveResponse(_Strict):
    """C6 resolve v0 — the model-directory resolution body inference reads per request."""

    model_id: str
    adapter: str
    adapter_path: str | None


class TurnWriteAck(_Strict):
    ok: bool
    turn_id: str


class Health(_Strict):
    ok: bool
