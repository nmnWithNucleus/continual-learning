"""``clipcap`` — the clip captioner: ONE multi-image VLM call → the ``caption`` slot.

Thin client over an OpenAI-compatible endpoint (the self-hosted Qwen3-VL — an
external cloud-style endpoint, not a supervised model server, so ``server = ""`` and
the wire lives in ``app/vision/clipcap/vlm.py``). Consumes ``clipprep``'s transient
frames and ``screentext``'s digest, injects the OCR text into the prompt (D-09 — one
witness on two channels, the L11 corollary), parses the reply through the tolerant
ladder, and ABSORBS the retired ``vision/emit.py``'s render: the slot value is
``render_caption`` — the D-10 one-paragraph ``"{app} — {activity}. {description}"``
render, D-12 single-line collapse, and the D-11 char cap (a CORRECTNESS knob: the
chars-per-second-of-life dose) applied on a sentence boundary. The rest of emit.py
did NOT move here: the D-05 verbatim-span rule lives in ``pipeline.build_c2`` and the
L12 grid rule lives in the law + its tests.

OPTIONAL (L7): a captioner failure is a ``caption`` hole (record ships, healed by
redrive) — v0's raise-and-redeliver posture, translated. ``needs = ("clipprep",
"screentext")``: an OCR failure cancels this stage (statuses ``failed`` +
``cancelled``), so a caption is NEVER produced from silently-missing OCR — the
coupling v0 enforced by making screentext required.

PROMPT-PACK IDENTITY (the L4 hard gate): the packs stay in ``app/vision/prompts``
with their LOCK; this file pins the aggregate ``PACK_DIGEST`` as a CODE CONSTANT and
registration fails LOUD below if the on-disk pack digests differently — a
``.prompt.md`` edit without bumping ``Backend("vlm", vB)`` (and this pin) is
impossible to ship silently. Decode params (max_tokens, temperature) live in the pack
front-matter, inside the digest.

Code pins (L4) — the v0 env knobs they replace:

  =============================  ==================  ============================
  v0 env knob (dead)             pin                 value
  =============================  ==================  ============================
  VIDEO_VLM_MODEL                MODEL               Qwen/Qwen3-VL-32B-Instruct
  VIDEO_SCENARIO                 SCENARIO            screen-mac
  VIDEO_CAPTION_CHARS_SHARE      CAPTION_RATE        16  (of the 22 total, D-11)
  VIDEO_CLIP_PROMPT              (dead)              routes.json + SCENARIO decide
  VIDEO_VLM_STRUCTURED           (dead)              guided decoding pinned OFF
  VIDEO_MAX_PROMPT_IMAGES_CHECK  (dead)              clipprep's max_frames=12 bounds K
  =============================  ==================  ============================

Operational env (output-INERT, L4: the same served model behind any endpoint yields
the same bytes; a timeout can only fail the call — a hole, never different bytes):
``VLM_URL`` (default ``http://127.0.0.1:8000``), ``VLM_API_KEY`` (default empty),
``VLM_TIMEOUT_S`` (default ``120``). Read fresh per call.
"""
from __future__ import annotations

import os

from ...stagegraph.stage import (
    Backend,
    Stage,
    StageContext,
    StageOutput,
    StageRegistrationError,
    register_stage,
)
from ...vision import prompts
from ...vision.budget import caption_cap, truncate_sentence
from ...vision.clip_types import ClipDesc, ClipFrames
from ...vision.clipcap import vlm

# ---- code pins (L4). Editing any of these is a vB bump. --------------------------
MODEL = "Qwen/Qwen3-VL-32B-Instruct"   # served-model-name requested on the wire
SCENARIO = "screen-mac"                # prompt route (routes.json) + [[scenario_label]]
CAPTION_RATE = 16                      # D-11: caption chars/second-of-life (of 22 total)

# The aggregate prompt-pack digest this backend version was shipped against. Computed
# by app/vision/prompts at import from the on-disk pack; `python -m app.vision.prompts`
# prints the current value. EDITING A PACK => recompute, update this pin AND bump vB.
PACK_DIGEST_PIN = "565066a0"

if prompts.PACK_DIGEST != PACK_DIGEST_PIN:
    raise StageRegistrationError(
        f"clipcap: on-disk prompt pack digest {prompts.PACK_DIGEST!r} != pinned "
        f"{PACK_DIGEST_PIN!r} — a .prompt.md/routes/schemas edit shipped without a "
        "backend-version bump. Update PACK_DIGEST_PIN in app/stages/video/clipcap.py "
        "and bump Backend('vlm', vB) so the dialect forks (L4); a silent prompt change "
        "must never write records under an unchanged pipeline_version."
    )


def render_caption(desc: ClipDesc, span_seconds: float) -> str:
    """The single-line, budgeted caption string (D-10 / D-11 / D-12), absorbed from the
    retired vision/emit.py. Pure and deterministic: identical ``(desc, span)`` →
    identical text on every worker."""
    app = desc.app.strip()
    activity = desc.activity.strip()
    description = desc.description.strip()

    # D-10: "app — activity." lead when present, degrading so a fallback (empty app /
    # activity) never renders a dangling " — " or a leading ". ".
    if app and activity:
        lead = f"{app} — {activity}."
    elif app:
        lead = f"{app}."
    elif activity:
        lead = f"{activity}."
    else:
        lead = ""
    text = f"{lead} {description}".strip() if lead else description

    # D-12: collapse every whitespace run (incl. any stray newline) to one space. The
    # parse ladder already cleaned each field; this guards the joined string.
    text = " ".join(text.split())

    # D-11: truncate to the per-record char budget on a sentence boundary (word-boundary
    # fallback inside truncate_sentence). cap == 0 → "" (a valid empty caption).
    return truncate_sentence(text, caption_cap(span_seconds, CAPTION_RATE))


@register_stage
class ClipcapStage(Stage):
    name = "clipcap"
    modality = "video"
    slot = "caption"
    stage_version = 1
    backend = Backend("vlm", 1)
    needs = ("clipprep", "screentext")
    required = False          # L7: failure = caption hole; record ships; heal redrives
    server = ""               # external OpenAI-compatible endpoint, not a fleet server
    # L5/L10 budget: version key + JSON overhead + the caption. The text is capped at
    # CAPTION_RATE × span (960 chars at the 60 s design max), so 4096 bytes holds even
    # at ~4 UTF-8 bytes/char. Cost: ~16 chars per second of life.
    byte_budget = 4096

    async def run_async(self, ctx: StageContext) -> StageOutput:
        clip: ClipFrames = ctx.inputs["clipprep"].bytes
        # screentext ran (a declared need; its failure would have cancelled us), so its
        # slot value exists — "" (ran-and-empty) renders as "(none)" in the prompt.
        ocr_text: str = ctx.inputs["screentext"].value["value"]
        chunk_id = str(ctx.c1.get("chunk_id", ""))

        outcome = await vlm.describe(
            clip, ocr_text, chunk_id,
            # identity pins (vB):
            model=MODEL, scenario=SCENARIO, caption_rate=CAPTION_RATE,
            # operational wire (output-inert, read fresh per call):
            url=os.getenv("VLM_URL", "http://127.0.0.1:8000").rstrip("/"),
            api_key=os.getenv("VLM_API_KEY", ""),
            timeout_s=float(os.getenv("VLM_TIMEOUT_S", "120")),
        )
        if outcome.fallback and ctx.metrics is not None:
            try:
                ctx.metrics.inc("dp_video_parse_fallback_total",
                                {"step": outcome.step})
            except Exception:  # metrics must never fail a chunk
                pass

        return StageOutput(value={"value": render_caption(outcome.desc, ctx.span_seconds)})
