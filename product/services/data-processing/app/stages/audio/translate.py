"""Audio SIDECAR stage: translation (whisper task=translate → English).

Byte-identical transplant of the monolithic ``_translate`` stage: when a target language
differs from the detected one, append a ``discriminator="translation"`` transcript unit —
a stable, distinct record beside the original, never a mutation of it. Reads only the
immutable ``asr`` result (NOT the diarized segments), so it runs safely in parallel with
the diarize mutate stage. Off (default) / nothing to translate → no unit.

``policy='required'`` preserves today's contract: a translate-backend failure fails the
chunk (redelivery retries), it does not silently drop the sidecar.
"""
from __future__ import annotations

from ...audio import translate
from ...audio.config import get_audio_config
from ...processing.base import ProcessedContent, ProcessedUnit, empty_enrichments
from ...stagegraph import Stage, StageContext, StageResult, register_stage
from ...timeutil import parse_rfc3339
from .asr import _absolute_segments


@register_stage
class TranslateStage(Stage):
    name = "translate"
    modality = "audio"
    kind = "sidecar"
    needs = ("asr",)
    order = 20

    def enabled(self, settings) -> bool:
        return translate.select(get_audio_config()) is not None

    def run_sync(self, ctx: StageContext) -> StageResult:
        cfg = get_audio_config()
        backend = translate.select(cfg)  # None when off (incl. whisper+non-'en' degrade)
        if backend is None:
            return StageResult()
        asr_result = ctx.slots["asr"]
        if not asr_result.text.strip():  # silence / all-ambient → nothing to translate
            return StageResult()
        target = cfg.translate_target
        detected = (asr_result.language or "").strip().lower()
        if detected and detected == target:  # already in the target language
            return StageResult()
        result = backend.translate(
            ctx.settings, ctx.blob, ctx.span_seconds, asr_result, target
        )
        if not result.text.strip():
            return StageResult()
        base = parse_rfc3339(ctx.c1["t_start"])
        segments = _absolute_segments(base, ctx.span_seconds, result.segments)
        content = ProcessedContent(
            kind="transcript",
            text=result.text,
            language=result.language or None,
            segments=segments or None,
        )
        return StageResult(units=[
            ProcessedUnit(
                content=content,
                enrichments=empty_enrichments(),  # own empty block; NOT the diarized one
                discriminator="translation",
            )
        ])
