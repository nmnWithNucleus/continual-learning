"""Contract A client: build the Qwen3-VL chat request and relay the SSE token stream.

We are the *client* of WS1's vLLM (OpenAI-compatible) server. For one turn we:
  - build a length-1 (stateless, no history) chat request — either a video+text request
    (a `video_url` content part pointing at the saved clip via a `file://` URL, plus the
    user's text) or a **text-only** request (system + user text, no video part),
  - POST it with `stream=true` to /v1/chat/completions using an async httpx client,
  - parse the SSE chunks (`data: {...}` lines, terminated by `[DONE]`), pull
    `choices[].delta.content`, and yield that plain text incrementally.

We also ask vLLM for usage (`stream_options.include_usage=true`): the FINAL streamed
chunk has `choices:[]` and `usage:{prompt_tokens,completion_tokens,total_tokens}`. We
capture those into a caller-supplied dict (`usage_sink`) so /api/turn can emit a per-turn
metrics frame. Token *breakdown* (system / text / video) comes from the vLLM `/tokenize`
endpoint — see `token_breakdown()`.

The async generator returned by `stream_answer()` is fed straight into a FastAPI
`StreamingResponse`, so tokens reach the phone as they arrive (no buffering).
"""

from __future__ import annotations

import json
import time
from typing import AsyncIterator, Optional

import httpx

import config


def build_text_payload(prompt: str) -> dict:
    """Assemble a TEXT-ONLY chat request (no video content part).

    Used for the text-only turn: the user typed/spoke a question without recording a clip.
    Stateless / length-1, same system prompt as the video path.
    """
    return {
        "model": config.MODEL_ID,
        "stream": True,
        "max_tokens": config.MAX_NEW_TOKENS,
        "stream_options": {"include_usage": True},
        "messages": [
            {"role": "system", "content": config.SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }


def build_request_payload(clip_path: Optional[str], prompt: str) -> dict:
    """Assemble the Contract-A request body.

    With `clip_path` -> the video+text request (see HANDOFF.md -> Contract A): a
    `video_url` content part (`file://` URL, shared node FS) plus the user's text.
    With `clip_path=None` -> delegate to `build_text_payload()` (text-only turn).
    """
    if not clip_path:
        return build_text_payload(prompt)

    file_url = "file://" + clip_path  # clip_path is absolute -> file:///abs/...
    return {
        "model": config.MODEL_ID,
        "stream": True,
        "max_tokens": config.MAX_NEW_TOKENS,
        "stream_options": {"include_usage": True},
        "messages": [
            {"role": "system", "content": config.SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": file_url}},
                    {"type": "text", "text": prompt},
                ],
            },
        ],
        # NOTE: no per-request mm_processor_kwargs. vLLM ignores `extra_body` over the raw
        # HTTP API and does not honor max_pixels for video; fps (2.0) + num_frames (60) are
        # set at launch, and clip resolution is bounded by the backend ffmpeg normalization.
    }


def _extract_delta_text(chunk_obj: dict) -> str:
    """Pull plain text from one OpenAI streaming chunk's choices[].delta.content."""
    try:
        choices = chunk_obj.get("choices") or []
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str):
            return content
        # Some servers stream content as a list of parts; concatenate text parts.
        if isinstance(content, list):
            out = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    out.append(part["text"])
                elif isinstance(part, str):
                    out.append(part)
            return "".join(out)
    except Exception:
        return ""
    return ""


async def stream_answer(
    clip_path: Optional[str],
    prompt: str,
    usage_sink: Optional[dict] = None,
) -> AsyncIterator[str]:
    """Yield answer text chunks from vLLM as they stream in.

    `clip_path=None` streams a text-only turn (no video part). If `usage_sink` is given,
    it is populated in-place with per-turn metrics as they become known:
      - "ttft_ms":        ms from just-before the POST to the first content token
      - "inference_ms":   ms from just-before the POST to stream done
      - "prompt_total":   usage.prompt_tokens (from the final include_usage chunk)
      - "output":         usage.completion_tokens
    Best-effort: missing keys just mean that sub-step didn't report (the caller emits a
    best-effort metrics frame regardless).

    On any upstream/transport error, yields a final line `\n[error] <msg>` (Contract B)
    and returns cleanly — the caller just relays whatever this yields.
    """
    payload = build_request_payload(clip_path, prompt)
    timeout = httpx.Timeout(
        connect=config.VLLM_CONNECT_TIMEOUT,
        read=config.VLLM_READ_TIMEOUT,
        write=config.VLLM_READ_TIMEOUT,
        pool=config.VLLM_CONNECT_TIMEOUT,
    )

    sink = usage_sink if usage_sink is not None else {}
    t0 = time.monotonic()
    got_first = False
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                config.VLLM_CHAT_COMPLETIONS_URL,
                json=payload,
                headers={"Accept": "text/event-stream"},
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "replace")[:500]
                    yield f"\n[error] vLLM returned {resp.status_code}: {body}"
                    return

                async for raw_line in resp.aiter_lines():
                    line = raw_line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        # Skip non-JSON keepalive/comment lines defensively.
                        continue
                    # Capture usage from the final include_usage chunk (choices:[]).
                    usage = obj.get("usage")
                    if isinstance(usage, dict):
                        if usage.get("prompt_tokens") is not None:
                            sink["prompt_total"] = int(usage["prompt_tokens"])
                        if usage.get("completion_tokens") is not None:
                            sink["output"] = int(usage["completion_tokens"])
                    text = _extract_delta_text(obj)
                    if text:
                        if not got_first:
                            got_first = True
                            sink["ttft_ms"] = int((time.monotonic() - t0) * 1000)
                        yield text
        sink["inference_ms"] = int((time.monotonic() - t0) * 1000)
    except httpx.ConnectError:
        yield (
            "\n[error] could not reach the model server at "
            f"{config.VLLM_CHAT_COMPLETIONS_URL} (is WS1 vLLM up?)"
        )
    except httpx.ReadTimeout:
        yield "\n[error] model server timed out"
    except Exception as exc:  # last-resort guard so the stream always closes cleanly
        yield f"\n[error] {type(exc).__name__}: {exc}"


async def _tokenize_count(client: httpx.AsyncClient, body: dict) -> Optional[int]:
    """POST one body to vLLM /tokenize and return its `count`, or None on any failure."""
    try:
        resp = await client.post(config.VLLM_TOKENIZE_URL, json=body)
        if resp.status_code != 200:
            return None
        data = resp.json()
        cnt = data.get("count")
        return int(cnt) if cnt is not None else None
    except Exception:
        return None


async def token_breakdown(prompt: str, prompt_total: Optional[int]) -> dict:
    """Best-effort token breakdown via vLLM /tokenize.

    Returns a dict with int keys (0 where unknown):
      system   = tokens for SYSTEM_PROMPT alone
      text     = tokens for the user prompt alone
      non_video= system + text + chat-template tokens (NO video) for the messages
      video    = max(0, prompt_total - non_video)   (0 for text-only turns)
    Any sub-step that fails just contributes 0 — never raises.
    """
    out = {"system": 0, "text": 0, "video": 0}
    model = config.MODEL_ID
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            system = await _tokenize_count(client, {"model": model, "prompt": config.SYSTEM_PROMPT})
            text = await _tokenize_count(client, {"model": model, "prompt": prompt})
            non_video = await _tokenize_count(
                client,
                {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": config.SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "add_generation_prompt": True,
                },
            )
    except Exception:
        return out

    if system is not None:
        out["system"] = system
    if text is not None:
        out["text"] = text
    if prompt_total is not None and non_video is not None:
        out["video"] = max(0, int(prompt_total) - int(non_video))
    return out


async def health_check() -> Optional[str]:
    """Best-effort: return the model id if vLLM is reachable, else None."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
            resp = await client.get(config.VLLM_BASE_URL.rstrip("/") + "/v1/models")
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("data") or []
                if models:
                    return models[0].get("id")
                return config.MODEL_ID
    except Exception:
        return None
    return None
