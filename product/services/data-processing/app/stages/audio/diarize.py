"""diarize — speaker turns: thin client over ``servers/pyannote`` (L9).

Produces the ``diarization`` slot: RAW speaker turns as absolute-time splits —
nothing else. The v0 stage MUTATED the primary's segments in place (filled
``segments[].speaker`` + ``enrichments.speakers``); both are dead: the
speaker-aligned view is the separate ``transcript`` slot from ``speaker_align``
(L5 — no stage edits another's slot), and the ``enrichments.speakers``
aggregation has no C2 v1 home (retired with the enrichments block, D24).

Params pinned in code (L4): ``span_seconds`` only — the server's one required
param (turns are clamped to it server-side). v0's DIARIZE_MIN_SPEAKERS /
DIARIZE_MAX_SPEAKERS knobs defaulted to 0 = "let the model decide"; that
default IS the pin — no clustering hints are sent. Sending hints would be a
vB bump.

Label normalization: the slot speaks ``speaker-N`` in FIRST-ONSET order (the
plan §2 vocabulary), tie-broken on the raw label — re-derived here from the
server's turns rather than trusting its vocabulary (today's server emits
``spk_N`` already first-onset-ordered, so the mapping is spk_N -> speaker-N;
any server relabeling cannot silently change this slot). Chunk-local by L1: no
cross-chunk speaker state exists in DP.

Times: the same clamp + absolute mapping as asr's splits (offsets clamped into
[0, span_seconds], rendered as chunk t_start + offset). The server already
clamps; clamping again here keeps the slot's law independent of server trust.
"""
from __future__ import annotations

import base64
from typing import Any

from ...timeutil import abs_time, parse_rfc3339
from ...stagegraph.stage import Backend, Stage, StageContext, StageOutput, register_stage
from .asr import require_client


def normalize_speakers(turns) -> dict[str, str]:
    """raw server label -> ``speaker-N`` by first onset (raw label breaks ties)
    — the v0 normalization rule, applied client-side to the slot vocabulary."""
    first_onset: dict[str, float] = {}
    for turn in turns:
        raw = turn["speaker"]
        start = float(turn["start_s"])
        if raw not in first_onset or start < first_onset[raw]:
            first_onset[raw] = start
    order = sorted(first_onset, key=lambda raw: (first_onset[raw], raw))
    return {raw: f"speaker-{i}" for i, raw in enumerate(order)}


@register_stage
class DiarizeStage(Stage):
    name = "diarize"
    modality = "audio"
    slot = "diarization"
    stage_version = 1
    backend = Backend("pyannote", 1)
    needs = ()
    required = False         # optional: a hole never blocks the record (L7)
    byte_budget = 8192       # real 17.8 s golden (10 turns) emits 1167 B; a
                             # busy 60 s chunk (~40 turns) stays ~4 KB
    server = "pyannote"
    # L10: consumed in-run by speaker_align (the derived transcript slot).
    consumer = "stage:speaker_align"

    async def run_async(self, ctx: StageContext) -> StageOutput:
        client = require_client(ctx, self)
        result = await client.infer({
            "input_b64": base64.b64encode(ctx.blob).decode("ascii"),
            "codec": ctx.c1["codec"],
            "params": {"span_seconds": ctx.span_seconds},
        })

        turns = result.get("turns") or []
        remap = normalize_speakers(turns)
        base = parse_rfc3339(ctx.c1["t_start"])
        splits: list[dict[str, Any]] = []
        for turn in turns:
            start = min(max(float(turn["start_s"]), 0.0), ctx.span_seconds)
            end = min(max(float(turn["end_s"]), start), ctx.span_seconds)
            splits.append({
                "t_start": abs_time(base, start),
                "t_end": abs_time(base, end),
                "speaker": remap[turn["speaker"]],
            })
        # splits [] = ran, nobody spoke — the honest empty claim (L11).
        return StageOutput(value={"splits": splits})
