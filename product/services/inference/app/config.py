"""Runtime configuration, read fresh per request from the environment.

Reading env per request (rather than freezing at import) keeps the service
trivially testable: a test can point STORAGE_URL at an ephemeral stub and flip
MODEL_BACKEND without re-importing the app.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# The fixed system prompt inference prepends to every turn (C3 carries only the
# user text in v0). Overridable via env for experiments.
DEFAULT_SYSTEM_PROMPT = (
    "You are Nucleus, a helpful, concise personal AI assistant. "
    "Answer the user's question directly and truthfully. "
    "Use Markdown when it improves clarity."
)

DEFAULT_MODEL_ID = "Qwen/Qwen3-VL-32B-Instruct"


@dataclass(frozen=True)
class Settings:
    model_backend: str          # "mock" (default, no GPU) | "vllm"
    storage_url: str            # C6 resolve + C4 write live here
    vllm_url: str               # OpenAI-compatible vLLM base URL
    model_id: str               # fallback model id when resolve is unavailable
    system_prompt: str
    mock_token_delay: float     # per-token async delay for the mock backend
    http_timeout: float         # inter-service httpx timeout (seconds)


def get_settings() -> Settings:
    return Settings(
        model_backend=os.getenv("MODEL_BACKEND", "mock").strip().lower(),
        storage_url=os.getenv("STORAGE_URL", "http://localhost:8083").rstrip("/"),
        vllm_url=os.getenv("VLLM_URL", "http://localhost:8000").rstrip("/"),
        model_id=os.getenv("MODEL_ID", DEFAULT_MODEL_ID),
        system_prompt=os.getenv("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
        mock_token_delay=float(os.getenv("MOCK_TOKEN_DELAY", "0.02")),
        http_timeout=float(os.getenv("INFERENCE_HTTP_TIMEOUT", "30")),
    )
