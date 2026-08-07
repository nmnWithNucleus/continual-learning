"""The ``clipprep`` stage — real ffmpeg over generated fixtures.

The stage runs REAL ffmpeg (the two passes) over ``ffmpeg lavfi``
fixtures built at test time (tests/conftest_video.py — no binaries committed).
The contract proven here: frames land in ``StageOutput.bytes`` and NO record slot
is emitted (``value=None`` — frames are re-derivable heavy data, not-persisted by
default under L5); an undecodable blob RAISES (required stage → the chunk attempt
fails; the v0 mock synthetic fallback is dead).
"""
from __future__ import annotations

import hashlib

import pytest

from app.stagegraph.executor import resolve, run_graph
from app.stagegraph.stage import StageContext
from app.stages.video.clipprep import CLIP_SETTINGS, ClipPrepStage
from app.vision.clip import ClipDecodeError, caption_frame_count
from app.vision.clip_types import ClipFrames
from tests import conftest_video as fx

requires_decoder = pytest.mark.skipif(
    not fx.ffmpeg_available(), reason="needs ffmpeg (the single canonical decoder)")

EPOCH = 1_700_000_000.0   # NOT a 120s-multiple +offset trap: next floor boundary at +40s


def _ctx(blob: bytes, span: float = 10.0) -> StageContext:
    c1 = fx.make_c1(t_start_epoch=EPOCH, span_seconds=span)
    return StageContext(c1=c1, blob=blob, span_seconds=span, inputs={}, clients={})


# ------------------------------------------------------------------ declaration + pins

def test_declaration():
    s = ClipPrepStage()
    assert (s.name, s.modality) == ("clipprep", "video")
    assert s.segment == "clipprep.v1-ffmpeg.v1"
    assert s.needs == () and s.required is True and s.server == ""
    assert type(s)._defines("run_sync") and not type(s)._defines("run_async")


def test_pins_are_the_v0_defaults():
    # The knob-disposition contract: every output-affecting VIDEO_* env knob of the
    # v0 clip prep, pinned in code at its v0 default (L4). Changing one = vB bump.
    assert CLIP_SETTINGS.seconds_per_frame == 2.5
    assert CLIP_SETTINGS.max_frames == 12
    assert CLIP_SETTINGS.min_frames == 2
    assert CLIP_SETTINGS.frame_width == 768
    assert CLIP_SETTINGS.ocr_frame_width == 1728
    assert CLIP_SETTINGS.analysis_period_s == 2.0
    assert CLIP_SETTINGS.ocr_idle_peak == 8
    assert CLIP_SETTINGS.ocr_layout_peak == 40
    assert CLIP_SETTINGS.ocr_max_events == 3
    assert CLIP_SETTINGS.ocr_floor_s == 120.0


# ------------------------------------------------------------------ the real passes

@requires_decoder
def test_appswitch_yields_frames_in_bytes_and_no_slot():
    out = ClipPrepStage().run_sync(_ctx(fx.appswitch_clip()))
    assert out.value is None                       # NO record slot — transient prep only
    cf = out.bytes
    assert isinstance(cf, ClipFrames)
    assert cf.span_s == 10.0
    # K = clamp(ceil(10/2.5), 2, 12) = 4 caption frames, all with lo-res pixels.
    assert caption_frame_count(10.0, CLIP_SETTINGS) == 4
    lo_frames = [f for f in cf.frames if f.jpeg_lo is not None]
    assert len(lo_frames) == 4
    assert all(f.jpeg_lo.startswith(b"\xff\xd8") for f in lo_frames)   # real JPEGs
    # The black->white cut at 5s fires exactly one change event; its frame carries
    # the hi-res (native-width) rendition for OCR.
    assert not cf.idle
    assert len(cf.ocr_times) == 1 and 4.0 <= cf.ocr_times[0] <= 8.0
    hi_frames = [f for f in cf.frames if f.jpeg_hi is not None]
    assert len(hi_frames) == 1
    assert abs(hi_frames[0].t_offset_s - cf.ocr_times[0]) <= 0.25


@requires_decoder
def test_flat_clip_is_idle_with_no_ocr_reads():
    out = ClipPrepStage().run_sync(_ctx(fx.flat_clip()))
    cf = out.bytes
    assert cf.idle is True
    assert cf.ocr_times == ()                      # floor (120s) is outside a 10s span
    assert [f for f in cf.frames if f.jpeg_lo is not None]  # caption frames still exist


@requires_decoder
def test_vfr_clip_decodes_instead_of_dead_lettering():
    # The CFR poison-pill regression: sparse irregular PTS must degrade gracefully
    # (fewer frames), never raise a FrameCountError.
    out = ClipPrepStage().run_sync(_ctx(fx.vfr_clip(), span=11.0))
    cf = out.bytes
    assert isinstance(cf, ClipFrames) and len(cf.frames) >= 1


@requires_decoder
def test_determinism_same_bytes_same_frames():
    ctx = _ctx(fx.appswitch_clip())

    def digest():
        cf = ClipPrepStage().run_sync(ctx).bytes
        h = hashlib.sha256()
        for f in cf.frames:
            h.update(str((f.index, f.t_offset_s)).encode())
            h.update(f.jpeg_lo or b"\x00")
            h.update(f.jpeg_hi or b"\x00")
        h.update(str(cf.ocr_times).encode())
        return h.hexdigest()

    assert digest() == digest()


def test_undecodable_blob_raises_never_placeholders():
    """Required-stage posture (L7): no synthetic frames, no record — the chunk attempt
    fails and the worker's retry/dead-letter taxonomy owns it."""
    with pytest.raises(ClipDecodeError):
        ClipPrepStage().run_sync(_ctx(b"not-a-video-blob-\x00\x01\x02"))
    with pytest.raises(ClipDecodeError):
        ClipPrepStage().run_sync(_ctx(b""))


# ------------------------------------------------------------------ through the executor

@requires_decoder
def test_through_executor_no_slot_and_bytes_freed():
    import asyncio

    resolved = resolve("video", [ClipPrepStage()])
    c1 = fx.make_c1(t_start_epoch=EPOCH, span_seconds=10.0)

    async def _main():
        return await run_graph(resolved, c1=c1, blob=fx.flat_clip(),
                               span_seconds=10.0, clients={})

    result = asyncio.run(_main())
    assert result.statuses == {"clipprep": "ok"}
    assert result.slots == {}                      # value=None ⇒ no slot, ever
    assert result.pipeline_version == "clipprep.v1-ffmpeg.v1"
