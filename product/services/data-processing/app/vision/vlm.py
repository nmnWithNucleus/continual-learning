"""Real VLM captioner — dense keyframe captions + OCR, over an OpenAI endpoint.

LATE-BOUND (imported only when VIDEO_BACKEND=vlm): this is plain ``httpx`` against
an OpenAI-compatible ``/v1/chat/completions`` — the same wire the inference service
already speaks to the Qwen3-VL-32B served on the GPU node (vLLM, TP=8). So it needs
NO heavy Python package (httpx is already a base dep) and NO GPU in this process;
the GPU lives behind the HTTP endpoint. Point ``VIDEO_VLM_URL`` at any compatible
VL server (the node-7 Qwen3-VL, or a lighter local captioner served via vLLM).

Per keyframe we send the JPEG as a base64 ``image_url`` data part plus a prompt
asking for a factual caption AND a verbatim transcription of legible on-screen
text — the D8 OCR-specialist pass folded into one VL call. The caller weaves that
OCR text into the caption written to /context (D8: the model learns on-screen text
from the description target, not by reading pixels at inference); structured bbox
geometry is a later additive-C2 field, out of frozen scope.

Decoding is greedy (temperature 0) so the same keyframe captions the same way
across reprocessings — as close to the C2 idempotency contract as a VLM allows
(the record_id is deterministic regardless; greedy keeps the text stable too).
"""
from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from .config import VisionSettings
from .result import Keyframe, KeyframeCaption

logger = logging.getLogger("data-processing.vision.vlm")

# Distinct from the mock dialect so a reprocess under a different backend forks a
# new record_id (version-forward reprocessing), per the C2 contract.
PIPELINE_VERSION = "vidproc-vlm-v0"

_SYSTEM = (
    "You are a precise visual describer for a personal life-logging pipeline. "
    "Describe what is happening in the frame factually and concisely. Then "
    "transcribe any legible on-screen text exactly as written."
)
_USER = (
    "Describe this video keyframe. Respond in exactly two lines and nothing else:\n"
    "Caption: <one or two factual sentences describing the scene>\n"
    "On-screen text: <every legible on-screen/UI text, verbatim; or 'none'>"
)


def _data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def _parse(reply: str) -> tuple[str, str | None]:
    """Split the two-line reply into (caption, ocr_text). Tolerant of a model that
    ignores the format: an unstructured reply becomes the caption with no OCR."""
    caption, ocr = "", None
    for raw in reply.splitlines():
        line = raw.strip()
        low = line.lower()
        if low.startswith("caption:"):
            caption = line.split(":", 1)[1].strip()
        elif low.startswith("on-screen text:"):
            val = line.split(":", 1)[1].strip()
            if val and val.lower() not in ("none", "n/a", "(none)", "''", '""'):
                ocr = val
    if not caption:
        caption = reply.strip()  # model didn't follow the format; keep it all
    return caption, ocr


def _caption_one(client: httpx.Client, vs: VisionSettings, kf: Keyframe) -> KeyframeCaption:
    if kf.image_jpeg is None:  # synthetic keyframe (undecodable blob) — no call
        return KeyframeCaption(
            index=kf.index,
            caption="[vlm captioner: no decodable frame for this keyframe]",
            ocr_text=None,
        )
    payload = {
        "model": vs.vlm_model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": _data_url(kf.image_jpeg)}},
                    {"type": "text", "text": _USER},
                ],
            },
        ],
        "max_tokens": vs.vlm_max_tokens,
        "temperature": 0,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {vs.vlm_api_key}"} if vs.vlm_api_key else {}
    resp = client.post(
        f"{vs.vlm_url}/v1/chat/completions", json=payload, headers=headers
    )
    resp.raise_for_status()
    body = resp.json()
    # A well-formed 200 must carry a choices[].message.content. A 200 that doesn't
    # is a misbehaving endpoint: raise a CLEAR error (not an opaque KeyError/Index
    # error) so it propagates -> the chunk is not marked done -> at-least-once retry,
    # rather than writing a silently-degraded caption into /context.
    choices = body.get("choices") or []
    if not choices:
        raise ValueError(f"VLM response carried no choices: {str(body)[:200]}")
    reply = ((choices[0].get("message") or {}).get("content") or "").strip()
    caption, ocr = _parse(reply)
    return KeyframeCaption(index=kf.index, caption=caption, ocr_text=ocr)


def caption(
    vs: VisionSettings, keyframes: list[Keyframe], c1: dict[str, Any]
) -> list[KeyframeCaption]:
    """Caption each keyframe via the VL endpoint (sequential; the whole call runs
    off the event loop in the core's threadpool). A request error propagates so the
    chunk is NOT marked done and an at-least-once retry can reprocess it."""
    out: list[KeyframeCaption] = []
    with httpx.Client(timeout=vs.vlm_timeout) as client:
        for kf in keyframes:
            out.append(_caption_one(client, vs, kf))
    logger.info(
        "vlm captioned %d keyframe(s) for chunk %s via %s (%s)",
        len(out), c1.get("chunk_id"), vs.vlm_url, vs.vlm_model,
    )
    return out
