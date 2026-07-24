"""Video PREP stage for the clip pipeline (`VIDEO_PIPELINE=clip`): the delta gate + frames.

A ``sidecar`` that emits NO units — it only ``provides`` the three slots the downstream
clip stages consume: ``clip_frames`` (``ClipFrames``: the caption grid + OCR-frame times +
idle flag + span), ``delta`` (``Delta``: the per-analysis-time change maps + anchor trace,
for observability/calibration), and ``vision_settings`` (the base ``VisionSettings`` +
clip knobs bundle). Two ffmpeg passes, one decode each (D-04) — the span is a C1 field, so
there is no container-duration probe and no cut-detection pass.

Runs ONLY under the clip pipeline: ``enabled()`` is ``resolve_pipeline() == "clip"`` and
the safe default is ``keyframe``, so on the shipped fixtures the legacy graph runs and this
stage stays dormant — the default-keyframe suite stays green. ``version_fragment`` is gated
on the SAME resolver (``"+cp-v1"`` under clip, ``""`` otherwise), so ``enabled()`` ⇔
``version_fragment() != ''`` — forward-compatible with WS-E2's registration-time binding
raise for sidecars-with-provides (the addendum §11 WS-E2 hole).

Two decode outcomes, exactly like the legacy ``keyframes`` stage:
  * undecodable/ffmpeg-absent under the MOCK dev backend → a synthetic ``ClipFrames``
    (timing-less frames, no OCR reads, idle) so the loop stays headless;
  * undecodable/ffmpeg-absent under a NON-mock backend → RAISE (the chunk is redelivered;
    placeholders must never persist as processed truth).
A requested frame the stream does not contain is a ``FrameCountError`` and RAISES on ANY
backend (the ``-frame_pts`` guard) — never masked by the synthetic fallback.

ORDER — DESIGN vs the pre-integration tree. §2.1 assigns this stage ``order 0``, but the
retained legacy ``keyframes`` stage still holds ``order 0`` and ``register_stage`` enforces
per-modality order uniqueness UNCONDITIONALLY (``stage.py:260-266``), so ``order 0`` here
would fail discovery and redden all 173 tests on this branch. ``order`` is behaviourally
INERT for this stage — execution is readiness-driven off ``needs`` (empty), and ``order``
only sequences the assembly of EMITTED sidecar units, of which this stage has none — so it
is registered at a non-colliding order until integration renumbers the legacy pair (freeing
0/10/20 for the clip cohort), at which point it returns to 0. See the WS-B build log.

CLIP MODE IS NOT WIRED ON THIS BRANCH (also an integration concern). Two things are missing
until WS-G/WS-D land: the legacy ``keyframes``/``captions`` have no ``enabled()`` gate yet
(WS-G adds it), so they stay enabled in clip mode and ``keyframes`` ALSO provides
``vision_settings`` — flipping ``VIDEO_PIPELINE=clip`` here therefore raises a *slot-owner*
``GraphResolutionError`` on ``vision_settings`` (not the eventual "no clip primary"); and the
``clipcap`` primary (WS-D) does not exist. Both resolve at integration. WS-B's tests drive
this stage DIRECTLY (never through ``run_graph`` in clip mode), and the default keyframe
graph is byte-unchanged, so the suite stays green.
"""
from __future__ import annotations

from ...stagegraph import Stage, StageContext, StageResult, register_stage
from ...timeutil import parse_rfc3339
from ...vision import clip as clip_mod
from ...vision.mode import resolve_pipeline


@register_stage
class ClipPrepStage(Stage):
    name = "clipprep"
    modality = "video"
    kind = "sidecar"                 # emits no units; only provides slots
    policy = "required"
    needs = ()
    provides = ("clip_frames", "delta", "vision_settings")
    order = 5                        # design order 0; non-colliding stand-in (see docstring)

    def enabled(self, settings) -> bool:
        return resolve_pipeline() == "clip"

    def version_fragment(self, settings) -> str:
        # Gated on the SAME resolver as enabled() so the binding holds for a sidecar with
        # non-empty `provides` (WS-E2 forward-compat): enabled() <=> fragment != ''.
        return "+cp-v1" if resolve_pipeline() == "clip" else ""

    def run_sync(self, ctx: StageContext) -> StageResult:
        cvs = clip_mod.build_vision_settings()
        t_start_epoch = parse_rfc3339(ctx.c1["t_start"]).timestamp()
        try:
            clip_frames, delta = clip_mod.prepare_clip(
                ctx.blob, ctx.span_seconds, t_start_epoch, cvs
            )
        except clip_mod.ClipDecodeError:
            # FrameCountError is NOT caught here — a missing requested frame always raises.
            if cvs.base.backend != "mock":
                raise RuntimeError(
                    f"video chunk {ctx.c1['chunk_id']}: no decodable clip frames "
                    "(ffmpeg absent/timed out or undecodable bytes) — refusing to emit "
                    "placeholder frames under the non-mock backend; the chunk will be "
                    "redelivered"
                )
            clip_frames = clip_mod.synthetic_clip_frames(ctx.span_seconds, cvs.clip)
            delta = clip_mod.empty_delta()
        return StageResult(
            slots={"clip_frames": clip_frames, "delta": delta, "vision_settings": cvs}
        )
