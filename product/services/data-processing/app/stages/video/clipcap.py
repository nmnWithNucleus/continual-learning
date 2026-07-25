"""Video PRIMARY stage: the clip captioner (Architecture A, ratified — WS-D, D2).

The clip pipeline's spine (design §2). Consumes ``clip_frames`` (WS-B ``clipprep``) and the
rendered ``ocr_text`` injection block (WS-C ``screentext``), makes ONE multi-image VLM call
through the captioner seam, and emits EXACTLY one ``kind='caption'`` record. All the logic
lives in the backend (``app/vision/clipcap/``) and ``app/vision/emit.py``; this stage is the
thin graph adapter.

CONFIRMED WIRING: kind='primary', policy='required', order 20 (the locked clip band is
clipprep=5, screentext=15, clipcap=20 — clear of legacy captions=10),
needs=("clipprep","screentext"), provides=("clip",), mutable_slots=("enrichments",),
version_fragment = backend.PIPELINE_VERSION + backend.prompt_tag(vs) + cfg_tag(vs).
``enabled()`` is ``resolve_pipeline() == "clip"``, so the stage is DORMANT under the default
keyframe pipeline and every legacy fixture stays green.

Settings note: the captioner, budget and ``cfg_tag`` all speak D1's FLAT ``VisionSettings``,
so this stage reads it via ``get_vision_settings()`` (env-fresh, identical to what
``version_fragment`` uses) rather than ``clipprep``'s composite ``ClipVisionSettings`` slot —
both are pure functions of the same env, so the dialect and the run stay consistent, and the
stage does not couple to another workstream's settings wrapper. ``ocr_text`` is injected into
the clip prompt by the vlm backend under the labelled ``## On-screen text … (INPUT, not
target)`` block (D-09): use it to name/ground what is visible, never copy it out.
"""
from __future__ import annotations

from ...processing.base import ProcessedUnit
from ...stagegraph import Stage, StageContext, StageResult, register_stage
from ...vision import clipcap, emit
from ...vision.config import get_vision_settings
from ...vision.mode import resolve_pipeline
from ...vision.version import cfg_tag


@register_stage
class ClipcapStage(Stage):
    name = "clipcap"
    modality = "video"
    kind = "primary"
    policy = "required"
    needs = ("clipprep", "screentext")
    provides = ("clip",)
    mutable_slots = ("enrichments",)   # declared for a future bbox/quality mutate (E-5); no writer yet
    order = 20

    def enabled(self, settings) -> bool:
        """Dormant unless the clip pipeline is selected (default/unknown → keyframe)."""
        return resolve_pipeline() == "clip"

    def version_fragment(self, settings) -> str:
        """The clip base dialect: backend PIPELINE_VERSION + prompt-pack tag + cfg tag. A
        backend flip, a prompt edit (via prompt_tag/PACK_DIGEST) or any OUTPUT_AFFECTING knob
        forks the record_id (version-forward). The mock backend's prompt_tag is "" so a
        prompt edit never re-keys the headless corpus (D-13)."""
        vs = get_vision_settings()
        backend = clipcap.select(vs)
        return backend.PIPELINE_VERSION + backend.prompt_tag(vs) + cfg_tag(vs)

    async def run_async(self, ctx: StageContext) -> StageResult:
        """ONE loop-native multi-image call (0 threadpool tokens — §7.4). ``screentext``
        completed first (a declared need), so its rendered OCR block is injected here (D-09)."""
        vs = get_vision_settings()
        clip = ctx.slots["clip_frames"]
        ocr_text = ctx.slots["ocr_text"]
        metrics = getattr(ctx.resources, "metrics", None)
        backend = clipcap.select(vs)
        desc = await backend.describe(vs, clip, ocr_text, ctx.c1, metrics)
        return StageResult(slots={"clip": desc})

    def assemble(self, ctx: StageContext) -> list[ProcessedUnit]:
        """Exactly ONE kind='caption' unit, discriminator="", t_start=None (D-05). The
        render + budget + single-line law live in emit.caption_unit; the record set is a pure
        function of the chunk, never of the model output."""
        desc = ctx.slots["clip"]
        return [emit.caption_unit(desc, ctx.span_seconds, get_vision_settings())]
