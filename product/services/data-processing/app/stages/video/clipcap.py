"""``clipcap`` — the clip captioner: ONE multi-image VLM call → the ``caption`` slot.

Thin client over an OpenAI-compatible endpoint (the self-hosted Qwen3-VL — an
external cloud-style endpoint, not a supervised model server, so ``server = ""`` and
the wire lives in ``app/vision/clipcap/vlm.py``). Consumes ``clipprep``'s transient
frames and ``screentext``'s digest, injects the OCR text into the prompt (one witness
on two channels, the L11 corollary), parses the reply through the tolerant ladder, and
renders the slot value via ``render_caption`` — the one-paragraph
``"{app} — {activity}. {description}"`` form, single-line collapse, and the
chars-per-second-of-life cap applied on a sentence boundary. The verbatim-span rule
lives in ``pipeline.build_c2``; the L12 grid rule lives in the law + its tests.

OPTIONAL (L7): a captioner failure is a ``caption`` hole (record ships, healed by
redrive). ``needs = ("clipprep", "screentext")``: an OCR failure cancels this stage
(statuses ``failed`` + ``cancelled``), so a caption is NEVER produced from
silently-missing OCR.

PROMPT-PACK IDENTITY (the L4 hard gate): the packs stay in ``app/vision/prompts``
with their LOCK; this file pins the aggregate ``PACK_DIGEST`` as a CODE CONSTANT and
registration fails LOUD below if the on-disk pack digests differently — a
``.prompt.md`` edit without bumping ``Backend("vlm", vB)`` (and this pin) is
impossible to ship silently. Decode params (max_tokens, temperature) live in the pack
front-matter, inside the digest.

Code pins (L4):

  =============================  ==================  ============================
  pin                            constant            value
  =============================  ==================  ============================
  served model name              MODEL               Qwen/Qwen3-VL-32B-Instruct
  prompt route                   SCENARIO            screen-mac
  caption chars/sec share        CAPTION_RATE        16  (of the 22 total)
  prompt file override           (dead)              routes.json + SCENARIO decide
  structured decoding            (dead)              guided decoding pinned OFF
  max prompt images check        (dead)              clipprep's max_frames=12 bounds K
  =============================  ==================  ============================

Operational env (output-INERT, L4: the same served model behind any endpoint yields
the same bytes; a timeout can only fail the call — a hole, never different bytes):
``VLM_URL`` (default ``http://127.0.0.1:8000``), ``VLM_API_KEY`` (default empty),
``VLM_TIMEOUT_S`` (default ``120``). Read fresh per call.
"""
from __future__ import annotations

import logging
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

logger = logging.getLogger("data-processing.stages.video.clipcap")

# ---- code pins (L4). Editing any of these is a vB bump. --------------------------
MODEL = "Qwen/Qwen3-VL-32B-Instruct"   # served-model-name requested on the wire
SCENARIO = "screen-mac"                # prompt route (routes.json) + [[scenario_label]]
CAPTION_RATE = 16                      # caption chars/second-of-life (of 22 total)

# The aggregate prompt-pack digest this backend version was shipped against. Computed
# by app/vision/prompts at import from the on-disk pack; `python -m app.vision.prompts`
# prints the current value. EDITING A PACK => recompute, update this pin AND bump vB.
# 2026-08-07: the legacy per-frame prompt pack was removed. The digest covers the
# whole pack dir, so its removal moved the aggregate digest 565066a0 -> 80efa39f
# even though screen-clip-v1's caption bytes are byte-identical — the guard is aggregate
# by design (no pack file changes silently) and the price is a dialect fork on any dir
# change. Re-pinned + vB bumped vlm.v1 -> vlm.v2 below.
PACK_DIGEST_PIN = "80efa39f"

if prompts.PACK_DIGEST != PACK_DIGEST_PIN:
    raise StageRegistrationError(
        f"clipcap: on-disk prompt pack digest {prompts.PACK_DIGEST!r} != pinned "
        f"{PACK_DIGEST_PIN!r} — a .prompt.md/routes/schemas edit shipped without a "
        "backend-version bump. Update PACK_DIGEST_PIN in app/stages/video/clipcap.py "
        "and bump Backend('vlm', vB) so the dialect forks (L4); a silent prompt change "
        "must never write records under an unchanged pipeline_version."
    )


_warned_timeout: set[str] = set()


def _vlm_timeout_s() -> float:
    """VLM_TIMEOUT_S with the house warn-and-default posture for operational
 enum/number knobs: an unparsable value falls back LOUDLY (once per value),
 never crashes the chunk (a typo'd timeout must not fail ingestion)."""
    raw = os.getenv("VLM_TIMEOUT_S", "120")
    try:
        return float(raw)
    except ValueError:
        if raw not in _warned_timeout:
            _warned_timeout.add(raw)
            logger.warning("VLM_TIMEOUT_S=%r is not a number — using 120", raw)
        return 120.0


def render_caption(desc: ClipDesc, span_seconds: float) -> tuple[str, bool]:
    """The single-line, budgeted caption string, plus whether the cap actually truncated
    it (the ``dp_video_truncated_total{pass="caption"}`` signal). Pure and deterministic:
    identical ``(desc, span)`` → identical result on every worker."""
    app = desc.app.strip()
    activity = desc.activity.strip()
    description = desc.description.strip()

    # Lead: "app — activity." when present, degrading so a fallback (empty app /
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

    # Collapse every whitespace run (incl. any stray newline) to one space. The
    # parse ladder already cleaned each field; this guards the joined string.
    text = " ".join(text.split())

    # Truncate to the per-record char budget on a sentence boundary (word-boundary
    # fallback inside truncate_sentence). cap == 0 → "" (a valid empty caption).
    capped = truncate_sentence(text, caption_cap(span_seconds, CAPTION_RATE))
    return capped, capped != text


@register_stage
class ClipcapStage(Stage):
    name = "clipcap"
    modality = "video"
    slot = "caption"
    stage_version = 1
    # vB bumped v1 -> v2 when the legacy per-frame prompt pack was removed and the
    # aggregate PACK_DIGEST_PIN moved (see the pin comment above).
    backend = Backend("vlm", 2)
    needs = ("clipprep", "screentext")
    required = False          # L7: failure = caption hole; record ships; heal redrives
    server = ""               # external OpenAI-compatible endpoint, not a fleet server
    # L10: C10 v2 routes slots.caption to Scene lines. ~16 chars/s cap.
    consumer = "daylog:scene"
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
            timeout_s=_vlm_timeout_s(),
        )
        if outcome.fallback and ctx.metrics is not None:
            try:
                # Both labels, per the declaration — pack is stamped by describe()
                # (cleanup round: {step}-only inc'd into a {pack,step} family and
                # the KeyError was swallowed; the series could never record).
                ctx.metrics.inc("dp_video_parse_fallback_total",
                                {"pack": outcome.pack, "step": outcome.step})
            except Exception:  # metrics must never fail a chunk
                logger.exception("parse-fallback metric failed")

        caption, truncated = render_caption(outcome.desc, ctx.span_seconds)
        if truncated and ctx.metrics is not None:
            try:
                ctx.metrics.inc("dp_video_truncated_total", {"pass": "caption"})
            except Exception:
                logger.exception("truncated metric failed")
        return StageOutput(value={"value": caption})
