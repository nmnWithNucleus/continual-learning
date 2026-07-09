"""Mock backend — the DEFAULT, no-GPU path.

Streams a clearly-labelled canned answer token-by-token with small async delays
so the whole serve loop runs end-to-end on any box. It is unmistakably NOT a real
model answer.
"""
from __future__ import annotations

import asyncio
import re
from typing import AsyncIterator

from ..config import Settings
from ..prompt import word_count

# Split into "word + trailing whitespace" chunks so streamed pieces reassemble
# to exactly the original answer (no lost spaces).
_CHUNK_RE = re.compile(r"\S+\s*")


def _answer(user_text: str) -> str:
    return (
        f"[mock model · MODEL_BACKEND=mock] You said: '{user_text}'. "
        "A real answer needs the vLLM backend on the a3mega node."
    )


async def stream(
    settings: Settings,
    system_prompt: str,
    user_text: str,
    usage_out: dict,
) -> AsyncIterator[str]:
    answer = _answer(user_text)
    chunks = _CHUNK_RE.findall(answer) or [answer]
    for chunk in chunks:
        if settings.mock_token_delay > 0:
            await asyncio.sleep(settings.mock_token_delay)
        yield chunk
    usage_out["prompt_tokens"] = word_count(system_prompt) + word_count(user_text)
    usage_out["output_tokens"] = word_count(answer)
