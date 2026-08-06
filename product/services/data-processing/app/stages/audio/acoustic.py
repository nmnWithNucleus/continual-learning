"""acoustic — acoustic-event tags: thin client over ``servers/ast`` (L9).

The server returns the RAW top-20 AudioSet ``[label, score]`` tags (descending);
the SELECTION that v0's caption folding performed is ported here, client-side,
and its outputs land as structured values instead of a rendered sentence.

v0 selection semantics (audio/acoustic/caption.py), preserved EXACTLY:
  1. DROP the AudioSet speech-family labels (case-insensitive) — speech is the
     transcript's job, never the acoustic slot's claim;
  2. keep tags scoring >= THRESHOLD (v0 env default ACOUSTIC_THRESHOLD=0.1,
     now a code pin);
  3. rank by (-score, raw label) — the deterministic tie-break;
  4. cap at TOP_K (v0 env default ACOUSTIC_TOP_K=3, now a code pin; v0's
     ``max(1, top_k)`` floor is kept);
  5. emit the surviving labels lowercased+stripped (exactly the tokens v0
     folded into its caption).

DELIBERATELY DROPPED with the caption rendering: the sentence join ("a, b, and
c.") and the "Ambient background noise." fallback — the slot's ``values: []``
IS the ran-nothing-detected honest empty claim (L11), so a fallback string
would be a false positive claim. The server-side ``top_k=20`` retrieval width
is the v0 pipeline-construction value (the Stage B golden's params).

Slot value (C2 v1 ``acoustic`` sub-schema): ``{"values": [...], "confidence":
<top selected score>}``; ``confidence`` omitted when nothing survived.
"""
from __future__ import annotations

import base64

from ...stagegraph.stage import Backend, Stage, StageContext, StageOutput, register_stage
from .asr import require_client

# ---- code pins (L4): the v0 env-knob defaults, frozen ------------------------
TOP_K = 3            # was ACOUSTIC_TOP_K (default 3): max tags emitted
THRESHOLD = 0.1      # was ACOUSTIC_THRESHOLD (default 0.1): min per-tag score
SERVER_TOP_K = 20    # raw tag retrieval width — v0's pipeline(top_k=20)

# AudioSet speech-family labels this slot must never claim (v0 list, verbatim).
SPEECH_LABELS = frozenset({
    "speech",
    "male speech, man speaking",
    "female speech, woman speaking",
    "child speech, kid speaking",
    "conversation",
    "narration, monologue",
    "speech synthesizer",
    "silence",
})


def select_tags(tags) -> list[tuple[str, float]]:
    """The v0 folding selection over raw ``{label, score}`` tags: speech-drop,
    threshold, (-score, label) rank, top-k cap. Returns (raw label, score)."""
    ranked = sorted(
        ((t["label"], float(t["score"])) for t in tags
         if t["label"].strip().lower() not in SPEECH_LABELS
         and float(t["score"]) >= THRESHOLD),
        key=lambda t: (-t[1], t[0]),
    )
    return ranked[: max(1, TOP_K)]


@register_stage
class AcousticStage(Stage):
    name = "acoustic"
    modality = "audio"
    stage_version = 1
    backend = Backend("ast", 1)
    needs = ()
    required = False        # optional: a hole never blocks the record (L7)
    byte_budget = 2048      # <= 3 short labels + a float; goldens emit 44 B
    server = "ast"

    async def run_async(self, ctx: StageContext) -> StageOutput:
        client = require_client(ctx, self)
        result = await client.infer({
            "input_b64": base64.b64encode(ctx.blob).decode("ascii"),
            "codec": ctx.c1["codec"],
            "params": {"top_k": SERVER_TOP_K},
        })

        selected = select_tags(result.get("tags") or [])
        if not selected:
            # Ran, nothing (non-speech) detected — the honest empty claim.
            return StageOutput(value={"values": []})
        return StageOutput(value={
            "values": [label.strip().lower() for label, _score in selected],
            "confidence": selected[0][1],   # top selected score
        })
