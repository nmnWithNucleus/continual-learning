"""asr — the audio primary: thin client over ``servers/whisper`` (L9).

Loop-native (``run_async``): prepare the /infer envelope, await the whisper
pool, post-process the result into the ``asr`` slot. No model code, no env
reads — every output-affecting choice is a code pin below (L4).

Params pinned to the golden (servers/whisper PROVENANCE.md):
``task=transcribe, beam_size=1, language="en", vad=true``. DELIBERATE DIALECT
CHANGE vs v0: fw.v1 means large-v3 / cuda / fp16 / beam 1 / language pinned en /
VAD on — prior env-DEFAULT was base / cpu / int8 with auto-detect language
(ASR_MODEL/ASR_DEVICE/ASR_COMPUTE_TYPE/ASR_LANGUAGE knobs, all dead), while the
beta fleet actually ran the en pin. The carry-over says vB=1 reflects
this server-pinned identity; any future change to these params (or the server's
pinned model) is a vB bump.

Slot value (C2 v1 ``asr`` sub-schema):
 * ``value`` — the full transcript text; ``""`` is the honest VAD-silence
 empty claim (L11), always emitted.
 * ``language`` — the detected/pinned language; omitted ONLY when the server
 returned nothing truthy (the contract has it optional).
 * ``splits`` — chunk-relative segment times lifted to absolute RFC3339
 (chunk t_start + offset, clamped into [0, span_seconds] — the exact v0
 ``_absolute_segments`` mapping, minus the speaker field which now lives in
 the ``transcript`` slot); omitted when the server returned no segments.
"""
from __future__ import annotations

import base64
from typing import Any

from ...timeutil import abs_time, parse_rfc3339
from ...stagegraph.stage import Backend, Stage, StageContext, StageOutput, register_stage

# The /infer params, pinned in code — exactly the golden's params.
TRANSCRIBE_PARAMS: dict[str, Any] = {
    "task": "transcribe",
    "beam_size": 1,
    "language": "en",   # pinned, not auto-detect: faint ambient audio guesses
                        # wrong scripts and hallucinates (v0 beta finding)
    "vad": True,        # Silero VAD gate: silence transcribes EMPTY, not invented
}


def require_client(ctx: StageContext, stage: Stage):
    """The stage's server pool, or a loud RuntimeError — a missing client is an
 operational wiring bug, never something to catch-and-fake."""
    client = ctx.clients.get(stage.server)
    if client is None:
        raise RuntimeError(
            f"stage {stage.name!r} needs a model client for server "
            f"{stage.server!r} but ctx.clients has {sorted(ctx.clients)!r}"
        )
    return client


def absolute_splits(c1, span_seconds: float, segments) -> list[dict[str, Any]]:
    """Lift chunk-relative server segments to absolute-time split dicts.

 The exact v0 ``_absolute_segments`` semantics: each offset clamped into
 ``[0, span_seconds]`` (end additionally floored at start), mapped to
 ``chunk t_start + offset`` as RFC3339 UTC. Shared with the translate stage
 so both speak the same 3-key split shape ``{t_start, t_end, value}`` — a
 change here changes BOTH dialects (bump both stages' versions).
 """
    base = parse_rfc3339(c1["t_start"])
    out: list[dict[str, Any]] = []
    for seg in segments:
        start = min(max(float(seg["start_s"]), 0.0), span_seconds)
        end = min(max(float(seg["end_s"]), start), span_seconds)
        out.append({
            "t_start": abs_time(base, start),
            "t_end": abs_time(base, end),
            "value": seg["text"],
        })
    return out


@register_stage
class AsrStage(Stage):
    name = "asr"
    modality = "audio"
    stage_version = 1
    backend = Backend("fw", 1)
    needs = ()
    required = True          # no transcript claim -> no record (L7)
    # L10: C10 v2's ruled Heard-lines fallback reads slots.asr when transcript
    # holes; speaker_align consumes it in-run. ~15 chars/s of life.
    consumer = "daylog:heard"
    byte_budget = 32768      # real 17.8 s golden emits 564 B; a dense 60 s chunk
                             # stays ~4 KB (text rides twice: value + splits)
    server = "whisper"

    async def run_async(self, ctx: StageContext) -> StageOutput:
        client = require_client(ctx, self)
        result = await client.infer({
            "input_b64": base64.b64encode(ctx.blob).decode("ascii"),
            "codec": ctx.c1["codec"],
            "params": dict(TRANSCRIBE_PARAMS),
        })

        value: dict[str, Any] = {}
        language = result.get("language") or ""
        if language:
            value["language"] = language
        value["value"] = result.get("text", "")
        splits = absolute_splits(ctx.c1, ctx.span_seconds,
                                 result.get("segments") or [])
        if splits:
            value["splits"] = splits
        return StageOutput(value=value)
