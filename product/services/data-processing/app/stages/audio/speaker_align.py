"""speaker_align — the NEW derived view: asr splits x diarization turns ->
the ``transcript`` slot (L5: derived views are new slots from ordinary stages
whose ``needs`` reference the inputs — never edits of another stage's slot).

Pure CPU (``run_sync``), no server. Inputs arrive on the blackboard as the
producing stages' values (``ctx.inputs["asr"].value`` / ``ctx.inputs["diarize"]
.value``); reads are tolerant of an executor-stamped ``version`` key so the
same code joins stamped slot dicts identically.

Join semantics — the prior ``audio/diarize/assign.py`` rules, EXACTLY:
 * each asr split gets the speaker of its MAX-temporal-overlap diarization
 turn; equal overlap tie-breaks on the lexicographically SMALLEST label;
 strictly-zero overlap contributes nothing; no overlapping turn (or no
 turns at all) -> ``speaker: None``;
 * overlap is computed in seconds on the splits' own absolute times (v0
 computed in chunk-relative seconds — same intervals, same result);
 * one output split per asr split, in order, carrying the asr split's
 ``t_start``/``t_end``/``value`` strings VERBATIM (never re-rendered) plus
 the REQUIRED-NULLABLE ``speaker``.

No asr splits (the VAD-silence empty claim) -> ``{"splits": []}``. The v0
``enrichments.speakers`` aggregation (per-speaker totals) died with the
enrichments block () and is NOT ported.
"""
from __future__ import annotations

from typing import Any, Optional

from ...timeutil import parse_rfc3339
from ...stagegraph.stage import Backend, Stage, StageContext, StageOutput, register_stage


def _epoch(value: str) -> float:
    return parse_rfc3339(value).timestamp()


def _speaker_for(start: float, end: float,
                 turns: list[tuple[float, float, str]]) -> Optional[str]:
    """The max-overlap turn's speaker (v0 ``_speaker_for``, ported verbatim):
 lowest label breaks ties; None when no turn strictly overlaps."""
    best_overlap = 0.0
    best_speaker: Optional[str] = None
    for t_start, t_end, speaker in turns:
        overlap = max(0.0, min(end, t_end) - max(start, t_start))
        if overlap <= 0.0:
            continue
        if (best_speaker is None or overlap > best_overlap
                or (overlap == best_overlap and speaker < best_speaker)):
            best_overlap = overlap
            best_speaker = speaker
    return best_speaker


@register_stage
class SpeakerAlignStage(Stage):
    name = "speaker_align"
    modality = "audio"
    slot = "transcript"
    stage_version = 1
    backend = Backend("builtin", 1)
    needs = ("asr", "diarize")   # stage names; diarize's SLOT is "diarization"
    required = False             # optional: rides the diarize cone (L7)
    byte_budget = 49152          # asr's splits + a speaker per split; the real
                                 # golden emits 371 B — wide margin at 60 s
    server = ""                  # pure CPU: no client pool
    # L10: C10 v2 routes speech lines from slots.transcript.splits ().
    consumer = "daylog:heard"

    def run_sync(self, ctx: StageContext) -> StageOutput:
        asr_value = ctx.inputs["asr"].value or {}
        diar_value = ctx.inputs["diarize"].value or {}

        turns = [
            (_epoch(t["t_start"]), _epoch(t["t_end"]), t["speaker"])
            for t in (diar_value.get("splits") or [])
        ]

        splits: list[dict[str, Any]] = []
        for seg in asr_value.get("splits") or []:
            speaker = (
                _speaker_for(_epoch(seg["t_start"]), _epoch(seg["t_end"]), turns)
                if turns else None
            )
            splits.append({
                "t_start": seg["t_start"],   # asr's strings, verbatim
                "t_end": seg["t_end"],
                "value": seg["value"],
                "speaker": speaker,          # REQUIRED-NULLABLE (contract)
            })
        return StageOutput(value={"splits": splits})
