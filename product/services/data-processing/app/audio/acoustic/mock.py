"""Mock acoustic-event captioning — the DEFAULT no-GPU backend WHEN acoustic is on.

Deterministic and dependency-free (ignores the audio bytes): emits a fixed set of
non-speech event tags and runs them through the SHARED caption builder, so the whole
acoustic dialect — a ``discriminator="acoustic"`` ``content.kind="caption"`` sidecar
record — is exercised headless without transformers/torch.

It is NOT real acoustic analysis (``ast`` is the real path). The caption is unmistakably a
mock so it can't be confused with a real acoustic caption in ``/context``.
"""
from __future__ import annotations

from ..config import AudioConfig
from .caption import caption_from_tags
from .result import AcousticResult

# Canned non-speech tags (AudioSet-style). Fixed so the caption is deterministic.
_MOCK_TAGS: list[tuple[str, float]] = [
    ("Dishes, pots, and pans", 0.62),
    ("Water tap, faucet", 0.41),
    ("Speech", 0.90),  # present to prove the speech-family filter drops it
]


def caption(
    audio_bytes: bytes,
    codec: str,
    span_seconds: float,
    cfg: AudioConfig,
    chunk_id: str,
) -> AcousticResult:
    body = caption_from_tags(_MOCK_TAGS, cfg.acoustic_top_k, cfg.acoustic_threshold)
    text = f"[mock acoustic event caption for chunk {chunk_id}] {body}"
    return AcousticResult(text=text)
