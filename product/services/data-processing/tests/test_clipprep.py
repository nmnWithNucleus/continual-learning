"""The ``clipprep`` sidecar stage: contract, gating, the two ffmpeg passes end to end, the
backend-conditional fallback, the ``-frame_pts`` frame-index guard, and determinism.

Drives the stage directly (WS-B ships no full clip-mode E2E — that is an integration
deliverable). Fixtures are built at test time by ``ffmpeg lavfi``; NO binaries, NO GPU, NO
network. The default keyframe suite proves it stays dormant off-mode.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.stagegraph.stage import StageContext, stages_for
from app.stages.video.clipprep import ClipPrepStage
from app.vision import clip
from app.vision.clip_types import ClipFrames, Delta, Frame
from tests import conftest_video as fx

requires_decoder = pytest.mark.skipif(
    not fx.ffmpeg_available(), reason="needs ffmpeg (the single canonical decoder)")

# data-processing service root (this file lives at <root>/tests/), so the grep test is
# independent of the working directory pytest happens to be invoked from.
_ROOT = Path(__file__).resolve().parent.parent
WS_B_SOURCES = [
    "app/vision/clip.py",
    "app/vision/delta.py",
    "app/stages/video/clipprep.py",
    "scripts/calibrate_delta.py",
    "tests/conftest_video.py",   # fixture generation must not reach for a probe/cut-detector
    # (test_delta.py / test_clipprep.py legitimately name the tokens in assertions/comments,
    # so they are excluded — the guarantee is about the clip PATH, not the tests of it.)
]


def _ctx(blob: bytes, *, span: float = 10.0, t_start_epoch: float = 1_700_000_000.0,
         chunk_id: str = "chunk-clip-test-0001") -> StageContext:
    c1 = fx.make_c1(t_start_epoch=t_start_epoch, span_seconds=span, chunk_id=chunk_id)
    return StageContext(c1=c1, blob=blob, settings=None, span_seconds=span)


# ================================ STAGE CONTRACT ======================================

def test_clipprep_is_registered_and_declares_the_contract():
    reg = {s.name: s for s in stages_for("video")}
    assert "clipprep" in reg, "clipprep must self-register under the video modality"
    cp = reg["clipprep"]
    assert cp.kind == "sidecar"
    assert cp.policy == "required"
    assert cp.needs == ()
    assert cp.provides == ("clip_frames", "delta", "vision_settings")
    # order 0 is the design; it collides with legacy keyframes(0) on the pre-integration
    # tree, and order is inert for a unit-less sidecar (see the stage docstring). It just
    # must be distinct from the legacy orders so discovery does not raise.
    legacy_orders = {s.order for s in reg.values() if s.name in ("keyframes", "captions")}
    assert cp.order not in legacy_orders


def test_clipprep_enabled_and_fragment_gate_on_clip_mode(monkeypatch):
    cp = ClipPrepStage()
    monkeypatch.setenv("VIDEO_PIPELINE", "clip")
    assert cp.enabled(None) is True
    assert cp.version_fragment(None) == "+cp-v1"
    assert cp.is_enabled(None) is True
    # keyframe (the safe default) and any unknown value -> dormant, empty fragment.
    monkeypatch.setenv("VIDEO_PIPELINE", "keyframe")
    assert cp.enabled(None) is False
    assert cp.version_fragment(None) == ""
    monkeypatch.setenv("VIDEO_PIPELINE", "typo-value")
    assert cp.enabled(None) is False
    # enabled() <=> version_fragment() != '' for every mode (WS-E2 binding forward-compat).
    for mode in ("clip", "keyframe", "typo-value", ""):
        monkeypatch.setenv("VIDEO_PIPELINE", mode)
        assert cp.enabled(None) == bool(cp.version_fragment(None))


# ============================ END TO END (mock backend) ================================

@requires_decoder
def test_run_sync_provides_slots_and_emits_no_units(monkeypatch):
    monkeypatch.setenv("VIDEO_BACKEND", "mock")
    result = ClipPrepStage().run_sync(_ctx(fx.appswitch_clip()))
    assert result.units == [], "clipprep is a sidecar that emits NO units"
    assert set(result.slots) == {"clip_frames", "delta", "vision_settings"}
    cf, d, cvs = result.slots["clip_frames"], result.slots["delta"], result.slots["vision_settings"]
    assert isinstance(cf, ClipFrames) and isinstance(d, Delta)
    assert isinstance(cvs, clip.ClipVisionSettings)
    # the vision_settings slot bundles the base VisionSettings AND the clip knobs
    assert cvs.clip.seconds_per_frame == 2.5
    assert cvs.backend == "mock"          # delegated to base VisionSettings
    assert cf.span_s == 10.0


@requires_decoder
def test_run_sync_role_separates_caption_and_ocr_frames(monkeypatch):
    """The caption grid carries ``jpeg_lo`` (768 for the VLM); the OCR events carry
    ``jpeg_hi`` (native 1728). An app switch fires exactly one OCR read."""
    monkeypatch.setenv("VIDEO_BACKEND", "mock")
    cf: ClipFrames = ClipPrepStage().run_sync(_ctx(fx.appswitch_clip())).slots["clip_frames"]
    assert cf.idle is False, "an app switch is a change event"
    assert len(cf.ocr_times) == 1
    lo_frames = [f for f in cf.frames if f.jpeg_lo is not None]
    hi_frames = [f for f in cf.frames if f.jpeg_hi is not None]
    assert len(lo_frames) == 4, "the caption grid is 4 frames @10s (K=clamp(ceil(10/2.5),2,12))"
    assert len(hi_frames) == 1, "one OCR frame at the change event"
    # OCR frames' offsets are exactly the reported ocr_times.
    assert sorted(round(f.t_offset_s, 3) for f in hi_frames) == sorted(cf.ocr_times)
    for f in cf.frames:                   # every rendition is decodable JPEG bytes
        for j in (f.jpeg_lo, f.jpeg_hi):
            if j is not None:
                assert j[:2] == b"\xff\xd8", "JPEG SOI marker"


@requires_decoder
def test_idle_chunk_flag_and_no_ocr_reads(monkeypatch):
    monkeypatch.setenv("VIDEO_BACKEND", "mock")
    # flat black, t_start NOT near a floor boundary -> no change, no floor read.
    cf: ClipFrames = ClipPrepStage().run_sync(
        _ctx(fx.flat_clip("black"), t_start_epoch=1_700_000_050.0)
    ).slots["clip_frames"]
    assert cf.idle is True
    assert cf.ocr_times == ()
    assert all(f.jpeg_hi is None for f in cf.frames), "no OCR reads -> no hi renditions"
    assert any(f.jpeg_lo is not None for f in cf.frames), "still a caption grid"


@requires_decoder
def test_caption_grid_scales_with_span(monkeypatch):
    monkeypatch.setenv("VIDEO_BACKEND", "mock")
    cf: ClipFrames = ClipPrepStage().run_sync(
        _ctx(fx.flat_clip("black", dur=60), span=60.0, t_start_epoch=1_700_000_050.0)
    ).slots["clip_frames"]
    lo = [f for f in cf.frames if f.jpeg_lo is not None]
    assert len(lo) == 12, "K caps at VIDEO_CLIP_MAX_FRAMES=12 @60s"


@requires_decoder
def test_run_sync_is_byte_identical_across_runs(monkeypatch):
    """Two runs over one fixture -> byte-identical ClipFrames AND Delta (fleet
    determinism, exit criterion / D-05 / §4 R4)."""
    monkeypatch.setenv("VIDEO_BACKEND", "mock")

    def sig(result):
        cf: ClipFrames = result.slots["clip_frames"]
        d: Delta = result.slots["delta"]
        return (
            d.times, d.cells, d.accum, cf.ocr_times, cf.idle, cf.span_s,
            tuple((f.index, f.t_offset_s,
                   hashlib.sha256(f.jpeg_lo or b"").hexdigest(),
                   hashlib.sha256(f.jpeg_hi or b"").hexdigest()) for f in cf.frames),
        )

    blob = fx.appswitch_clip()
    assert sig(ClipPrepStage().run_sync(_ctx(blob))) == sig(ClipPrepStage().run_sync(_ctx(blob)))


# ==================== BACKEND-CONDITIONAL DECODE FALLBACK ==============================

def test_ffmpeg_absent_under_mock_yields_synthetic(monkeypatch):
    """ffmpeg absent + MOCK backend -> a synthetic ClipFrames keeps the headless loop
    running (timing-less frames, no OCR reads, idle) — the same shape on every worker."""
    monkeypatch.setenv("VIDEO_BACKEND", "mock")
    monkeypatch.setattr(clip.shutil, "which", lambda name: None)
    result = ClipPrepStage().run_sync(_ctx(fx.appswitch_clip() if fx.ffmpeg_available() else b"x"))
    cf: ClipFrames = result.slots["clip_frames"]
    d: Delta = result.slots["delta"]
    assert cf.idle is True and cf.ocr_times == ()
    assert all(f.jpeg_lo is None and f.jpeg_hi is None for f in cf.frames)
    assert len(cf.frames) >= 1
    assert d.times == () and d.cells == () and d.accum == ()


def test_ffmpeg_absent_under_non_mock_raises(monkeypatch):
    """ffmpeg absent + a NON-mock backend -> RAISE. Placeholder frames must never persist
    as processed truth under a real dialect; the chunk is redelivered (exit criterion)."""
    monkeypatch.setenv("VIDEO_BACKEND", "vlm")
    monkeypatch.setattr(clip.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="no decodable clip frames"):
        ClipPrepStage().run_sync(_ctx(b"anything"))


def test_undecodable_blob_under_non_mock_raises(monkeypatch):
    monkeypatch.setenv("VIDEO_BACKEND", "vlm")
    with pytest.raises(RuntimeError, match="no decodable clip frames"):
        ClipPrepStage().run_sync(_ctx(b"not-a-real-video-blob-\x00\x01\x02"))


@requires_decoder
def test_undecodable_blob_under_mock_yields_synthetic(monkeypatch):
    monkeypatch.setenv("VIDEO_BACKEND", "mock")
    cf: ClipFrames = ClipPrepStage().run_sync(
        _ctx(b"not-a-real-video-blob-\x00\x01\x02")
    ).slots["clip_frames"]
    assert cf.idle is True and cf.ocr_times == ()


# ======================= THE -frame_pts FRAME-INDEX GUARD =============================

@requires_decoder
def test_requesting_a_frame_the_stream_lacks_raises(tmp_path):
    """The measured defect #24: batched extraction can silently write FEWER files than
    requested (exit 0), mis-assigning pixels/timestamps. Requesting a PTS the stream does
    not contain RAISES (FrameCountError), never silently drops."""
    src = str(tmp_path / "chunk.bin")
    Path(src).write_bytes(fx.appswitch_clip())
    # opening (n=0) is real; PTS 999999999 is not in the stream -> count shortfall -> raise.
    with pytest.raises(clip.FrameCountError):
        clip._pass_b(src, lo_targets=[(clip._OPENING, 0.0), (999999999, 7.5)],
                     hi_targets=[], lo_width=768, hi_width=1728, out_dir=str(tmp_path))


@requires_decoder
def test_pass_b_maps_present_frames_to_their_offsets(tmp_path):
    """When all requested frames exist, each target's bytes land under its own t_offset
    (mapping is value-safe: outputs are ordered by time and zipped to time-sorted targets)."""
    src = str(tmp_path / "chunk.bin")
    Path(src).write_bytes(fx.appswitch_clip())
    maps, times, pts = clip._pass_a(src, 2.0)          # times [2,4,6,8], real PTS
    lo = [(clip._OPENING, 0.0), (pts[0], times[0]), (pts[2], times[2])]
    hi = [(pts[1], times[1])]
    lo_map, hi_map = clip._pass_b(src, lo, hi, 768, 1728, str(tmp_path))
    assert set(lo_map) == {0.0, times[0], times[2]}
    assert set(hi_map) == {times[1]}
    for b in list(lo_map.values()) + list(hi_map.values()):
        assert b[:2] == b"\xff\xd8"                    # each is a real JPEG


@requires_decoder
def test_variable_frame_rate_clip_is_not_a_poison_pill(monkeypatch):
    """CFR-assumption regression: a valid VFR chunk (sparse frames, misleading duration_time)
    must be processed, not dead-lettered. The old N=round(t*fps) model requested frame
    indices far past the last real frame and raised FrameCountError on every backend."""
    monkeypatch.setenv("VIDEO_BACKEND", "mock")
    result = ClipPrepStage().run_sync(_ctx(fx.vfr_clip()))
    cf: ClipFrames = result.slots["clip_frames"]
    d: Delta = result.slots["delta"]
    assert len(cf.frames) >= 2, "the VFR clip decodes to several real frames"
    assert any(f.jpeg_lo is not None for f in cf.frames), "caption frames were extracted"
    assert len(d.times) >= 1, "the delta gate ran over the sparse frames"
    # every reported frame offset sits within the declared span (the true PTS, not a drift)
    for f in cf.frames:
        assert 0.0 <= f.t_offset_s <= cf.span_s


@requires_decoder
def test_frame_count_error_is_not_masked_by_synthetic_fallback(monkeypatch):
    """Exit criterion 4 at the STAGE level: a FrameCountError (a genuine decoder anomaly) is
    NEVER swallowed into the synthetic fallback — it propagates on EVERY backend, including
    mock. Only ClipDecodeError (undecodable) degrades to synthetic under mock."""
    monkeypatch.setenv("VIDEO_BACKEND", "mock")

    def boom(*a, **k):
        raise clip.FrameCountError("simulated missing frame")

    monkeypatch.setattr(clip, "_pass_b", boom)
    with pytest.raises(clip.FrameCountError):
        ClipPrepStage().run_sync(_ctx(fx.appswitch_clip()))


@requires_decoder
def test_calibrate_delta_prints_histogram_and_events_sweep(tmp_path, capsys):
    """Exit criterion 9: scripts/calibrate_delta.py prints the peak/spread histogram AND the
    events/chunk each candidate threshold would yield."""
    import importlib.util
    folder = tmp_path / "clips"
    folder.mkdir()
    (folder / "switch.mp4").write_bytes(fx.appswitch_clip())
    (folder / "flat.mp4").write_bytes(fx.flat_clip("black"))
    spec = importlib.util.spec_from_file_location(
        "calibrate_delta", str(_ROOT / "scripts" / "calibrate_delta.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rc = mod.main([str(folder)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "peak histogram" in out and "spread histogram" in out
    assert "events/chunk" in out and "idle_peak" in out


# ============================ NO ffprobe / NO scene ===================================

def test_clip_path_uses_no_container_probe_or_cut_detection():
    """Exit criterion: the tokens ``ffprobe`` and ``scene`` appear NOWHERE in the WS-B
    clip source — the span is a C1 field (no decoder-dependent identity) and the change
    metric is not a scene-cut detector (inert on screens)."""
    for rel in WS_B_SOURCES:
        text = (_ROOT / rel).read_text()
        assert "ffprobe" not in text, f"{rel} must not use a container-duration probe"
        assert "scene" not in text, f"{rel} must not use cut/scene detection"
