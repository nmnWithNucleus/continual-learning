"""translate — speech translation to English: built + tested, NOT REGISTERED.

NO ``@register_stage`` on purpose: the ratified dialect
excludes translation, v0's beta fleet ran TRANSLATE_BACKEND=off, and C2 v1 has
no ``translation`` slot — its sub-schema lands ADDITIVELY when this producer
ships into a dialect (an emitted translation slot fails today's contract gate,
by design: unknown slots fail closed). Enabling it is a code change: add the
decorator + the contract slot + the dialect bump, one ceremony.

Thin client over the SAME ``servers/whisper`` pool as asr (v0 parity: one
loaded model serves both tasks). Semantics (v0 stage + plan):
  * asr value empty OR detected language already "en" -> NO server call, emit
    ``{"value": ""}`` — the honest nothing-to-translate claim (L11). v0's
    TRANSLATE_TARGET knob is dead: the target is the code pin "en" (whisper's
    task=translate is X->English only — v0 enforced exactly this).
  * otherwise call whisper with task=translate: the SOURCE is auto-detected
    (``language: None`` — deliberately NOT asr's "en" pin, the v0 design
    point), same beam/VAD pins as asr; the RESULT language is hardcoded "en"
    (task=translate output is always English — v0 hardcoded it, the server
    does too).
  * splits ride the same absolute mapping as asr's (shared helper — one
    definition of the 3-key split shape); omitted when no segments came back;
    an empty translation text still emits (honest empty claim, a deliberate
    change from v0's emit-no-record).
"""
from __future__ import annotations

import base64
from typing import Any

from ...stagegraph.stage import Backend, Stage, StageContext, StageOutput
from .asr import absolute_splits, require_client

TRANSLATE_PARAMS: dict[str, Any] = {
    "task": "translate",
    "beam_size": 1,
    "language": None,   # auto-detect the SOURCE (translation implies non-en)
    "vad": True,
}
TARGET_LANGUAGE = "en"  # whisper task=translate is X->English only (v0 pin)


class TranslateStage(Stage):
    name = "translate"
    modality = "audio"
    slot = "translation"
    stage_version = 1
    backend = Backend("fw", 1)
    needs = ("asr",)
    required = False  # DELIBERATE FLIP vs v0 (policy='required'): under L7/L8 a
                      # translate outage should hole-and-heal, not dead-letter the
                      # chunk. Revisit at the enable ceremony (registration + the
                      # additive 'translation' contract slot land together).
    byte_budget = 32768     # same shape + scale as the asr slot
    server = "whisper"
    # L10: unregistered today; a non-English dogfood would route it to Heard.
    consumer = "speculative:non_english_dogfood"

    async def run_async(self, ctx: StageContext) -> StageOutput:
        asr_value = ctx.inputs["asr"].value or {}
        text = (asr_value.get("value") or "").strip()
        detected = (asr_value.get("language") or "").strip().lower()
        if not text or detected == TARGET_LANGUAGE:
            # Nothing to translate — no server call, honest empty claim.
            return StageOutput(value={"value": ""})

        client = require_client(ctx, self)
        result = await client.infer({
            "input_b64": base64.b64encode(ctx.blob).decode("ascii"),
            "codec": ctx.c1["codec"],
            "params": dict(TRANSLATE_PARAMS),
        })

        value: dict[str, Any] = {
            "language": TARGET_LANGUAGE,
            "value": result.get("text", ""),
        }
        splits = absolute_splits(ctx.c1, ctx.span_seconds,
                                 result.get("segments") or [])
        if splits:
            value["splits"] = splits
        return StageOutput(value=value)
