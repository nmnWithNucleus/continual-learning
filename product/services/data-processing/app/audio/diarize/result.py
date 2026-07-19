"""The neutral diarization result shape every diarize backend returns.

Mirrors ``app/asr/result.py``: turn times are CHUNK-RELATIVE offsets in seconds (from
the chunk start), so a backend never needs to know the absolute wall-clock. The audio
pipeline maps ASR segments onto these turns (max temporal overlap) to fill each
segment's ``speaker`` and to aggregate ``enrichments.speakers``.

Speaker labels are NORMALIZED to a stable ``spk_0, spk_1, …`` vocabulary (in order of
first appearance) by each backend, so the mock and pyannote paths speak one dialect and
a label is comparable across records.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SpeakerTurn:
    start_s: float   # offset from chunk start, seconds
    end_s: float     # offset from chunk start, seconds
    speaker: str     # normalized label, e.g. "spk_0"


@dataclass
class DiarizationResult:
    turns: list[SpeakerTurn] = field(default_factory=list)
