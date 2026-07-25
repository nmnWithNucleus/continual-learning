"""The clip captioner backend seam — VIDEO_BACKEND = mock | vlm | vertex (WS-D, D2).

Mirrors ``app/audio/diarize/__init__.py`` and the legacy ``app/vision/__init__.py``:
one private resolver drives BOTH what runs (``select``) and — via each backend's
``PIPELINE_VERSION`` + ``prompt_tag`` — the record dialect, so the two can never
diverge (the silent-overwrite class ``diarize`` was built to make impossible). An
unrecognized ``VIDEO_BACKEND`` resolves to ``mock`` (the no-GPU / no-network default),
so a typo can never leave the clip primary with no captioner.

Each backend module exposes exactly:
  * ``PIPELINE_VERSION: str`` — the base clip dialect (``vidclip-mock-v1`` /
    ``vidclip-vlm-v1`` / ``vidclip-vertex-v1``). Distinct from the legacy keyframe
    dialects (``vidproc-*-v0``), so the two pipelines never collide on a record_id.
  * ``prompt_tag(vs) -> str`` — the prompt-pack suffix folded into the clip primary's
    ``version_fragment`` (``backend.PIPELINE_VERSION + backend.prompt_tag(vs) +
    cfg_tag(vs)``). **The mock backend returns ``""``** — it never reads a prompt, so a
    ``.prompt.md`` edit must not re-key the headless corpus (D-13). The vlm/vertex
    backends return ``app.vision.prompts.version_tag(vs)``.
  * ``async def describe(vs, clip, ocr_text, c1, metrics=None) -> ClipDesc`` — the ONE
    multi-image call (D-02) that turns a chunk's frames + injected OCR text into a
    ``ClipDesc``. Async so the long GPU wait is loop-native (0 threadpool tokens, §7.4);
    the mock path is trivially async (no await, no blocking).
"""
from __future__ import annotations

from ..config import VisionSettings

_BACKENDS = ("mock", "vlm", "vertex")


def _resolve(vs: VisionSettings) -> str:
    """Canonical clip-captioner backend name; any unrecognized ``VIDEO_BACKEND``
    resolves to ``mock`` (never leave the primary without a captioner)."""
    return vs.backend if vs.backend in _BACKENDS else "mock"


def select(vs: VisionSettings):
    """Return the resolved clip-captioner backend module. ``vlm`` / ``vertex`` are
    LATE-BOUND (imported only on their path) so the mock default never pulls httpx-only
    wire code or the vertex stub's docs into the headless loop."""
    name = _resolve(vs)
    if name == "vlm":
        from . import vlm
        return vlm
    if name == "vertex":
        from . import vertex
        return vertex
    from . import mock
    return mock
