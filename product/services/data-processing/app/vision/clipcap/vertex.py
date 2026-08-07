"""Vertex / Gemini clip captioner — a DOCUMENTED STUB, not a serving path ().

Ship self-hosted. Vertex/Gemini is an eval ORACLE (a one-off quality ceiling — 200 windows
at HIGH ≈ $70), never continuous capture: the POC measured HIGH@2fps at 527.5 prompt tok
per second of video → an 8 h screen day is 15.2 M prompt tokens → $30.4/user-day online,
36–72× the self-hosted figure. Indefensible for continuous capture, so this module exists
to prove the seam and record the verified call shape — it does not run.

DELIBERATELY UNREGISTERED (): no stage constructs ``Backend("vertex", …)``, so
the oracle can never appear in a ``pipeline_version`` by accident. Enabling it is a
separately-budgeted decision made in code — build a one-off harness (or an ``.exp-``
dialect stage) that calls this module; there is no env switch to flip (L4).

The verified `google-genai` call shape (distinct from the OpenAI wire ``vlm.py`` speaks —
these fields do NOT exist on an OpenAI-compatible endpoint, which is exactly why 
rejects `video_url` for the serving path):

 from google import genai
 from google.genai import types
 client = genai.Client(vertexai=True, project=..., location=...)
 resp = client.models.generate_content(
 model="gemini-2.x",
 contents=[types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"),...], # or
 # types.Part(file_data=types.FileData(...)) + types.VideoMetadata(fps=...)
 config=types.GenerateContentConfig(
 media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
 temperature=0,
 response_mime_type="application/json",
 response_schema=<clip-json-v1>,
),
)
"""
from __future__ import annotations

from typing import Any

from ..clip_types import ClipFrames


async def describe(clip: ClipFrames, ocr_text: str, chunk_id: str, **_pins: Any):
    raise NotImplementedError(
        "the vertex/gemini clip captioner is an eval ORACLE, not a serving path (D-15) — "
        "≈$30/user-day for continuous capture. It is deliberately unregistered; run it "
        "only from a one-off, separately-budgeted eval harness."
    )
