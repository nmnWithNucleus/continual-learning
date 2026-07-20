"""Audio SIDECAR stage: acoustic-event captioning (AST AudioSet tagger).

Byte-identical transplant of the monolithic ``_acoustic_events`` stage: caption salient
non-speech audio as a ``discriminator="acoustic"`` caption unit — captioned, not dropped,
so an all-ambient chunk still yields a searchable record beside its (empty) transcript.
It reads only the raw blob (no dependency on ASR), so under the graph it now runs in
PARALLEL with asr instead of after it — the one observable *latency* improvement in the
audio port; the emitted records are identical (assembly still appends it after the
primary + translation by order). Off (default) → no unit.
"""
from __future__ import annotations

from ...audio import acoustic
from ...audio.config import get_audio_config
from ...processing.base import ProcessedContent, ProcessedUnit, empty_enrichments
from ...stagegraph import Stage, StageContext, StageResult, register_stage


@register_stage
class AcousticStage(Stage):
    name = "acoustic"
    modality = "audio"
    kind = "sidecar"
    needs = ()  # independent of ASR — runs concurrently
    order = 30

    def enabled(self, settings) -> bool:
        return acoustic.select(get_audio_config()) is not None

    def run_sync(self, ctx: StageContext) -> StageResult:
        cfg = get_audio_config()
        backend = acoustic.select(cfg)
        if backend is None:
            return StageResult()
        result = backend.caption(
            ctx.blob, ctx.c1["codec"], ctx.span_seconds, cfg, ctx.c1["chunk_id"]
        )
        if result is None or not result.text.strip():
            return StageResult()
        return StageResult(units=[
            ProcessedUnit(
                content=ProcessedContent(kind="caption", text=result.text),
                enrichments=empty_enrichments(),  # own empty block; NOT the diarized one
                discriminator="acoustic",
            )
        ])
