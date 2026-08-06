"""The ``clipcap`` stage — VLM thin client + absorbed render + the pack-digest gate.

New-API client tests, headless: the OpenAI-compatible endpoint is a fake
(``httpx.MockTransport`` injected through the ``vlm.make_async_client`` factory — the
client-level-fake rule, plan §3), so the production wire (D-02 payload, D-09 OCR
injection) is exercised with no model, and the caption slot value is PINNED.
"""
from __future__ import annotations

import asyncio
import base64
import importlib
import json

import httpx
import pytest

import app.stages.video.clipcap as clipcap_mod
from app.stagegraph.stage import (
    StageContext,
    StageOutput,
    StageRegistrationError,
)
from app.stagegraph import stage as stage_mod
from app.stages.video.clipcap import (
    CAPTION_RATE,
    MODEL,
    PACK_DIGEST_PIN,
    SCENARIO,
    ClipcapStage,
    render_caption,
)
from app.vision import prompts
from app.vision.clip_types import ClipDesc, ClipFrames, Frame
from app.vision.clipcap import vlm

_JPEG = base64.b64decode(  # a real tiny JPEG (SOI..EOI), enough for a data URL
    b"/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    b"HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    b"AAAAAAAAAAAACv/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AVN//2Q=="
)

CANNED_REPLY = {
    "app": "Xcode",
    "activity": "editing Swift code",
    "description": "The person edits a Swift file in Xcode, scrolling through a view controller.",
    "sensitive": False,
}


def _clip(n=3, span=60.0, idle=False):
    frames = tuple(Frame(index=i, t_offset_s=i * 2.5, jpeg_lo=_JPEG, jpeg_hi=None)
                   for i in range(n))
    return ClipFrames(frames=frames, ocr_times=(0.0,), idle=idle, span_s=span)


def _ctx(clip, ocr_text="+0s titlebar: Xcode — Editor", span=60.0):
    return StageContext(
        c1={"chunk_id": "chunk-cc-1"}, blob=b"", span_seconds=span,
        inputs={
            "clipprep": StageOutput(value=None, bytes=clip),
            "screentext": StageOutput(value={"value": ocr_text}),
        },
        clients={},
    )


def _fake_endpoint(monkeypatch, reply=CANNED_REPLY, *, capture: list | None = None,
                   status=200, raw_content: str | None = None):
    """Patch vlm.make_async_client with an httpx.MockTransport fake OpenAI endpoint."""
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": "boom"})
        content = raw_content if raw_content is not None else json.dumps(reply)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        })

    monkeypatch.setattr(
        vlm, "make_async_client",
        lambda timeout_s: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _run(ctx):
    return asyncio.run(ClipcapStage().run_async(ctx))


# ------------------------------------------------------------------ declaration + pins

def test_declaration():
    s = ClipcapStage()
    assert (s.name, s.modality, s.slot_name) == ("clipcap", "video", "caption")
    assert s.segment == "clipcap.v1-vlm.v1"
    assert s.needs == ("clipprep", "screentext")
    assert s.required is False
    assert s.server == ""               # external endpoint, not a fleet server
    assert type(s)._defines("run_async") and not type(s)._defines("run_sync")


def test_identity_pins():
    # The v0 env knobs, as code (L4): VIDEO_VLM_MODEL / VIDEO_SCENARIO /
    # VIDEO_CAPTION_CHARS_SHARE.
    assert MODEL == "Qwen/Qwen3-VL-32B-Instruct"
    assert SCENARIO == "screen-mac"
    assert CAPTION_RATE == 16


# ------------------------------------------------------------------ the pack-digest gate

def test_pack_digest_pin_matches_the_on_disk_pack():
    assert prompts.PACK_DIGEST == PACK_DIGEST_PIN


def test_pack_edit_without_vb_bump_fails_registration(monkeypatch):
    """The L4 hard gate: if the on-disk pack digests differently from the pin, the
    stage module REFUSES to import — a .prompt.md edit cannot ship without a vB bump."""
    monkeypatch.setattr(stage_mod, "_REGISTRY", {})     # throwaway registry
    monkeypatch.setattr(stage_mod, "_discovered", True)
    monkeypatch.setattr(prompts, "PACK_DIGEST", "deadbeef")
    with pytest.raises(StageRegistrationError, match="digest"):
        importlib.reload(clipcap_mod)
    # The guard raises BEFORE the class is redefined/registered, so the originally
    # registered stage (and this module's imports) remain intact.


# ------------------------------------------------------------------ the wire (D-02/D-09)

def test_call_produces_the_pinned_caption_slot():
    captured: list[httpx.Request] = []

    def run(monkeypatch):
        _fake_endpoint(monkeypatch, capture=captured)
        return _run(_ctx(_clip()))

    mp = pytest.MonkeyPatch()
    try:
        out = run(mp)
    finally:
        mp.undo()
    # The PINNED caption: render_caption over the canned reply at span 60/rate 16
    # (cap 960 — no truncation), D-10 lead + description.
    assert out.value == {"value":
        "Xcode — editing Swift code. The person edits a Swift file in Xcode, "
        "scrolling through a view controller."}
    assert out.bytes is None
    assert len(captured) == 1


def test_payload_shape_is_one_multi_image_call(monkeypatch):
    captured: list[httpx.Request] = []
    _fake_endpoint(monkeypatch, capture=captured)
    ocr_text = "+0s titlebar: Xcode — ViewController.swift"
    _run(_ctx(_clip(n=3), ocr_text=ocr_text))

    assert len(captured) == 1                       # ONE call (D-02), never per-frame
    req = captured[0]
    assert req.url.path == "/v1/chat/completions"
    body = json.loads(req.content)
    assert body["model"] == MODEL                   # the code-pinned served model
    assert body["stream"] is False
    # Decode params come from the pack front-matter (digest-pinned).
    spec = prompts.select(SCENARIO)
    assert body["max_tokens"] == spec.max_tokens
    assert body["temperature"] == spec.temperature
    assert "response_format" not in body            # guided decoding pinned OFF

    system, user = body["messages"]
    assert system["role"] == "system" and isinstance(system["content"], str)
    parts = user["content"]
    # Frame labels interleave before each image; the task text is LAST (D-02).
    images = [p for p in parts if p["type"] == "image_url"]
    labels = [p for p in parts if p["type"] == "text" and p["text"].startswith("Frame ")]
    assert len(images) == 3 and len(labels) == 3
    assert parts[-1]["type"] == "text" and "Reply with ONE JSON" in parts[-1]["text"]
    # D-09: the OCR text is injected INTO the prompt text, before the task.
    head = parts[0]
    assert head["type"] == "text" and ocr_text in head["text"]


def test_empty_ocr_text_renders_as_none_marker(monkeypatch):
    captured: list[httpx.Request] = []
    _fake_endpoint(monkeypatch, capture=captured)
    _run(_ctx(_clip(), ocr_text=""))                # ran-and-empty screentext
    body = json.loads(captured[0].content)
    head = body["messages"][1]["content"][0]["text"]
    assert "(none)" in head


def test_single_frame_uses_the_single_pack_and_idle_uses_idle(monkeypatch):
    captured: list[httpx.Request] = []
    _fake_endpoint(monkeypatch, capture=captured)
    _run(_ctx(_clip(n=1)))
    _run(_ctx(_clip(n=3, idle=True)))
    single = json.loads(captured[0].content)
    idle = json.loads(captured[1].content)
    assert single["max_tokens"] == prompts.get("screen-clip-single-v1").max_tokens
    assert idle["max_tokens"] == prompts.get("screen-clip-idle-v1").max_tokens


def test_operational_env_reaches_the_wire_but_not_the_bytes(monkeypatch):
    """VLM_URL/VLM_API_KEY are operational (L4): they move the request, never the
    slot value."""
    captured: list[httpx.Request] = []
    _fake_endpoint(monkeypatch, capture=captured)
    monkeypatch.setenv("VLM_URL", "http://elsewhere.test:9999")
    monkeypatch.setenv("VLM_API_KEY", "sekret")
    out = _run(_ctx(_clip()))
    req = captured[0]
    assert req.url.host == "elsewhere.test" and req.url.port == 9999
    assert req.headers["Authorization"] == "Bearer sekret"
    assert out.value["value"].startswith("Xcode — editing Swift code.")


# ------------------------------------------------------------------ failure posture (L7)

def test_endpoint_error_raises_for_a_hole(monkeypatch):
    _fake_endpoint(monkeypatch, status=502)
    with pytest.raises(httpx.HTTPStatusError):
        _run(_ctx(_clip()))


def test_empty_reply_raises(monkeypatch):
    _fake_endpoint(monkeypatch, raw_content="")
    with pytest.raises(ValueError):
        _run(_ctx(_clip()))


def test_no_decodable_frame_raises(monkeypatch):
    _fake_endpoint(monkeypatch)
    frames = (Frame(index=0, t_offset_s=0.0, jpeg_lo=None, jpeg_hi=None),)
    clip = ClipFrames(frames=frames, ocr_times=(), idle=True, span_s=10.0)
    with pytest.raises(ValueError, match="no decodable frame"):
        _run(_ctx(clip))


def test_malformed_reply_degrades_through_the_parse_ladder(monkeypatch):
    # A fenced/prose reply still yields a caption (the ladder is the contract of
    # record); the stage only raises on EMPTY.
    _fake_endpoint(monkeypatch, raw_content=(
        "Sure! Here is the JSON:\n```json\n" + json.dumps(CANNED_REPLY) + "\n```"))
    out = _run(_ctx(_clip()))
    assert "Xcode" in out.value["value"]


# ------------------------------------------------------------------ render (absorbed emit.py)

def _desc(app="Mail", activity="reading an email", description="A thread is open.",
          sensitive=False):
    return ClipDesc(app=app, activity=activity, description=description,
                    sensitive=sensitive, raw="", parsed=True)


def test_render_lead_and_description():
    assert render_caption(_desc(), 60.0) == "Mail — reading an email. A thread is open."


def test_render_degrades_without_dangling_separators():
    # D-10: no dangling " — ", no leading ". " when fields are empty.
    assert render_caption(_desc(app="", activity=""), 60.0) == "A thread is open."
    assert render_caption(_desc(activity=""), 60.0) == "Mail. A thread is open."
    assert render_caption(_desc(app=""), 60.0) == "reading an email. A thread is open."


def test_render_is_single_line(monkeypatch):
    d = _desc(description="line one\nline two\n\nline three")
    out = render_caption(d, 60.0)
    assert "\n" not in out and "  " not in out      # D-12


def test_render_truncates_at_the_span_budget_on_a_sentence():
    # cap = 16 * span. At span=2 -> 32 chars.
    d = _desc(description="First sentence here. Second sentence that will not fit at all.")
    out = render_caption(d, 2.0)
    assert len(out) <= 32
    assert out.endswith(".")                        # sentence boundary, not mid-word
