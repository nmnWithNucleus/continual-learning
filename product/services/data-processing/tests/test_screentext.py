"""WS-C seam: the `screentext` stage — frozen wiring, resolver-driven fragment/enabledness,
the always-one `kind='ocr'` unit, per-frame error absorption, redaction counting, and the
sidecar /health gate. Headless: mock default, no network, no GPU. The stage is driven
directly (its unit-test contract) and once through the real executor with a fake clipprep.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.processing.base import ProcessedContent, ProcessedUnit
from app.stagegraph import Stage, StageContext, StageResult
from app.stagegraph.executor import resolve, run_graph
from app.vision.clip_types import ClipFrames, Frame, OcrRead, OcrRegion
from app.vision.ocr import mock as ocr_mock
from app.vision.ocr import ppocr
from app.stages.video.screentext import ScreentextStage, _match_frame


class FakeMetrics:
    def __init__(self):
        self.counts: dict[str, float] = {}

    def inc(self, name, labels=None, amount=1.0):
        self.counts[name] = self.counts.get(name, 0.0) + amount

    def observe(self, *a, **k):
        pass


def _clipframes(ocr_times=(0.0, 5.0), span=10.0, hi=b"jpeg"):
    frames = tuple(
        Frame(index=i, t_offset_s=t, jpeg_lo=None, jpeg_hi=hi)
        for i, t in enumerate(ocr_times)
    )
    return ClipFrames(frames=frames, ocr_times=tuple(ocr_times), idle=False, span_s=span)


def _ctx(clip_frames, *, chunk_id="chunk-ULID-1", span=10.0, metrics=None):
    return StageContext(
        c1={"chunk_id": chunk_id, "t_start": "2026-07-09T12:00:00Z", "t_end": "2026-07-09T12:00:10Z"},
        blob=b"", settings=None, span_seconds=span,
        slots={"clip_frames": clip_frames},
        resources=SimpleNamespace(metrics=metrics),
    )


def _run(stage, ctx):
    return stage.run_sync(ctx)


# ============================ frozen wiring ====================================

def test_frozen_wiring():
    s = ScreentextStage()
    assert s.name == "screentext"
    assert s.modality == "video"
    assert s.kind == "sidecar"
    assert s.policy == "required"
    assert s.needs == ("clipprep",)
    assert s.provides == ("ocr_text",)
    assert s.order == 15   # locked clip band; 10 is the legacy captions stage
    # run_sync (blocking HTTP to loopback), not run_async.
    assert type(s)._defines("run_sync") and not type(s)._defines("run_async")


def test_enabled_only_in_clip_mode(monkeypatch):
    s = ScreentextStage()
    monkeypatch.setenv("VIDEO_PIPELINE", "clip")
    assert s.enabled(None) is True
    monkeypatch.setenv("VIDEO_PIPELINE", "keyframe")
    assert s.enabled(None) is False
    monkeypatch.setenv("VIDEO_PIPELINE", "garbage")   # unknown -> keyframe (safe default)
    assert s.enabled(None) is False


def test_version_fragment_tracks_backend(monkeypatch):
    s = ScreentextStage()
    monkeypatch.setenv("VIDEO_OCR_BACKEND", "mock")
    assert s.version_fragment(None) == "+ocr-mock-v1"
    monkeypatch.setenv("VIDEO_OCR_BACKEND", "off")
    assert s.version_fragment(None) == "+ocr-off-v1"
    monkeypatch.setenv("VIDEO_OCR_BACKEND", "ppocr")
    assert s.version_fragment(None) == "+ocr-ppv4-cpu-v1"
    monkeypatch.setenv("VIDEO_OCR_BACKEND", "typo")   # unknown -> off, still non-empty (R1)
    assert s.version_fragment(None) == "+ocr-off-v1"


# ============================ the always-one ocr unit (D-05/D-08) ===============

def _assert_single_ocr_unit(result: StageResult):
    assert len(result.units) == 1
    u = result.units[0]
    assert u.content.kind == "ocr"
    assert u.discriminator == "ocr"
    assert u.t_start is None and u.t_end is None
    assert "\n" not in u.content.text and "\r" not in u.content.text
    # the provided slot equals the record text — one witness, two channels (R2 corollary).
    assert result.slots["ocr_text"] == u.content.text
    return u


def test_mock_backend_emits_one_ocr_unit_with_text(monkeypatch):
    monkeypatch.setenv("VIDEO_OCR_BACKEND", "mock")
    result = _run(ScreentextStage(), _ctx(_clipframes()))
    u = _assert_single_ocr_unit(result)
    assert u.content.text != ""            # mock produces legible text
    assert "+0s" in u.content.text         # self-anchored offsets


def test_off_backend_still_emits_one_empty_ocr_unit(monkeypatch):
    monkeypatch.setenv("VIDEO_OCR_BACKEND", "off")
    result = _run(ScreentextStage(), _ctx(_clipframes()))
    u = _assert_single_ocr_unit(result)
    assert u.content.text == ""            # off -> no OCR, but the record ALWAYS exists


def test_no_ocr_frames_still_emits_one_empty_unit(monkeypatch):
    monkeypatch.setenv("VIDEO_OCR_BACKEND", "mock")
    result = _run(ScreentextStage(), _ctx(_clipframes(ocr_times=())))
    u = _assert_single_ocr_unit(result)
    assert u.content.text == ""


def test_unknown_backend_behaves_like_off(monkeypatch):
    monkeypatch.setenv("VIDEO_OCR_BACKEND", "not-a-backend")
    result = _run(ScreentextStage(), _ctx(_clipframes()))
    u = _assert_single_ocr_unit(result)
    assert u.content.text == ""


# ============================ per-frame error absorption (A-10) =================

def test_minority_frame_errors_absorbed_and_counted(monkeypatch):
    monkeypatch.setenv("VIDEO_OCR_BACKEND", "mock")
    calls = {"n": 0}
    real_read = ocr_mock.read

    def flaky(cfg, frame, client, chunk_id):
        calls["n"] += 1
        if frame.index == 0:            # 1 of 4 frames fails -> 25% <= 50% -> absorbed
            raise RuntimeError("boom")
        return real_read(cfg, frame, client, chunk_id)

    monkeypatch.setattr(ocr_mock, "read", flaky)
    metrics = FakeMetrics()
    result = _run(ScreentextStage(), _ctx(_clipframes(ocr_times=(0.0, 2.5, 5.0, 7.5)), metrics=metrics))
    _assert_single_ocr_unit(result)                       # still emits its record
    assert metrics.counts.get("dp_ocr_frame_errors_total") == 1
    assert metrics.counts.get("dp_video_ocr_events") == 4  # frames attempted


def test_majority_frame_errors_raises(monkeypatch):
    monkeypatch.setenv("VIDEO_OCR_BACKEND", "mock")

    def always_boom(cfg, frame, client, chunk_id):
        raise RuntimeError("sidecar down")

    monkeypatch.setattr(ocr_mock, "read", always_boom)
    with pytest.raises(RuntimeError, match="refusing to write a corpus"):
        _run(ScreentextStage(), _ctx(_clipframes(ocr_times=(0.0, 5.0))))


# ============================ redaction counter through the stage ==============

def test_redaction_counter_incremented(monkeypatch):
    monkeypatch.setenv("VIDEO_OCR_BACKEND", "mock")

    def leak(cfg, frame, client, chunk_id):
        return OcrRead(t_offset_s=frame.t_offset_s, regions=(
            OcrRegion(text="key AKIAIOSFODNN7EXAMPLE here", role="", bbox=(0.1, 0.4, 0.9, 0.45), conf=0.99),
        ))

    monkeypatch.setattr(ocr_mock, "read", leak)
    metrics = FakeMetrics()
    result = _run(ScreentextStage(), _ctx(_clipframes(ocr_times=(0.0,)), metrics=metrics))
    u = result.units[0]
    assert "[redacted:secret]" in u.content.text
    assert "AKIAIOSFODNN7EXAMPLE" not in u.content.text
    assert metrics.counts.get("dp_ocr_redactions_total") == 1


# ============================ /health gate (D-06) ==============================

def test_ppocr_health_mismatch_fails_the_stage(monkeypatch):
    monkeypatch.setenv("VIDEO_OCR_BACKEND", "ppocr")
    monkeypatch.setenv("VIDEO_OCR_MODEL_SHA_DET", "EXPECTED")
    monkeypatch.setenv("VIDEO_OCR_MODEL_SHA_REC", "R1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "model_sha_det": "SWAPPED", "model_sha_rec": "R1"})

    monkeypatch.setattr(ppocr, "make_client",
                        lambda c: httpx.Client(transport=httpx.MockTransport(handler)))
    ppocr._HEALTH_VERIFIED.clear()
    with pytest.raises(RuntimeError, match="mismatch"):
        _run(ScreentextStage(), _ctx(_clipframes()))


# ============================ frame matching ===================================

def test_match_frame_nearest_within_tolerance():
    frames = (
        Frame(index=0, t_offset_s=0.0, jpeg_lo=None, jpeg_hi=b"a"),
        Frame(index=1, t_offset_s=5.01, jpeg_lo=None, jpeg_hi=b"b"),
    )
    assert _match_frame(frames, 5.0).index == 1     # within tol
    assert _match_frame(frames, 9.9) is None        # no frame near a selected time


# ============================ real-registry discovery (integration base) =======

def test_screentext_registered_and_wired_in_real_registry(monkeypatch):
    """Integration-base check — SKIPPED when clipprep (WS-B) is absent (e.g. a WS-C-only
    branch, where unconditional registration makes _discover() raise on the dangling need).

    Unlike the hand-built executor test below, this drives the REAL stage registry, so it is
    the only test that catches a registration / order bug: screentext must be discoverable at
    order 15 with NO duplicate orders (order 10 would collide with the legacy `captions`
    stage), and clip-mode resolve() must wire it under exactly one primary with its fragment
    composed into pipeline_version (R1). run_graph emission of the ocr unit is covered by
    test_screentext_through_the_executor; the full clip-mode E2E is C3's integration deliverable.
    """
    import importlib.util
    if importlib.util.find_spec("app.stages.video.clipprep") is None:
        pytest.skip("clipprep (WS-B) absent — this is an integration-base test")

    from app.stagegraph.stage import stages_for

    video = stages_for("video")                 # triggers the real _discover()
    by_name = {s.name: s for s in video}
    # (1) THE masked-bug catcher: real-registry registration + order 15 + no collision.
    assert "screentext" in by_name, "screentext must be statically discoverable"
    assert by_name["screentext"].order == 15
    orders = [s.order for s in video]
    assert len(orders) == len(set(orders)), f"duplicate video stage orders: {sorted(orders)}"

    # (2) clip-mode wiring — asserted only once the clip graph is coherent: it also needs
    # the legacy `captions` primary gated to keyframe mode (WS-G) and a real clip primary
    # (WS-D). On a partial integration base those are absent, so guard on exactly one
    # enabled clip primary rather than assuming the full graph; the registration/order
    # check above is the load-bearing assertion either way.
    monkeypatch.setenv("VIDEO_PIPELINE", "clip")
    monkeypatch.setenv("VIDEO_OCR_BACKEND", "mock")
    clip_primaries = [s for s in video if s.kind == "primary" and s.is_enabled(None)]
    if len(clip_primaries) == 1:
        resolved = resolve("video", video, None)
        assert "screentext" in {s.name for s in resolved.enabled}      # wired in clip mode
        assert resolved.primary.name == clip_primaries[0].name         # the single clip primary
        assert "+ocr-mock-v1" in resolved.pipeline_version             # fragment composed in (R1)


# ============================ real executor path (with a fake clipprep) ========

def test_screentext_through_the_executor(monkeypatch):
    """Resolve + run_graph a minimal video-shaped graph (fake clipprep provides
    clip_frames, fake primary emits a caption) and assert screentext lands exactly one
    ocr unit with the frozen shape alongside the primary."""
    monkeypatch.setenv("VIDEO_PIPELINE", "clip")
    monkeypatch.setenv("VIDEO_OCR_BACKEND", "mock")

    class FakeClipprep(Stage):
        name = "clipprep"; modality = "video"; kind = "sidecar"; order = 0
        provides = ("clip_frames",)
        def run_sync(self, ctx):
            return StageResult(slots={"clip_frames": _clipframes(ocr_times=(0.0, 5.0))})

    class FakePrimary(Stage):
        name = "clipcap"; modality = "video"; kind = "primary"; order = 20
        needs = ("clipprep", "screentext")
        def version_fragment(self, settings): return "vidclip-test-v1"
        def run_sync(self, ctx): return StageResult()
        def assemble(self, ctx):
            return [ProcessedUnit(content=ProcessedContent(kind="caption", text="a caption"),
                                  discriminator="")]

    stages = [FakePrimary(), FakeClipprep(), ScreentextStage()]
    resolved = resolve("video", stages, None)
    # screentext's fragment composes into the dialect (it feeds the caption -> R1).
    assert "+ocr-mock-v1" in resolved.pipeline_version
    ctx = _ctx(None)                                  # clip_frames comes from FakeClipprep
    ctx.slots.clear()
    units = asyncio.run(run_graph(resolved, ctx))
    kinds = [(u.content.kind, u.discriminator) for u in units]
    assert ("caption", "") in kinds
    ocr_units = [u for u in units if u.content.kind == "ocr"]
    assert len(ocr_units) == 1
    assert ocr_units[0].discriminator == "ocr" and ocr_units[0].t_start is None
    assert ctx.slots["ocr_text"] == ocr_units[0].content.text
