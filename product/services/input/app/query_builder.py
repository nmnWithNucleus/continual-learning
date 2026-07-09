"""QueryBuilder — turns raw request text into a schema-valid C3 UserPrompt.

Isolated from the web layer so it is unit-testable on its own. This is the one
component the charter says "every prompt-shape evolution lands there" — for the
MVP it is deliberately thin: computer text surface only, single user message,
no C8 normalization, no C11 recent-context (those are later slices).

Source of truth for the shape is ``contracts/c3_userprompt.v0.json``. The pydantic
models below mirror that schema (extra fields forbidden == additionalProperties
false); ``validate_c3`` re-checks a built payload against the JSON Schema so we
never emit a non-conformant C3.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import List, Literal, Optional
from uuid import uuid4

import jsonschema
from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# MVP defaults (v0.0). Scope: computer text surface only.
#   - surface        : "computer"      (mobile / extension / wearable are later slices)
#   - modalities     : ["text"]        (speech / image / video are later slices)
#   - template_version stamped into every C3 so continuum can tie training data
#     to the exact prompt shape it was built with.
# ---------------------------------------------------------------------------
TEMPLATE_VERSION = "mvp-0"
DEFAULT_USER_ID = os.environ.get("DEV_USER_ID", "dev-user")
SURFACE = "computer"
MODALITIES: List[str] = ["text"]
CAN_RENDER_MARKDOWN = True

CONTRACT_ID = "C3"
CONTRACT_VERSION = "0"


def utc_now_iso() -> str:
    """RFC3339 / ISO-8601 UTC timestamp, e.g. ``2026-07-09T12:34:56.789012Z``."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Pydantic models mirroring contracts/c3_userprompt.v0.json
# ---------------------------------------------------------------------------
class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "system"]
    text: str


class ClientCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    surface: Literal["computer", "extension", "mobile", "wearable"]
    modalities: List[Literal["text", "speech", "image", "video"]]
    can_render_markdown: bool


class UserPrompt(BaseModel):
    """C3 UserPrompt v0 (text-only)."""

    model_config = ConfigDict(extra="forbid")
    contract: Literal["C3"] = CONTRACT_ID
    version: Literal["0"] = CONTRACT_VERSION
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    created_at: str
    messages: List[Message]
    client_capabilities: ClientCapabilities
    template_version: str


# ---------------------------------------------------------------------------
# JSON Schema validation (the frozen contract is the source of truth)
# ---------------------------------------------------------------------------
def _contracts_dir() -> Path:
    override = os.environ.get("CONTRACTS_DIR")
    if override:
        return Path(override)
    # app/query_builder.py -> app -> input -> services -> product/contracts
    return Path(__file__).resolve().parents[3] / "contracts"


@lru_cache(maxsize=1)
def load_c3_schema() -> dict:
    with open(_contracts_dir() / "c3_userprompt.v0.json", "r", encoding="utf-8") as fh:
        return json.load(fh)


def validate_c3(payload: dict) -> None:
    """Raise ``jsonschema.ValidationError`` if ``payload`` is not a valid C3 v0."""
    jsonschema.validate(instance=payload, schema=load_c3_schema())


# ---------------------------------------------------------------------------
# QueryBuilder
# ---------------------------------------------------------------------------
class QueryBuilder:
    """Mints ids and assembles a C3 UserPrompt from raw request text."""

    def __init__(
        self,
        template_version: str = TEMPLATE_VERSION,
        default_user_id: str = DEFAULT_USER_ID,
        surface: str = SURFACE,
    ) -> None:
        self.template_version = template_version
        self.default_user_id = default_user_id
        self.surface = surface

    # -- id minting (server-side, per charter) ------------------------------
    @staticmethod
    def new_session_id() -> str:
        return "sess-" + uuid4().hex

    @staticmethod
    def new_turn_id() -> str:
        return "turn-" + uuid4().hex

    # -- prompt assembly ----------------------------------------------------
    def build(
        self,
        text: str,
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> UserPrompt:
        """Build a validated C3 UserPrompt from raw user text.

        Missing ``session_id`` / ``turn_id`` are minted here. Raises
        ``ValueError`` on empty text.
        """
        if text is None or not text.strip():
            raise ValueError("text must be a non-empty string")

        prompt = UserPrompt(
            user_id=user_id or self.default_user_id,
            session_id=session_id or self.new_session_id(),
            turn_id=turn_id or self.new_turn_id(),
            created_at=created_at or utc_now_iso(),
            messages=[Message(role="user", text=text)],
            client_capabilities=ClientCapabilities(
                surface=self.surface,
                modalities=list(MODALITIES),
                can_render_markdown=CAN_RENDER_MARKDOWN,
            ),
            template_version=self.template_version,
        )
        # Defensive: guarantee the frozen JSON Schema accepts what we emit.
        validate_c3(prompt.model_dump())
        return prompt

    def build_dict(self, text: str, **kwargs) -> dict:
        """Convenience: build and return the C3 as a plain dict."""
        return self.build(text, **kwargs).model_dump()
