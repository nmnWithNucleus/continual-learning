"""Mock diarization — the DEFAULT no-GPU diarizer WHEN diarization is turned on.

Deterministic and dependency-free (ignores the audio bytes, like mock ASR): lays down
``DIARIZE_SPEAKERS`` (default 2) equal, contiguous speaker turns across the chunk span,
labelled ``spk_0 … spk_{n-1}``. That is enough to exercise the whole diarization dialect
headless — segment ``speaker`` fill + ``enrichments.speakers`` aggregation — in tests and
in ``run_learn`` without pyannote or a GPU.

It is NOT real diarization (there is no clustering of the actual audio); ``pyannote`` is
the real path. Its ``PIPELINE_TAG`` is distinct from pyannote's so the two never collide
on ``record_id`` for the same chunk.
"""
from __future__ import annotations

from ..config import AudioConfig
from .result import DiarizationResult, SpeakerTurn

# Folded into the audio pipeline_version (via ``diarize.version_tag``) when selected, so
# activating mock diarization version-forks the primary record. See ``diarize/__init__``.
PIPELINE_TAG = "diar-mock-v1"


def diarize(
    blob: bytes,
    codec: str,
    span_seconds: float,
    cfg: AudioConfig,
) -> DiarizationResult:
    n = max(1, cfg.diarize_speakers)
    span = span_seconds if span_seconds and span_seconds > 0 else 1.0
    turn_len = span / n

    turns: list[SpeakerTurn] = []
    for i in range(n):
        start = i * turn_len
        # Pin the final turn's end exactly to the span (avoid float drift at the edge).
        end = span if i == n - 1 else (i + 1) * turn_len
        turns.append(SpeakerTurn(start_s=start, end_s=end, speaker=f"spk_{i}"))
    return DiarizationResult(turns=turns)
