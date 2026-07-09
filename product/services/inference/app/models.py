"""Pydantic models mirroring the frozen JSON Schemas in product/contracts/.

These give us structured construction (C4 record, C9 end frame) and parse-time
sanity. The JSON Schemas remain the source of truth — tests validate the wire
payloads against them directly.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ---- C3 UserPrompt (consumed) ------------------------------------------------

class Message(BaseModel):
    role: Literal["user", "system"]
    text: str


class ClientCapabilities(BaseModel):
    surface: Literal["computer", "extension", "mobile", "wearable"]
    modalities: list[Literal["text", "speech", "image", "video"]]
    can_render_markdown: bool


class C3UserPrompt(BaseModel):
    contract: Literal["C3"]
    version: Literal["0"]
    user_id: str
    session_id: str
    turn_id: str
    created_at: str
    messages: list[Message]
    client_capabilities: ClientCapabilities
    template_version: str


# ---- C6 resolve (consumed) ---------------------------------------------------

class C6Resolve(BaseModel):
    model_id: str
    adapter: str
    adapter_path: str | None = None


# ---- C9 end frame (produced) -------------------------------------------------

class Usage(BaseModel):
    prompt_tokens: int | None = None
    output_tokens: int | None = None


class C9EndFrame(BaseModel):
    contract: Literal["C9"] = "C9"
    version: Literal["0"] = "0"
    turn_id: str
    model_id: str
    adapter: str
    usage: Usage | None = None
    finished: bool = True
    error: str | None = None


# ---- C4 turn record (produced) -----------------------------------------------

class C4TurnRecord(BaseModel):
    contract: Literal["C4"] = "C4"
    version: Literal["0"] = "0"
    user_id: str
    session_id: str
    turn_id: str
    user_prompt: dict[str, Any]      # the full, untouched C3 this turn answered
    response_text: str
    model_id: str
    adapter: str
    created_at: str
    completed_at: str
    tool_traces: list[Any] = Field(default_factory=list)
    mentor_traces: list[Any] = Field(default_factory=list)
