"""``clipprep`` — the video graph's frame prep: two ffmpeg passes → transient frames.

The one REQUIRED video stage (L7): everything downstream consumes its frames, so an
undecodable chunk fails the attempt loudly (worker retry → dead-letter) — placeholder
frames are never emitted as processed truth (the v0 mock synthetic fallback is dead;
mock dialects are client-level fakes now, plan §3).

Emits NO record slot: ``StageOutput(value=None, bytes=ClipFrames)``. The frames are
re-derivable heavy data (a deterministic decode of the ``/raw`` bytes), which defaults
to not-persisted under L5; the executor hands ``bytes`` to the declared consumers
(``screentext``, ``clipcap``) and frees them after the last one finishes. The C2 §2
video record carries ``caption`` + ``ocr`` only.

ffmpeg stays a SUBPROCESS (self-isolating, L9 — no model server), so ``run_sync`` in
the executor's worker threadpool is the right execution mode: the blocking
``subprocess.run`` waits can never freeze the event loop.

EVERY output-affecting knob is a CODE PIN below (L4) — the v0 env knobs they replace,
with their v0 defaults (all carried forward unchanged):

  ================================  =========================  ======
  v0 env knob (dead)                pin                        value
  ================================  =========================  ======
  VIDEO_CLIP_SECONDS_PER_FRAME      seconds_per_frame          2.5
  VIDEO_CLIP_MAX_FRAMES             max_frames                 12
  VIDEO_CLIP_MIN_FRAMES             min_frames                 2
  VIDEO_CLIP_FRAME_WIDTH            frame_width                768
  VIDEO_OCR_FRAME_WIDTH             ocr_frame_width            1728
  VIDEO_ANALYSIS_PERIOD_S           analysis_period_s          2.0
  VIDEO_OCR_IDLE_PEAK               ocr_idle_peak              8
  VIDEO_OCR_LAYOUT_PEAK             ocr_layout_peak            40
  VIDEO_OCR_MAX_EVENTS              ocr_max_events             3
  VIDEO_OCR_FLOOR_S                 ocr_floor_s                120.0
  ================================  =========================  ======

(The delta-gate constants that were never env — pixel binarize 24, 32×32 grid, spread
thresholds — stay where they always were, in ``app/vision/delta.py``.) Changing ANY of
these changes downstream bytes ⇒ bump ``backend.version`` (vB). The v0 ``delta``
observability slot is not carried forward (no consumer — L10); the delta trace is
computed inside ``prepare_clip`` and discarded here.
"""
from __future__ import annotations

from ...timeutil import parse_rfc3339
from ...stagegraph.stage import Backend, Stage, StageContext, StageOutput, register_stage
from ...vision.clip import ClipSettings, prepare_clip

# The clip-prep operating point (see the table above). vB CONTRACT: editing any field
# here is a behavior change for every downstream slot -> bump Backend("ffmpeg", vB).
CLIP_SETTINGS = ClipSettings(
    seconds_per_frame=2.5,
    max_frames=12,
    min_frames=2,
    frame_width=768,        # 768 -> exactly 360 Qwen3-VL vision tokens/frame (D-03/A-16)
    ocr_frame_width=1728,   # the mac capture cap — OCR reads native res, no resample
    analysis_period_s=2.0,
    ocr_idle_peak=8,
    ocr_layout_peak=40,
    ocr_max_events=3,
    ocr_floor_s=120.0,
)


@register_stage
class ClipPrepStage(Stage):
    name = "clipprep"
    modality = "video"
    stage_version = 1
    backend = Backend("ffmpeg", 1)
    needs = ()
    required = True
    server = ""               # ffmpeg is a subprocess, not a model server (L9)
    # L10: no record slot; the transient frames feed both downstream stages.
    consumer = "stage:screentext+clipcap"
    byte_budget = 1           # emits NO slot (value=None); the budget is structurally inert

    def run_sync(self, ctx: StageContext) -> StageOutput:
        t_start_epoch = parse_rfc3339(ctx.c1["t_start"]).timestamp()
        # ClipDecodeError / FrameCountError propagate: required failure -> no record,
        # worker retry -> dead-letter (L7). Never placeholder frames.
        clip_frames, _delta = prepare_clip(
            ctx.blob, ctx.span_seconds, t_start_epoch, CLIP_SETTINGS
        )
        return StageOutput(value=None, bytes=clip_frames)
