"""Vertex / Gemini clip captioner — a DOCUMENTED STUB, not a serving path (WS-D, D-15).

Ship self-hosted. Vertex/Gemini is an eval ORACLE (a one-off quality ceiling — 200 windows
at HIGH ≈ $70), never continuous capture: the POC measured HIGH@2fps at 527.5 prompt tok
per second of video → an 8 h screen day is 15.2 M prompt tokens → $30.4/user-day online,
36–72× the self-hosted figure. Indefensible for continuous capture, so this backend exists
to prove the seam and record the verified call shape — it does not run.

The verified `google-genai` call shape (distinct from the OpenAI wire the vlm backend
speaks — these fields do NOT exist on an OpenAI-compatible endpoint, which is exactly why
D-02 rejects `video_url` for the serving path):

    from google import genai
    from google.genai import types
    client = genai.Client(vertexai=True, project=..., location=...)
    resp = client.models.generate_content(
        model="gemini-2.x",
        contents=[types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"), ...],  # or
                 # types.Part(file_data=types.FileData(...)) + types.VideoMetadata(fps=...)
        config=types.GenerateContentConfig(
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
            temperature=0,
            response_mime_type="application/json",
            response_schema=<clip-json-v1>,
        ),
    )

Enabling it is a deliberate, separately-budgeted decision (WS-H's oracle line item), not a
config flip in the ingest path. Until then ``describe`` RAISES so a stray ``VIDEO_BACKEND=
vertex`` in the ingest fleet fails loud rather than silently billing $30/user-day.
"""
from __future__ import annotations

from typing import Any

from .. import prompts
from ..clip_types import ClipDesc, ClipFrames
from ..config import VisionSettings

# Distinct base dialect: a record captioned by the oracle is never confused with a
# self-hosted one.
PIPELINE_VERSION = "vidclip-vertex-v1"


def prompt_tag(vs: VisionSettings) -> str:
    """Same prompt-pack dialect suffix as the vlm backend — the oracle reads the same pack."""
    return prompts.version_tag(vs)


async def describe(
    vs: VisionSettings,
    clip: ClipFrames,
    ocr_text: str,
    c1: dict[str, Any],
    metrics: Any = None,
) -> ClipDesc:
    raise NotImplementedError(
        "the vertex/gemini clip captioner is an eval ORACLE, not a serving path (D-15) — "
        "it is not enabled in the ingest loop (≈$30/user-day for continuous capture). Run it "
        "as WS-H's one-off graded oracle, never via VIDEO_BACKEND=vertex on the ingest fleet."
    )
