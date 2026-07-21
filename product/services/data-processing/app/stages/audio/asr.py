"""Audio PRIMARY stage: ASR (mock | faster_whisper, VAD-gated) + absolute-time mapping.

Byte-identical transplant of the monolithic processor's ``_asr`` stage + final assembly:
transcribe via the selected backend, lift chunk-relative segment offsets to absolute
RFC3339 clamped into the chunk span, and (in ``assemble``, after every other stage ran)
emit the primary transcript unit — ``discriminator=''``, so its record_id stays
byte-for-byte the pre-seam v0 id.

``version_fragment`` is the BASE audio dialect (``asr-mock-v0`` / ``asr-fw-v1``),
delegating to the selected backend exactly as ``pipeline_version`` always has. The
``segments`` + ``enrichments`` slots are declared mutable — the diarize stage fills
speakers into them in place; the executor's SlotView keeps everyone else out (a
sidecar is never even handed a reference to them).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ...asr import select as select_asr
from ...asr.result import AsrSegment
from ...processing.base import ProcessedContent, ProcessedUnit, empty_enrichments
from ...stagegraph import Stage, StageContext, StageResult, register_stage
from ...timeutil import abs_time, parse_rfc3339


def _absolute_segments(
    base: datetime, span_seconds: float, segments: list[AsrSegment]
) -> list[dict[str, Any]]:
    """Lift chunk-relative ASR/translation segments to absolute-time C2 segment dicts.

    Each offset is clamped into ``[0, span_seconds]`` and mapped to ``base + offset``.
    ``speaker`` is ``None`` here (the diarize stage fills the transcript's in place; a
    translation's segments stay null). Shared by the asr + translate stages so both speak
    exactly the 4-key C2 segment shape ``{t_start, t_end, text, speaker}``."""
    out: list[dict[str, Any]] = []
    for seg in segments:
        start = min(max(seg.start_s, 0.0), span_seconds)
        end = min(max(seg.end_s, start), span_seconds)
        out.append(
            {
                "t_start": abs_time(base, start),
                "t_end": abs_time(base, end),
                "text": seg.text,
                "speaker": None,
            }
        )
    return out


@register_stage
class AsrStage(Stage):
    name = "asr"
    modality = "audio"
    kind = "primary"
    order = 0
    provides = ("asr", "segments", "enrichments")
    mutable_slots = ("segments", "enrichments")

    def version_fragment(self, settings) -> str:
        # The base audio dialect — exactly the old pipeline_version's base term.
        return select_asr(settings).PIPELINE_VERSION

    def run_sync(self, ctx: StageContext) -> StageResult:
        asr_result = select_asr(ctx.settings).transcribe(
            ctx.settings, ctx.blob, ctx.c1["codec"], ctx.span_seconds, ctx.c1["chunk_id"]
        )
        base = parse_rfc3339(ctx.c1["t_start"])
        return StageResult(slots={
            "asr": asr_result,
            "segments": _absolute_segments(base, ctx.span_seconds, asr_result.segments),
            "enrichments": empty_enrichments(),
        })

    def assemble(self, ctx: StageContext) -> list[ProcessedUnit]:
        asr_result = ctx.slots["asr"]
        content = ProcessedContent(
            kind="transcript",
            text=asr_result.text,
            language=asr_result.language or None,
            segments=ctx.slots["segments"] or None,
        )
        return [
            ProcessedUnit(
                content=content,
                enrichments=ctx.slots["enrichments"],
                discriminator="",  # the chunk's primary record; sidecars carry their own
            )
        ]
