"""Audio plugin: an explicit staged pipeline over one chunk.

    asr -> diarize -> translate -> acoustic_events

Stage 1 (asr) is REAL and is exactly the original M0 path: select the backend
(mock default / faster_whisper), transcribe, and map each chunk-relative segment
offset to an absolute RFC3339 time clamped into the chunk span.
``pipeline_version`` delegates to the selected backend, so the mock/faster_whisper
distinction (and its record_id fork) is preserved exactly.

Stages 2-4 are documented NO-OP STUBS pinning their future contracts (CHARTER
OQ12), so landing each is a fill-in on the stage body, not a reshape:

  * diarize          — fills ``segments[].speaker`` (required-null today) and
                       ``enrichments.speakers``;
  * translate        — appends a ``discriminator="translation"`` unit;
  * acoustic_events  — appends a ``discriminator="acoustic"`` caption unit for
                       non-speech audio (captioned, not dropped).

The stages hand an ``AudioPipelineState`` carrier down the line; final assembly
emits the transcript unit first, then whatever extra units later stages appended
(none today). With today's backends the output is byte-identical to the
pre-pipeline processor — the existing mock-loop tests are the proof.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ...asr import select as select_asr
from ...asr.result import AsrResult
from ...config import Settings
from ...timeutil import abs_time, parse_rfc3339
from ..base import ProcessedContent, ProcessedUnit, Processor, empty_enrichments
from ..registry import register


@dataclass
class AudioPipelineState:
    """State carried through the audio stages for ONE chunk.

    ``segments`` are already-absolute C2 segment dicts (the asr stage owns the
    offset->absolute mapping, and later stages annotate them in place);
    ``extra_units`` collects whole additional records appended by later stages
    (translation, acoustic captions), each distinguished by its discriminator.
    """

    c1: dict[str, Any]
    blob: bytes
    settings: Settings
    span_seconds: float
    asr: Optional[AsrResult] = None                          # set by the asr stage
    segments: list[dict[str, Any]] = field(default_factory=list)
    enrichments: dict[str, list] = field(default_factory=empty_enrichments)
    extra_units: list[ProcessedUnit] = field(default_factory=list)


@register
class AudioProcessor(Processor):
    modality = "audio"
    content_kind = "transcript"

    def pipeline_version(self, settings: Settings) -> str:
        return select_asr(settings).PIPELINE_VERSION

    def process(
        self,
        c1: dict[str, Any],
        blob: bytes,
        settings: Settings,
        span_seconds: float,
    ) -> list[ProcessedUnit]:
        state = AudioPipelineState(
            c1=c1, blob=blob, settings=settings, span_seconds=span_seconds
        )
        for stage in (self._asr, self._diarize, self._translate, self._acoustic_events):
            stage(state)

        content = ProcessedContent(
            kind="transcript",
            text=state.asr.text,
            language=state.asr.language or None,
            segments=state.segments or None,
        )
        transcript = ProcessedUnit(
            content=content,
            enrichments=state.enrichments,
            discriminator="",  # the chunk's primary record; extras carry their own
        )
        return [transcript, *state.extra_units]

    # ---- Stage 1: ASR (real) --------------------------------------------------

    def _asr(self, state: AudioPipelineState) -> None:
        """Transcribe via the selected backend and map each chunk-relative segment
        offset to an absolute RFC3339 time clamped into the chunk span."""
        state.asr = select_asr(state.settings).transcribe(
            state.settings,
            state.blob,
            state.c1["codec"],
            state.span_seconds,
            state.c1["chunk_id"],
        )
        base = parse_rfc3339(state.c1["t_start"])
        for seg in state.asr.segments:
            start = min(max(seg.start_s, 0.0), state.span_seconds)
            end = min(max(seg.end_s, start), state.span_seconds)
            state.segments.append(
                {
                    "t_start": abs_time(base, start),
                    "t_end": abs_time(base, end),
                    "text": seg.text,
                    "speaker": None,  # required-nullable; the diarize stage's target
                }
            )

    # ---- Stage 2: diarization (stub) ------------------------------------------

    def _diarize(self, state: AudioPipelineState) -> None:
        """STUB — future contract: identify who spoke when; fill
        ``segments[].speaker`` (required-null until then) and
        ``enrichments.speakers``. Landing it changes the output dialect, so it
        arrives with an audio pipeline_version bump (forked records). No-op today."""

    # ---- Stage 3: translation (stub) ------------------------------------------

    def _translate(self, state: AudioPipelineState) -> None:
        """STUB — future contract: when the transcript's language is not the
        user's, append a ``discriminator="translation"`` ProcessedUnit to
        ``extra_units`` carrying the translated transcript — a stable, distinct
        record beside the original, never a mutation of it. No-op today."""

    # ---- Stage 4: acoustic events (stub) ---------------------------------------

    def _acoustic_events(self, state: AudioPipelineState) -> None:
        """STUB — future contract: caption salient non-speech audio (doors,
        sirens, music) as a ``discriminator="acoustic"`` caption unit in
        ``extra_units`` — captioned, not dropped, so an all-ambient chunk still
        yields a searchable record beside its (empty) transcript. No-op today."""
