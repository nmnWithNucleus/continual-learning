"""vLLM backend — the real path (needs the a3mega GPU node).

OpenAI-compatible streaming client against {VLLM_URL}/v1/chat/completions with
stream=true. Text-only in v0: no image/video content parts. Requires a running
vLLM server (see serve_vllm.sh); NOT exercised by the mock loop.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from ..config import Settings
from ..prompt import word_count


async def stream(
    settings: Settings,
    system_prompt: str,
    user_text: str,
    usage_out: dict,
) -> AsyncIterator[str]:
    url = f"{settings.vllm_url}/v1/chat/completions"
    payload = {
        "model": settings.model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "stream": True,
        # Ask vLLM to emit a final usage-only chunk so we can report real counts.
        "stream_options": {"include_usage": True},
    }

    output_words = 0
    got_usage = False

    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue

                usage = obj.get("usage")
                if usage:
                    usage_out["prompt_tokens"] = usage.get("prompt_tokens")
                    usage_out["output_tokens"] = usage.get("completion_tokens")
                    got_usage = True

                for choice in obj.get("choices", []):
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        output_words += word_count(piece)
                        yield piece

    # Fallback usage if the server didn't send a usage chunk.
    if not got_usage:
        usage_out["prompt_tokens"] = word_count(system_prompt) + word_count(user_text)
        usage_out["output_tokens"] = output_words
