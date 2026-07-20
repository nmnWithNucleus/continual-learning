"""Video processor — now a thin GraphProcessor over the video STAGE GRAPH.

The keyframe pipeline that used to live here (extract → caption → weave OCR → per-keyframe
sub-spans) is now two drop-in stage files under ``app/stages/video/`` — ``keyframes``
(extraction + fallbacks, provides the slot) and ``captions`` (the primary: mock/vlm
captioning, now CONCURRENT under vlm, + the assembly of every unit). This module is the
compat shim that keeps the public seam byte-stable:

  * class ``VideoProcessor`` registered via ``@register`` (registry + HTTP core unchanged);
  * ``modality='video'``, ``content_kind='caption'``;
  * ``pipeline_version`` composes to ``vidproc-mock-v0`` / ``vidproc-vlm-v0`` exactly as
    before (see ``stagegraph.executor.resolve``);
  * the module-level names the seam + video tests pin — ``extract_keyframes``,
    ``PIPELINE_VERSION``, ``SYNTHETIC_KEYFRAMES`` — remain HERE. The keyframes stage calls
    ``video_proc.extract_keyframes`` as a late-bound attribute, so tests that monkeypatch
    it keep intercepting.

Add a stage (a standalone OCR pass, multi-level summaries, bbox enrichment) by dropping a
file in ``app/stages/video/`` — you never touch this shim.
"""
from __future__ import annotations

# Re-exported so the keyframes stage's late-bound `video_proc.extract_keyframes` lookup +
# the tests that monkeypatch it resolve HERE (a module attribute, never a snapshot import).
from ...vision.frames import extract_keyframes  # noqa: F401
# The mock dialect string the seam tests handle (== default pipeline_version base term).
from ...vision.mock import PIPELINE_VERSION  # noqa: F401
from ...stagegraph import GraphProcessor
from ..registry import register

# Timing-less keyframes emitted when the blob can't be decoded under the mock backend —
# matches the pre-real-pipeline stub's fan-out (one chunk -> a few keyframe records).
SYNTHETIC_KEYFRAMES = 3


@register
class VideoProcessor(GraphProcessor):
    modality = "video"
    content_kind = "caption"
