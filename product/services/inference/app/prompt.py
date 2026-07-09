"""Prompt assembly + simple token accounting.

v0 keeps this deliberately trivial: a fixed system prompt plus the user's text
pulled from the C3 messages. No context injection, no templating beyond what the
QueryBuilder already stamped upstream.
"""
from __future__ import annotations

from typing import Any


def extract_user_text(c3: dict[str, Any]) -> str:
    """Concatenate the text of every user-role message in a C3 UserPrompt.

    v0 C3 carries a single user message, but we join defensively so an
    additional user turn never gets silently dropped. System-role messages in
    C3 are ignored here — inference owns the system prompt.
    """
    parts = [
        m.get("text", "")
        for m in c3.get("messages", [])
        if m.get("role") == "user"
    ]
    return "\n".join(p for p in parts if p)


def word_count(text: str) -> int:
    """Whitespace word count — the v0 stand-in for a real tokenizer."""
    return len(text.split())
