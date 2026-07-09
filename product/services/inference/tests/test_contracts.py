"""Contract-level checks that don't need the full loop.

Confirms the schemas load + resolve their cross-refs, the mock backend's chunks
reassemble losslessly, and a valid C6 resolve validates.
"""
from __future__ import annotations

import asyncio

from app import backends
from app.config import get_settings
from app.contracts import validate_contract


def test_c6_resolve_shape_validates():
    resolve = {
        "model_id": "Qwen/Qwen3-VL-32B-Instruct",
        "adapter": "base",
        "adapter_path": None,
    }
    validate_contract("c6", resolve)  # conforms to c6_resolve.v0.json


def test_mock_backend_chunks_reassemble_and_report_usage(monkeypatch):
    monkeypatch.setenv("MODEL_BACKEND", "mock")
    monkeypatch.setenv("MOCK_TOKEN_DELAY", "0")
    settings = get_settings()
    backend = backends.select(settings)

    async def _collect():
        usage: dict = {}
        parts = []
        async for chunk in backend.stream(settings, settings.system_prompt, "hello there", usage):
            parts.append(chunk)
        return "".join(parts), usage

    answer, usage = asyncio.run(_collect())
    assert "You said: 'hello there'" in answer
    assert answer.startswith("[mock model")
    # word-count usage is populated and internally consistent
    assert usage["output_tokens"] == len(answer.split())
    assert usage["prompt_tokens"] == len(settings.system_prompt.split()) + len("hello there".split())


def test_backend_selection_defaults_to_mock(monkeypatch):
    monkeypatch.setenv("MODEL_BACKEND", "something-unknown")
    assert backends.select(get_settings()).__name__.endswith("mock")
    monkeypatch.setenv("MODEL_BACKEND", "vllm")
    assert backends.select(get_settings()).__name__.endswith("vllm")
