"""Mock translation — the DEFAULT no-GPU translator WHEN translation is turned on.

Deterministic and dependency-free: tags the ASR transcript (and each of its segments)
with the target language rather than actually translating. It exercises the full
translation dialect headless — a ``discriminator="translation"`` sidecar record with
``content.kind="transcript"`` and ``language=<target>``, its segments lifted to absolute
time by the stage — without loading faster-whisper or a GPU.

It is NOT a real translation (``whisper`` is the real path). The text is unmistakably a
mock so it can never be confused with a real translated record in ``/context``.
"""
from __future__ import annotations

from ...asr.result import AsrSegment
from ...config import Settings
from .result import TranslationResult


def translate(
    settings: Settings,
    audio_bytes: bytes,
    span_seconds: float,
    asr_result,
    target: str,
) -> TranslationResult:
    text = f"[mock translation -> {target}] {asr_result.text}"
    segments = [
        AsrSegment(seg.start_s, seg.end_s, f"[->{target}] {seg.text}")
        for seg in asr_result.segments
    ]
    return TranslationResult(text=text, language=target, segments=segments)
