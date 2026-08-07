"""Real clip captioner — ONE multi-image VLM call per chunk (/).

Plain ``httpx.AsyncClient`` against an OpenAI-compatible ``/v1/chat/completions`` —
the wire the Qwen3-VL served on the GPU node speaks. No heavy dependency (httpx is a
base dep), no GPU in this process; async so the long single-stream decode is
loop-native. Called by the ``clipcap`` stage (``app/stages/video/clipcap.py``), which
passes every output-affecting parameter EXPLICITLY (model name, scenario, caption
rate — all code pins under its backend version, L4); only the endpoint wire (url /
api key / timeout) is operational input.

**The payload is the design's headline (): K stills in ONE call, NOT
``video_url``.** Message shape: ``[{system}, {user: [head_text, Frame-label, image,
Frame-label, image, …, task_text]}]`` — the frame time labels ``Frame k (+t.s):``
interleaved before each image, and the task + JSON contract LAST. Frame selection
stays on DP's pinned ffmpeg rather than the serving box's decoder (the determinism
argument).

**OCR is injected, not read ().** The specialist pass's text arrives as
``ocr_text`` and is rendered into the pack's ``## On-screen text … (INPUT, not
target)`` block; the system prompt forbids copying it out.

The tolerant parse ladder (``app.vision.parse``) is the contract of record: a
Flash-class 32B ignores rules, so every reply runs through the ladder. Guided
decoding is NOT requested (the prior ``VIDEO_VLM_STRUCTURED`` knob defaulted to a probe
that was never wired, i.e. off; pinned off here — the ladder converges guided and
unguided replies to the same ``ClipDesc``). An empty reply RAISES; a fallback is
counted by the caller via ``ParseOutcome``.
"""
from __future__ import annotations

import base64
import dataclasses
import logging
from typing import Any

import httpx

from .. import prompts
from ..budget import caption_word_bounds
from ..clip_types import ClipFrames, Frame
from ..parse import ParseOutcome, parse_clip

logger = logging.getLogger("data-processing.vision.clipcap.vlm")

# Runtime pack variants (): the 1-image fallback and the idle-screen profile.
# Both are inside the aggregate PACK_DIGEST the clipcap stage pins, so a change to
# either is caught by the registration digest gate.
_SINGLE_PACK = "screen-clip-single-v1"
_IDLE_PACK = "screen-clip-idle-v1"

# The pack templates put the task + JSON contract after the OCR block; we split there so
# the images land BETWEEN the OCR block and the task, keeping the task text LAST ().
# Absent (a reworded pack) -> the whole user text goes after the images (task still last).
_TASK_MARKER = "Reply with ONE JSON"


def make_async_client(timeout_s: float) -> httpx.AsyncClient:
    """Construct the VL HTTP client. A single patchable factory (tests inject an
 ``httpx.MockTransport`` here), and the one place a shared pool would later live."""
    return httpx.AsyncClient(timeout=timeout_s)


def _data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def _select_spec(scenario: str, k: int, idle: bool) -> prompts.PromptSpec:
    """Pick the runtime pack variant: single frame -> the K=1 pack; an idle clip -> the
 idle pack; else the scenario default. Falls back to the scenario pack if a named
 variant is missing from the registry (a registry gap, never a crash)."""
    name = _SINGLE_PACK if k <= 1 else (_IDLE_PACK if idle else None)
    if name is not None:
        try:
            return prompts.get(name)
        except KeyError:  # pragma: no cover - registry always ships the variants
            logger.warning("clip pack %r missing; using the scenario default", name)
    return prompts.select(scenario)


def _build_messages(
    spec: prompts.PromptSpec,
    frames: list[Frame],
    ocr_text: str,
    span_s: float,
    *,
    scenario: str,
    caption_rate: int,
) -> list[dict[str, Any]]:
    """Render the pack and assemble the multi-image user message ()."""
    lo, hi = caption_word_bounds(span_s, caption_rate)
    context = {
        "span_s": f"{span_s:.0f}",
        "scenario_label": prompts.scenario_label(scenario),
        "n": len(frames),
        "offsets": ", ".join(f"{f.t_offset_s:.1f}" for f in frames),
        "ocr_block": ocr_text.strip() or "(none)",
        "words_lo": lo,
        "words_hi": hi,
    }
    rendered = prompts.render(spec, **context)

    # rfind, not find: the injected OCR block sits BEFORE the task in the rendered text, so
    # an OCR string that itself contains the marker (a screenshot of these instructions) must
    # not steal the split — the real task is always the LAST occurrence.
    idx = rendered.user.rfind(_TASK_MARKER)
    head, tail = (rendered.user[:idx], rendered.user[idx:]) if idx >= 0 else ("", rendered.user)

    user_content: list[dict[str, Any]] = []
    if head.strip():
        user_content.append({"type": "text", "text": head.rstrip()})
    for k, f in enumerate(frames):
        user_content.append({"type": "text", "text": f"Frame {k} (+{f.t_offset_s:.1f}s):"})
        user_content.append({"type": "image_url", "image_url": {"url": _data_url(f.jpeg_lo)}})
    user_content.append({"type": "text", "text": tail.strip()})

    return [
        {"role": "system", "content": rendered.system},
        {"role": "user", "content": user_content},
    ]


async def describe(
    clip: ClipFrames,
    ocr_text: str,
    chunk_id: str,
    *,
    model: str,
    scenario: str,
    caption_rate: int,
    url: str,
    api_key: str,
    timeout_s: float,
) -> ParseOutcome:
    """One multi-image VLM call → the parse ladder's ``ParseOutcome``. Raises on no
 decodable frame, a choice-less 200, or an empty reply (the stage is optional under
 L7, so a raise is a HOLE + a cancelled cone, healed by redrive — never a blank
 caption persisted as truth).

 ``model`` / ``scenario`` / ``caption_rate`` are the caller's CODE PINS (vB
 identity); ``url`` / ``api_key`` / ``timeout_s`` are operational wire — the same
 served model behind any of them yields the same bytes (L4).
 """
    frames = [f for f in clip.frames if f.jpeg_lo is not None]
    if not frames:
        raise ValueError(
            f"clip chunk {chunk_id!r}: no decodable frame to caption — refusing a "
            "blank record"
        )

    spec = _select_spec(scenario, len(frames), clip.idle)
    messages = _build_messages(
        spec, frames, ocr_text, clip.span_s,
        scenario=scenario, caption_rate=caption_rate,
    )
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": spec.max_tokens,       # decode pins live in the pack (digest-pinned)
        "temperature": spec.temperature,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    client = make_async_client(timeout_s)
    try:
        resp = await client.post(f"{url}/v1/chat/completions", json=payload, headers=headers)
        resp.raise_for_status()
        body = resp.json()
    finally:
        await client.aclose()

    choices = body.get("choices") or []
    if not choices:  # a 200 with no choices is a misbehaving endpoint — raise clearly
        raise ValueError(f"clip VLM response carried no choices: {str(body)[:200]}")
    choice0 = choices[0]
    if choice0.get("finish_reason") == "length":
        logger.warning("clip caption truncated by max_tokens for chunk %s (pack=%s)",
                       chunk_id, spec.id)
    reply = ((choice0.get("message") or {}).get("content") or "").strip()

    outcome = parse_clip(reply)  # raises EmptyReplyError (ValueError) on an empty reply
    # Stamp the pack that rendered this prompt so the caller can label
    # dp_video_parse_fallback_total{pack,step} truthfully (cleanup round: the
    # inc previously carried only {step} and the labelled family never recorded).
    outcome = dataclasses.replace(outcome, pack=spec.id)
    logger.info(
        "clip captioned chunk %s: pack=%s k=%d step=%s app=%r",
        chunk_id, spec.id, len(frames), outcome.step, outcome.desc.app,
    )
    return outcome
