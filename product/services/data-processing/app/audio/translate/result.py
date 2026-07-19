"""The neutral translation result shape every translate backend returns.

Reuses ``app/asr/result.AsrSegment`` for segments — a translated transcript rides the
exact same chunk-relative ``(start_s, end_s, text)`` shape as ASR, so the audio pipeline
lifts both to absolute RFC3339 with one helper.

``language`` is the OUTPUT language, written into the translation record's
``content.language`` (for the whisper backend this is always ``"en"`` — Whisper's
``task="translate"`` is X→English only). The detected SOURCE language is intentionally not
carried: C2 has no home for it in v0, and the primary transcript record already records
the detected language. (Surfacing source-language provenance is a future additive C2
field.)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ...asr.result import AsrSegment


@dataclass
class TranslationResult:
    text: str
    language: str                       # OUTPUT language (BCP-47), e.g. "en"
    segments: list[AsrSegment] = field(default_factory=list)  # chunk-relative
