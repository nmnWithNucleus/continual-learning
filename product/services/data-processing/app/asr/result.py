"""The neutral ASR result shape every backend returns.

Segment times are CHUNK-RELATIVE offsets in seconds (start_s/end_s from the chunk
start). The pipeline maps them to absolute RFC3339 wall-clock (= chunk t_start +
offset) and clamps them into the chunk span, so no backend has to know the
absolute wall-clock or worry about spilling past the chunk edge.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AsrSegment:
    start_s: float   # offset from chunk start, seconds
    end_s: float     # offset from chunk start, seconds
    text: str


@dataclass
class AsrResult:
    text: str
    language: str
    segments: list[AsrSegment] = field(default_factory=list)
