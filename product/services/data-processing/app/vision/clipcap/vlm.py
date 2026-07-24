"""Real clip captioner — ONE multi-image VLM call per chunk (WS-D, D2; D-02/D-09).

LATE-BOUND (imported only when VIDEO_BACKEND=vlm): plain ``httpx.AsyncClient`` against an
OpenAI-compatible ``/v1/chat/completions`` — the same wire the inference service speaks to
the Qwen3-VL-32B served on the GPU node. No heavy dependency (httpx is a base dep), no GPU
in this process. Async so the long single-stream decode is loop-native (0 threadpool
tokens — §7.4), not a worker thread held for seconds.

**The payload is the design's headline (D-02): K stills in ONE call, NOT ``video_url``.**
Message shape: ``[{system}, {user: [head_text, Frame-label, image, Frame-label, image, …,
task_text]}]`` — the frame time labels ``Frame k (+t.s):`` interleaved before each image,
and the task + JSON contract LAST. Frames keep the payload identical between mock and real
backends (the headless test exercises the production wire) and keep frame selection on DP's
pinned ffmpeg rather than the serving box's decoder (the determinism argument, §12.1).

**OCR is injected, not read (D-09).** The specialist pass's text arrives as ``ocr_text``
and is rendered into the pack's ``## On-screen text … (INPUT, not target)`` block; the
system prompt forbids copying it out. The captioner reads layout at ``clip_frame_width`` and
never reads text — the low-res/high-res contradiction resolved.

The tolerant parse ladder (``app.vision.parse``) is the contract of record: guided decoding
is requested when available, but a Flash-class 32B ignores rules, so every reply is run
through the ladder. An empty reply RAISES (chunk redelivers); a fallback is counted.
"""
from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from .. import prompts
from ..clip_types import ClipDesc, ClipFrames, Frame
from ..config import VisionSettings
from ..parse import parse_clip

logger = logging.getLogger("data-processing.vision.clipcap.vlm")

# The base clip dialect for the real captioner (distinct from mock + from legacy).
PIPELINE_VERSION = "vidclip-vlm-v1"

# Runtime pack variants (D-03): the 1-image ``--limit-mm-per-prompt`` fallback and the
# idle-screen profile. Both fork the corpus via the aggregate PACK_DIGEST (D-13), so
# naming them here does not hide them from the dialect.
_SINGLE_PACK = "screen-clip-single-v1"
_IDLE_PACK = "screen-clip-idle-v1"

# The pack templates put the task + JSON contract after the OCR block; we split there so
# the images land BETWEEN the OCR block and the task, keeping the task text LAST (D-02).
# Absent (a reworded pack) -> the whole user text goes after the images (task still last).
_TASK_MARKER = "Reply with ONE JSON"


def make_async_client(vs: VisionSettings) -> httpx.AsyncClient:
    """Construct the VL HTTP client. A single patchable factory (tests inject an
    ``httpx.MockTransport`` here), and the one place a shared pool would later live."""
    return httpx.AsyncClient(timeout=vs.vlm_timeout)


def prompt_tag(vs: VisionSettings) -> str:
    """The prompt-pack dialect suffix (``@{pack}.p{V}.{DIGEST}``), resolver-pinned by
    ``prompts.version_tag`` so an unknown pack override stamps the default, never a
    bogus/colliding tag."""
    return prompts.version_tag(vs)


def _structured_enabled(vs: VisionSettings) -> bool:
    """Whether to request guided decoding (``response_format: json_schema``). ``on`` forces
    it; ``off`` disables it; ``auto`` (the default) stays OFF until a per-process capability
    probe confirms the server supports it — sending an unsupported ``response_format`` is a
    hard 400 on every chunk, whereas omitting it just makes the parse ladder work harder
    (``structured_mode`` is OPERATIONAL_ONLY precisely because both routes yield the same
    ClipDesc). The probe is wired when WS-A's E-3(a) lands; until then ``auto`` == off."""
    return vs.structured_mode == "on"


def _data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def _select_spec(vs: VisionSettings, k: int, idle: bool):
    """Pick the runtime pack variant: single frame -> the K=1 pack; an idle clip -> the
    idle pack; else the scenario default. Falls back to the scenario pack if a named
    variant is missing from the registry (a config gap, never a crash)."""
    name = _SINGLE_PACK if k <= 1 else (_IDLE_PACK if idle else None)
    if name is not None:
        try:
            return prompts.get(name)
        except KeyError:  # pragma: no cover - registry always ships the variants
            logger.warning("clip pack %r missing; using the scenario default", name)
    return prompts.select(vs)


def _build_messages(
    vs: VisionSettings, spec, frames: list[Frame], ocr_text: str, span_s: float
) -> list[dict[str, Any]]:
    """Render the pack and assemble the multi-image user message (D-02)."""
    lo, hi = prompts_word_bounds(vs, span_s)
    context = {
        "span_s": f"{span_s:.0f}",
        "scenario_label": prompts.scenario_label(vs.scenario),
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


def prompts_word_bounds(vs: VisionSettings, span_s: float) -> tuple[int, int]:
    """The (lo, hi) word band handed to the model, derived from the char budget. Imported
    lazily so this module has no hard import-time dependency on budget beyond call time."""
    from ..budget import caption_word_bounds

    return caption_word_bounds(span_s, vs)


def _count(metrics: Any, name: str, labels: dict[str, str]) -> None:
    """Null-guarded counter (metrics may be absent under subprocess isolation), mirroring
    the executor's guard — a metrics failure must never fail a chunk."""
    if metrics is None:
        return
    try:
        metrics.inc(name, labels)
    except Exception:  # pragma: no cover - metrics are best-effort
        logger.exception("metric inc failed: %s", name)


async def describe(
    vs: VisionSettings,
    clip: ClipFrames,
    ocr_text: str,
    c1: dict[str, Any],
    metrics: Any = None,
) -> ClipDesc:
    """One multi-image VLM call → a parsed ``ClipDesc``. Raises on no decodable frame or
    an empty reply (chunk not marked done → redelivery); counts truncation + parse
    fallbacks."""
    frames = [f for f in clip.frames if f.jpeg_lo is not None]
    if not frames:
        # Under the REAL dialect a chunk with no decodable frame must RAISE, not caption a
        # blank — matching keyframes.py refusing placeholder emission under vidproc-vlm-v0.
        raise ValueError(
            f"clip chunk {c1.get('chunk_id')!r}: no decodable frame to caption "
            "(ffmpeg absent/undecodable) — refusing a blank record under the vlm dialect"
        )

    spec = _select_spec(vs, len(frames), clip.idle)
    messages = _build_messages(vs, spec, frames, ocr_text, clip.span_s)
    payload: dict[str, Any] = {
        "model": vs.vlm_model,
        "messages": messages,
        "max_tokens": spec.max_tokens,
        "temperature": spec.temperature,
        "stream": False,
    }
    if _structured_enabled(vs) and spec.schema:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": spec.schema_name or "clip", "schema": spec.schema, "strict": True},
        }
    headers = {"Authorization": f"Bearer {vs.vlm_api_key}"} if vs.vlm_api_key else {}

    client = make_async_client(vs)
    try:
        resp = await client.post(f"{vs.vlm_url}/v1/chat/completions", json=payload, headers=headers)
        resp.raise_for_status()
        body = resp.json()
    finally:
        await client.aclose()

    choices = body.get("choices") or []
    if not choices:  # a 200 with no choices is a misbehaving endpoint — raise clearly
        raise ValueError(f"clip VLM response carried no choices: {str(body)[:200]}")
    choice0 = choices[0]
    if choice0.get("finish_reason") == "length":
        _count(metrics, "dp_video_truncated_total", {"pass": "caption"})
    reply = ((choice0.get("message") or {}).get("content") or "").strip()

    outcome = parse_clip(reply)  # raises EmptyReplyError (ValueError) on an empty reply
    if outcome.fallback:
        _count(metrics, "dp_video_parse_fallback_total", {"pack": spec.id, "step": outcome.step})
    logger.info(
        "clip captioned chunk %s: pack=%s k=%d step=%s app=%r",
        c1.get("chunk_id"), spec.id, len(frames), outcome.step, outcome.desc.app,
    )
    return outcome.desc
