"""VLM OCR backend — the A/B arm (D-06), so the "specialist CPU OCR vs the captioner
reading text" comparison can be RUN rather than argued.

LATE-BOUND (imported only when ``VIDEO_OCR_BACKEND=vlm``): plain ``httpx`` against an
OpenAI-compatible ``/v1/chat/completions`` endpoint, one image per call, greedy decode.
Unlike ``ppocr`` this arm takes a PROMPT; the eventual source of truth is WS-D's
``screen-ocr-v1`` pack (``app/vision/prompts/``), but this workstream does not own that
package and must stay self-contained + testable, so the prompt is inlined here as a
faithful copy of §5.2. When the pack lands, this backend can load it instead; the wire
(one image, JSON ``{"regions":[{role,text}]}``) is unchanged.

The model returns a ``role`` per region but no bbox and no per-region confidence — so
here ``bbox`` is a zero sentinel (reading order = model order, a stable sort) and ``conf``
is 1.0 (the model already omitted anything it could not read; D-07's conf gate is a no-op
on this arm). Redaction still runs DP-side, because a prompt rule is not an access control.
"""
from __future__ import annotations

import base64
import json
import logging
import re

import httpx

from ..clip_types import Frame, OcrRead, OcrRegion
from .config import OcrConfig

logger = logging.getLogger("data-processing.vision.ocr.vlm")

# Self-contained copy of the §5.2 `screen-ocr-v1` prompt (WS-D owns the canonical pack).
_SYSTEM = (
    "You transcribe text that is visible on a computer screen. Output the text only. "
    "VERBATIM OR NOTHING: copy characters exactly as rendered; if a string is too small, "
    "blurred, clipped or ambiguous, OMIT IT — never complete, correct, translate or infer. "
    "MEANING FIRST: return the text a person would care about later, not the whole "
    "interface. NO CHROME: skip menu bars, docks, toolbars and the clock unless the element "
    "IS the event. GROUP AND LOCATE: group text into reading-order regions with a role from "
    "titlebar, tab, sidebar, main, compose, message, toolbar, statusbar, dialog, "
    "notification. SECRETS: never transcribe a password, masked field, one-time code, card "
    "or account number, government id, API key or private key. No description, no summary, "
    "no commentary."
)
_USER = (
    "One screenshot at native capture resolution.\n"
    "Reply with ONE JSON object and nothing else:\n"
    '{"regions":[{"role":"<one of the roles above>","text":"<verbatim>"}]}\n'
    "At most {max_regions} regions, in reading order. If nothing legible and meaningful is "
    "on screen, return {\"regions\": []}."
)

_FIRST_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def make_client(cfg: OcrConfig) -> httpx.Client:
    return httpx.Client(timeout=cfg.timeout)


def _data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def _parse_regions(reply: str) -> list[dict]:
    """Tolerant parse: strip fences/prose, take the first balanced-ish object, load
    ``regions``. A reply that carries no parseable object yields no regions (the frame
    contributes nothing) rather than raising — an empty screen is a legitimate outcome."""
    text = reply.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):] if "{" in text else text
    match = _FIRST_OBJECT.search(text)
    if not match:
        return []
    try:
        obj = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    regions = obj.get("regions") if isinstance(obj, dict) else None
    return regions if isinstance(regions, list) else []


def read(cfg: OcrConfig, frame: Frame, client: httpx.Client, chunk_id: str) -> OcrRead:
    """OCR one frame via the VL endpoint. Raises on a missing frame or transport/protocol
    error (the stage absorbs + counts per-frame failures, >50% raises)."""
    if frame.jpeg_hi is None:
        raise RuntimeError(
            f"vlm-ocr: no hi-res frame at t+{frame.t_offset_s:.2f}s (undecodable/synthetic)"
        )
    payload = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": _data_url(frame.jpeg_hi)}},
                    # .replace (not .format) — the template embeds literal JSON braces.
                    {"type": "text", "text": _USER.replace("{max_regions}", str(cfg.max_events * 8))},
                ],
            },
        ],
        "max_tokens": cfg.max_tokens,
        "temperature": 0,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
    resp = client.post(f"{cfg.url}/v1/chat/completions", json=payload, headers=headers)
    resp.raise_for_status()
    body = resp.json()
    choices = body.get("choices") or []
    if not choices:
        raise ValueError(f"VLM OCR response carried no choices: {str(body)[:200]}")
    reply = ((choices[0].get("message") or {}).get("content") or "").strip()
    regions = []
    from .assemble import ROLES  # local: role vocabulary check

    for r in _parse_regions(reply):
        if not isinstance(r, dict):
            continue
        text = str(r.get("text", ""))
        role = str(r.get("role", "")).strip().lower()
        # Off-vocab / missing role -> "" so assemble derives it; with the zero sentinel bbox
        # the degenerate-bbox guard in assign_role resolves that to "main" (the content
        # default), never the top-of-heuristic titlebar band.
        role = role if role in ROLES else ""
        regions.append(OcrRegion(text=text, role=role, bbox=(0.0, 0.0, 0.0, 0.0), conf=1.0))
    return OcrRead(t_offset_s=frame.t_offset_s, regions=tuple(regions))
