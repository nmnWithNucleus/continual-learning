"""Generation backends behind the MODEL_BACKEND switch.

Each backend exposes:

    async def stream(settings, system_prompt, user_text, usage_out) -> AsyncIterator[str]

It yields answer-text chunks and, before it finishes, populates `usage_out` with
integer 'prompt_tokens' / 'output_tokens'. The caller accumulates the chunks and
reads usage_out after iteration completes.
"""
from __future__ import annotations

from ..config import Settings
from . import mock, vllm


def select(settings: Settings):
    """Return the backend module for the configured MODEL_BACKEND."""
    if settings.model_backend == "vllm":
        return vllm
    # Default (and any unrecognized value) -> mock, the no-GPU path.
    return mock
