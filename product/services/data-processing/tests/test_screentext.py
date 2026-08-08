"""The ``screentext`` stage — a thin client over the ocr model server.

The stage is driven directly with a StageContext whose
``clients["ocr"]`` is a client-level fake — no server is spawned. The
centerpiece is the GOLDEN test: the fake returns the REAL ocr server's measured
``/infer`` result (servers/ocr/tests/fixtures/golden_regions.json, bit-stable per its
PROVENANCE.md) for the REAL committed input frame — so the ``ocr`` slot value pinned
here is exactly what the C6 real-fleet run must reproduce end to end.
"""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest

from app.stagegraph.stage import StageContext, StageOutput
from app.stages.video.screentext import (
    DEDUP_RATIO,
    MIN_CHARS,
    MIN_CONF,
    OCR_CHARS_PER_SEC,
    ScreentextStage,
    _match_frame,
    _normalize_bbox,
)
from app.vision.clip_types import ClipFrames, Frame

_OCR_FIXTURES = (Path(__file__).parent.parent / "servers" / "ocr" / "tests" / "fixtures")
GOLDEN_REGIONS = json.loads((_OCR_FIXTURES / "golden_regions.json").read_text())
GOLDEN_JPEG = (_OCR_FIXTURES / "screen_planning_notes.jpg").read_bytes()

# THE PIN (C6 contract): running the registered screentext stage over the committed
# 1280x800 input frame, with the real ocr server's regions and the stage's code pins
# (conf 0.60 / min-chars 4 / dedup 0.92 / 6 chars-per-second at span 60, one read at
# t=0), must yield EXACTLY this slot value. The real-fleet run (C6) reproduces it via
# the live server, whose golden result is byte-stable (see PROVENANCE.md).
GOLDEN_SLOT_VALUE = (
    "+0s titlebar: QuarterlyPlanningNotes · "
    "+0s main: Action items for the data pipeline · "
    "+0s main: Review theingestbacklogbeforeFriday · "
    "+0s sidebar: Ship the model server framework · "
    "+0s sidebar: $python -m pytest tests/ · "
    "+0s sidebar: 42passedin3.14s · "
    "+0s sidebar: Saved2minutesago"
)


class FakeOcrClient:
    """Client-level fake for the ocr server: records each /infer payload and returns a
    canned result (or raises). The wire shape is asserted here so a drift from the
    servers/common envelope fails in THIS suite, not on the fleet."""

    def __init__(self, result=None, fail_at: set[int] | None = None):
        self.result = result if result is not None else GOLDEN_REGIONS
        self.fail_at = fail_at or set()
        self.calls: list[dict] = []

    async def aclose(self) -> None:  # the app's client-shutdown path calls this
        pass

    async def infer(self, payload: dict) -> dict:
        i = len(self.calls)
        self.calls.append(payload)
        assert set(payload) == {"input_b64", "codec", "params"}
        assert payload["codec"] == "image/jpeg" and payload["params"] == {}
        base64.b64decode(payload["input_b64"], validate=True)  # real b64, decodable
        if i in self.fail_at:
            raise RuntimeError("replica down (fake)")
        return json.loads(json.dumps(self.result))  # a fresh copy per call


def _ctx(clip_frames: ClipFrames, client, span_seconds: float) -> StageContext:
    return StageContext(
        c1={"chunk_id": "chunk-st-1"}, blob=b"", span_seconds=span_seconds,
        inputs={"clipprep": StageOutput(value=None, bytes=clip_frames)},
        clients={"ocr": client},
    )


def _frames(n: int, jpeg: bytes | None = GOLDEN_JPEG, step: float = 5.0):
    return tuple(Frame(index=i, t_offset_s=i * step, jpeg_lo=None, jpeg_hi=jpeg)
                 for i in range(n))


def _run(stage: ScreentextStage, ctx: StageContext) -> StageOutput:
    return asyncio.run(stage.run_async(ctx))


# ------------------------------------------------------------------ declaration

def test_declaration():
    s = ScreentextStage()
    assert (s.name, s.modality, s.slot_name) == ("screentext", "video", "ocr")
    assert s.segment == "screentext.v1-ppocr.v1"
    assert s.needs == ("clipprep",)
    assert s.required is False          # L7: failure = hole + cancelled caption cone
    assert s.server == "ocr"
    assert type(s)._defines("run_async") and not type(s)._defines("run_sync")


def test_pins_are_the_v0_defaults():
    # The knob-disposition contract: the v0 env defaults, carried into code (L4).
    assert (MIN_CONF, MIN_CHARS, DEDUP_RATIO, OCR_CHARS_PER_SEC) == (0.60, 4, 0.92, 6.0)


# ------------------------------------------------------------------ the golden pin

def test_golden_regions_produce_the_pinned_slot_value():
    """The REAL server result verbatim -> the exact ocr slot value C6 must reproduce."""
    client = FakeOcrClient()
    cf = ClipFrames(frames=_frames(1), ocr_times=(0.0,), idle=False, span_s=60.0)
    out = _run(ScreentextStage(), _ctx(cf, client, 60.0))
    assert out.value == {"value": GOLDEN_SLOT_VALUE}
    assert out.bytes is None
    # One selected time -> exactly one /infer call, carrying the committed frame.
    assert len(client.calls) == 1
    assert base64.b64decode(client.calls[0]["input_b64"]) == GOLDEN_JPEG


def test_golden_value_is_deterministic():
    cf = ClipFrames(frames=_frames(1), ocr_times=(0.0,), idle=False, span_s=60.0)
    a = _run(ScreentextStage(), _ctx(cf, FakeOcrClient(), 60.0))
    b = _run(ScreentextStage(), _ctx(cf, FakeOcrClient(), 60.0))
    assert a.value == b.value == {"value": GOLDEN_SLOT_VALUE}


# ------------------------------------------------------------------ honesty (L11)

def test_no_ocr_times_is_ran_and_empty():
    """An idle chunk (no delta events, no floor read) still EMITS the slot: value ""
    is the honest empty claim — the coverage signal. Never omitted on success."""
    client = FakeOcrClient()
    cf = ClipFrames(frames=_frames(2), ocr_times=(), idle=True, span_s=10.0)
    out = _run(ScreentextStage(), _ctx(cf, client, 10.0))
    assert out.value == {"value": ""}
    assert client.calls == []           # nothing to read, nothing sent


def test_empty_regions_is_ran_and_empty():
    client = FakeOcrClient(result={"regions": []})
    cf = ClipFrames(frames=_frames(1), ocr_times=(0.0,), idle=False, span_s=10.0)
    out = _run(ScreentextStage(), _ctx(cf, client, 10.0))
    assert out.value == {"value": ""}
    assert len(client.calls) == 1


# ---------------------------------------------------------------------- error posture

def test_minority_of_frame_errors_is_absorbed():
    # 1 of 3 frames errors -> absorbed; the digest renders from the other two.
    client = FakeOcrClient(fail_at={1})
    cf = ClipFrames(frames=_frames(3), ocr_times=(0.0, 5.0, 10.0), idle=False, span_s=60.0)
    out = _run(ScreentextStage(), _ctx(cf, client, 60.0))
    assert len(client.calls) == 3
    assert out.value["value"].startswith("+0s titlebar: QuarterlyPlanningNotes")


def test_majority_of_frame_errors_raises():
    """>50% erroring RAISES — the stage is optional, so this becomes an ocr HOLE and a
    cancelled caption cone (v0 dead-lettered the chunk; v1 heals via redrive, L8)."""
    client = FakeOcrClient(fail_at={0, 1})
    cf = ClipFrames(frames=_frames(3), ocr_times=(0.0, 5.0, 10.0), idle=False, span_s=60.0)
    with pytest.raises(RuntimeError, match=">50%"):
        _run(ScreentextStage(), _ctx(cf, client, 60.0))


def test_missing_hires_frame_counts_as_an_error():
    # jpeg_hi=None on the only selected frame -> 1/1 errors -> majority -> raise.
    client = FakeOcrClient()
    cf = ClipFrames(frames=_frames(1, jpeg=None), ocr_times=(0.0,), idle=False, span_s=10.0)
    with pytest.raises(RuntimeError, match=">50%"):
        _run(ScreentextStage(), _ctx(cf, client, 10.0))
    assert client.calls == []           # never sent a b64-of-None


def test_selected_time_with_no_extracted_frame_is_skipped_not_attempted():
    client = FakeOcrClient()
    # ocr_times names 40.0 but the only frame is at t=0 (outside the 0.25s tolerance).
    cf = ClipFrames(frames=_frames(1), ocr_times=(0.0, 40.0), idle=False, span_s=60.0)
    out = _run(ScreentextStage(), _ctx(cf, client, 60.0))
    assert len(client.calls) == 1       # only the matchable time was attempted
    assert out.value["value"] == GOLDEN_SLOT_VALUE


# ------------------------------------------------------------------ client-side mapping

def test_match_frame_tolerance():
    frames = _frames(2, step=5.0)
    assert _match_frame(frames, 0.2) is frames[0]      # within 0.25s
    assert _match_frame(frames, 5.24) is frames[1]
    assert _match_frame(frames, 2.5) is None           # nothing within tolerance


def test_normalize_bbox_divides_pixels_and_passes_through_normalized():
    # Pixel coords (the ocr server's contract) divide by the frame dims...
    assert _normalize_bbox([640, 400, 1280, 800], 1280, 800) == (0.5, 0.5, 1.0, 1.0)
    # ...already-normalized coords pass through, never divided a second time.
    assert _normalize_bbox([0.1, 0.2, 0.3, 0.4], 1280, 800) == (0.1, 0.2, 0.3, 0.4)
    # Garbage degrades to the zero sentinel, never a crash.
    assert _normalize_bbox(["x"], 1280, 800) == (0.0, 0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Cleanup round: the wired stage-side families record real values (they were
# declared/seeded with no producer — the lying-zero shape).
# ---------------------------------------------------------------------------

def test_stage_metrics_record_errors_redactions_truncation(monkeypatch):
    from dataclasses import replace as dc_replace

    from app.metrics import Metrics
    from app.stages.video import screentext as screentext_mod

    metrics = Metrics()
    metrics.declare_counter("dp_ocr_frame_errors_total", "absorbed per-frame errors")
    metrics.declare_counter("dp_ocr_redactions_total", "redacted spans")
    metrics.declare_counter("dp_video_truncated_total", "budget truncations", ["pass"])

    # Deterministic render outcome: 2 redactions + a truncation, regardless of
    # what the (faked) OCR returned.
    monkeypatch.setattr(screentext_mod.assemble, "render",
                        lambda *a, **kw: ("digest", 2, True))
    clip = ClipFrames(frames=_frames(3, step=2.0), ocr_times=(0.0, 2.0, 4.0),
                      idle=False, span_s=60.0)
    client = FakeOcrClient(fail_at={1})  # exactly one absorbed frame error
    ctx = dc_replace(_ctx(clip, client, 60.0), metrics=metrics)
    out = _run(ScreentextStage(), ctx)
    assert out.value == {"value": "digest"}

    rendered = metrics.render()
    assert "dp_ocr_frame_errors_total 1" in rendered, rendered
    assert "dp_ocr_redactions_total 2" in rendered, rendered
    assert 'dp_video_truncated_total{pass="ocr"} 1' in rendered, rendered
