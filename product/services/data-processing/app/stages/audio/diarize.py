"""Audio MUTATE stage: diarization fills segments[].speaker + enrichments.speakers.

Byte-identical transplant of the monolithic ``_diarize`` stage. It MUTATES the primary's
``segments``/``enrichments`` slots in place (the two slots ``asr`` declared mutable), so
the framework fingerprint-guards them for everyone else and — the load-bearing part —
its enabledness IS its ``version_fragment``: both come from ``diarize._resolve`` via the
existing ``diarize.select`` / ``diarize.version_tag`` pair, so the stage can never fill
speakers under the undiarized dialect (the silent-overwrite class the audio review once
caught, now impossible by construction). ``off`` (default) → empty fragment → the stage
never runs and the mock dialect stays byte-identical.
"""
from __future__ import annotations

from ...audio import diarize
from ...audio.config import get_audio_config
from ...audio.diarize.assign import assign_speakers
from ...stagegraph import Stage, StageContext, StageResult, register_stage


@register_stage
class DiarizeStage(Stage):
    name = "diarize"
    modality = "audio"
    kind = "mutate"
    needs = ("asr",)
    order = 10

    def version_fragment(self, settings) -> str:
        # THE single resolver: '' when off (stage never runs), '+diar-<backend>-v1' when
        # active (forks the whole chunk's dialect). Enabledness derives from this.
        return diarize.version_tag(get_audio_config())

    def run_sync(self, ctx: StageContext) -> StageResult:
        cfg = get_audio_config()
        backend = diarize.select(cfg)
        if backend is None:  # unreachable when enabled (fragment non-empty ⇒ backend set)
            return StageResult()
        asr_result = ctx.slots["asr"]
        result = backend.diarize(ctx.blob, ctx.c1["codec"], ctx.span_seconds, cfg)
        # Mutate the primary's slots in place — exactly the old stage's effect.
        ctx.slots["enrichments"]["speakers"] = assign_speakers(
            asr_result.segments, ctx.slots["segments"], result
        )
        return StageResult()
