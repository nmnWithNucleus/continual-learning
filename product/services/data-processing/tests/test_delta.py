"""The delta gate: the floor, the six D-04 calibration vectors, classification, the OCR
selector (floor convention + rank-free cap + anchor), and determinism.

Drives ``app/vision/delta.py`` and ``app/vision/clip.py``'s Pass A directly (no full
clip-mode E2E lives here — that is an integration deliverable). Fixtures are built at test time by
``ffmpeg lavfi``; NO binaries, NO GPU, NO network.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.vision import clip, delta
from app.vision.clip_types import Delta, DeltaCell
from tests import conftest_video as fx

requires_decoder = pytest.mark.skipif(
    not fx.ffmpeg_available(), reason="needs ffmpeg (the single canonical decoder)")
requires_font = pytest.mark.skipif(
    not fx.font_available(), reason="needs a drawtext-capable font (libfreetype+fontconfig)")

_FFMPEG = "ffmpeg"


def _analyze(blob: bytes, tmp_path, period: float = 2.0):
    """Run Pass A over a blob -> (change_maps, delta_times, delta_pts_ints)."""
    src = str(tmp_path / "chunk.bin")
    Path(src).write_bytes(blob)
    return clip._pass_a(src, period)


def _peaks(maps):
    return [delta.summarize_map(m).peak for m in maps]


def _spreads(maps):
    return [delta.summarize_map(m).spread for m in maps]


# ============================ THE FLOOR: exactly 2 =====================================

@requires_decoder
@pytest.mark.parametrize("color", ["black", "white", "gray"])
def test_floor_is_exactly_two_on_flat(color, tmp_path):
    """CRITICAL exit criterion (D-04): the measured delta FLOOR is EXACTLY 2 on flat black,
    flat white and flat 50 %-gray — a content-independent artefact of the area downscale
    (verified identical under lossless ffv1, so it is the scaler, not the codec). spread 0."""
    maps, times, _ = _analyze(fx.flat_clip(color), tmp_path)
    assert maps, "flat clip must yield deltas"
    for m in maps:
        cell = delta.summarize_map(m)
        # The floor is a deterministic artefact of ffmpeg's area scaler at this resolution.
        # A value other than 2 here is an ffmpeg-BUILD difference (the ffmpeg version is
        # pinned), NOT a code regression — clipprep never post-processes this value, it
        # comes straight out of the pinned Pass-A filter chain.
        assert cell.peak == 2, (
            f"{color}: floor peak must be exactly 2, got {cell.peak} — check the ffmpeg build "
            f"matches the pinned version (area-scaler rounding is build-specific)")
        assert cell.spread == 0, f"{color}: floor spread must be 0, got {cell.spread}"


# ==================== THE SIX D-04 CALIBRATION VECTORS reproduce ========================

@requires_decoder
def test_vector_app_switch_saturates_one_delta(tmp_path):
    """App switch: ONE delta saturates (peak 255, spread 1024 = full-frame change) and the
    rest sit at the floor (peak 2). The record set is unaffected either way (D-05)."""
    maps, times, _ = _analyze(fx.appswitch_clip(), tmp_path)
    peaks, spreads = _peaks(maps), _spreads(maps)
    saturated = [i for i, (p, s) in enumerate(zip(peaks, spreads)) if p == 255 and s == 1024]
    assert len(saturated) == 1, f"exactly one saturated delta expected, got peaks={peaks}"
    for i, (p, s) in enumerate(zip(peaks, spreads)):
        if i not in saturated:
            assert p == 2 and s == 0, f"non-switch delta {i} must be floor, got ({p},{s})"


@requires_decoder
def test_vector_scroll_is_sustained_layout(tmp_path):
    """Scrolling content: LAYOUT-scale change SUSTAINED across every delta (peak 255,
    spread 1024) — distinct from the switch's single saturated delta."""
    maps, times, _ = _analyze(fx.scroll_clip(), tmp_path)
    peaks, spreads = _peaks(maps), _spreads(maps)
    assert all(p == 255 for p in peaks), f"scroll peaks not sustained: {peaks}"
    assert all(s == 1024 for s in spreads), f"scroll spreads not sustained: {spreads}"
    for p, s in zip(peaks, spreads):
        assert delta.classify(p, s, idle_peak=8, layout_peak=40) == delta.LAYOUT


@requires_decoder
def test_vector_static_caret_stays_idle(tmp_path):
    """A screen whose only motion (a 1 Hz caret) aliases against the 2.0 s sample period, so
    consecutive analysis frames are effectively unchanged -> IDLE (peak <= IDLE_PEAK,
    measured 2). Exercises the IDLE classification on a genuinely-unchanged sample pair."""
    maps, times, _ = _analyze(fx.caret_clip(), tmp_path)
    for m in maps:
        cell = delta.summarize_map(m)
        assert cell.peak <= 8, f"static+caret must stay IDLE, got peak {cell.peak}"
        assert delta.classify(cell.peak, cell.spread, idle_peak=8, layout_peak=40) == delta.IDLE


@requires_decoder
@requires_font
def test_vector_typing_is_text_class_and_above_floor(tmp_path):
    """~40 wpm typing: the binarize-then-max metric recovers it as TEXT (8 < peak <= 40,
    spread <= 32) — clearly above the floor of 2. Measured peak 18–32, spread 1–2."""
    maps, times, _ = _analyze(fx.typing_clip(), tmp_path)
    peaks, spreads = _peaks(maps), _spreads(maps)
    assert maps
    for p, s in zip(peaks, spreads):
        assert 8 < p <= 40, f"typing peak {p} not in the TEXT band (8,40]"
        assert s <= 32
        assert delta.classify(p, s, idle_peak=8, layout_peak=40) == delta.TEXT
    assert max(peaks) >= 12, f"typing must be clearly above the floor of 2, peaks={peaks}"


@requires_decoder
@requires_font
def test_vector_fast_typing_is_wider_than_slow_typing(tmp_path):
    """Fast typing across several lines is a LAYOUT-scale change with a WIDER spread than
    slow typing (measured spread 11–15 vs 1–2) — the spread axis separates the two."""
    fast, _, _ = _analyze(fx.fasttype_clip(), tmp_path)
    slow, _, _ = _analyze(fx.typing_clip(), tmp_path)
    fast_spread, slow_spread = max(_spreads(fast)), max(_spreads(slow))
    assert fast_spread > slow_spread, f"fast spread {fast_spread} !> slow spread {slow_spread}"
    # LAYOUT: at least one delta beats the layout peak or the spread threshold.
    assert any(delta.classify(p, s, idle_peak=8, layout_peak=40) == delta.LAYOUT
               for p, s in zip(_peaks(fast), _spreads(fast)))


@requires_decoder
@requires_font
def test_mechanism_whole_frame_mean_is_blind_to_typing(tmp_path):
    """The D-04 mechanism claim, made executable: a WHOLE-FRAME mean abs-difference is
    ~blind to typing (the changed text is ~0.03 % of frame area, divided away), while the
    binarize-then-max metric recovers it well above the floor. This is why Pass A binarizes
    BEFORE the area downscale and takes the MAX over cells, not a frame mean."""
    src = str(tmp_path / "typing.bin")
    Path(src).write_bytes(fx.typing_clip())
    # Whole-frame mean abs-diff (NO binarize, scale to 1x1): the "blind mean" baseline.
    vf = ("select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,2.0)',format=gray,"
          "tblend=all_mode=difference,scale=1:1:flags=area")
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", src, "-vf", vf, "-an", "-fps_mode", "passthrough",
         "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"], capture_output=True)
    whole_frame_means = list(p.stdout)
    assert whole_frame_means, "expected some deltas"
    assert max(whole_frame_means) <= 1, f"mean should be ~blind (<=1), got {whole_frame_means}"
    # The binarize-then-max metric on the SAME clip is clearly non-zero.
    maps, _, _ = _analyze(fx.typing_clip(), tmp_path)
    assert max(_peaks(maps)) >= 12


# ============================== CLASSIFICATION (pure) ==================================

@pytest.mark.parametrize("peak,spread,expected", [
    (0, 0, delta.IDLE),
    (2, 0, delta.IDLE),         # the floor
    (8, 0, delta.IDLE),         # boundary: <= idle_peak
    (9, 0, delta.TEXT),         # just over idle
    (40, 5, delta.TEXT),        # boundary: peak not > layout_peak
    (41, 0, delta.LAYOUT),      # over layout peak
    (20, 33, delta.LAYOUT),     # wide spread -> layout even at a modest peak
    (20, 32, delta.TEXT),       # boundary: spread not > 32
])
def test_classify_boundaries(peak, spread, expected):
    assert delta.classify(peak, spread, idle_peak=8, layout_peak=40) == expected


# ============================ THE FLOOR-GRID CONVENTION ================================

def test_floor_time_exact_multiple_start_has_no_floor_in_a_short_chunk():
    """Pinned convention (caveat #8): a chunk whose t_start lands EXACTLY on a FLOOR_S
    boundary reads at the NEXT boundary (+FLOOR_S), so a short chunk has no floor point —
    strictly-after removes the t_prev ambiguity (the boundary is counted once, by the chunk
    that straddles it)."""
    floor_s = 120.0
    t_start = 10_000 * floor_s   # exactly on a boundary
    assert delta.floor_time([2.0, 4.0, 6.0, 8.0], t_start, floor_s) is None


def test_floor_time_straddle_returns_first_grid_point_after_boundary():
    floor_s = 120.0
    boundary = 10_000 * floor_s
    t_start = boundary - 5.0      # the boundary falls 5 s into the chunk
    assert delta.floor_time([2.0, 4.0, 6.0, 8.0], t_start, floor_s) == 6.0  # first >= 5.0


def test_floor_time_none_when_boundary_beyond_chunk():
    floor_s = 120.0
    boundary = 10_000 * floor_s
    t_start = boundary - 200.0    # next boundary is 200 s away, past a 10 s chunk
    assert delta.floor_time([2.0, 4.0, 6.0, 8.0], t_start, floor_s) is None


# =============================== THE RANK-FREE CAP =====================================

def test_even_spaced_subset_is_rank_free():
    """D-07: the cap keeps evenly-spaced items over the TIME-SORTED survivors, never a
    magnitude rank — so two ffmpeg builds ordering a tight cluster differently cannot emit
    different frames. The choice depends only on position."""
    assert delta.even_spaced_subset([2.0, 4.0, 6.0, 8.0], 3) == [2.0, 6.0, 8.0]
    assert delta.even_spaced_subset([2.0, 4.0], 3) == [2.0, 4.0]     # fewer than cap
    assert delta.even_spaced_subset([1, 2, 3, 4, 5], 1) == [1]
    assert delta.even_spaced_subset([1, 2, 3], 0) == []


# =============================== THE OCR SELECTOR ======================================

def _uniform_maps(cell_value: int, n: int, hot_cells: int = 1):
    """n synthetic change maps, each with `hot_cells` cells at `cell_value`, rest 0."""
    out = []
    for _ in range(n):
        cells = [0] * delta.MAP_CELLS
        for i in range(hot_cells):
            cells[i] = cell_value
        out.append(tuple(cells))
    return out


def test_selector_idle_flat_chunk_selects_nothing():
    """All-floor maps (every cell <= the anchor guard) never accumulate -> no change event,
    no floor point in range -> idle, empty selection."""
    times = [2.0, 4.0, 6.0, 8.0]
    maps = _uniform_maps(2, 4)   # peak 2 everywhere == the floor
    d, ocr_times, idle = delta.select_ocr_frames(
        times, maps, t_start_epoch=0.0, idle_peak=8, layout_peak=40, max_events=3, floor_s=120.0)
    assert idle is True
    assert ocr_times == ()
    assert all(a == 0 for a in d.accum), f"floor must not accumulate, got {d.accum}"


def test_selector_anchor_accumulates_sub_threshold_change():
    """The anchor property (D-04): a per-delta change BELOW IDLE_PEAK (peak 6 <= 8) never
    fires on its own, but accumulated since the last read it crosses and fires — then the
    accumulator resets. So slow drift is caught without any single delta crossing."""
    times = [2.0, 4.0, 6.0, 8.0]
    maps = _uniform_maps(6, 4)   # each single delta peak 6 -> IDLE alone
    d, ocr_times, idle = delta.select_ocr_frames(
        times, maps, t_start_epoch=0.0, idle_peak=8, layout_peak=40, max_events=3, floor_s=1e9)
    assert not idle
    # delta0 accum 6 (idle), delta1 accum 12 -> fires+reset, delta2 accum 6, delta3 accum 12 -> fires
    assert ocr_times == (4.0, 8.0), f"expected fires at 4 and 8, got {ocr_times}"


def test_selector_cap_is_applied_rank_free():
    """More change events than VIDEO_OCR_MAX_EVENTS -> even-spaced subset over sorted times."""
    times = [2.0, 4.0, 6.0, 8.0]
    maps = _uniform_maps(50, 4)  # every delta a change event
    d, ocr_times, idle = delta.select_ocr_frames(
        times, maps, t_start_epoch=0.0, idle_peak=8, layout_peak=40, max_events=3, floor_s=1e9)
    assert not idle
    assert ocr_times == (2.0, 6.0, 8.0)   # 4 events capped to 3, evenly spaced


def test_selector_floor_read_on_idle_straddle():
    """An idle chunk that straddles a FLOOR_S boundary still gets exactly one floor read
    (so a static screen is re-OCR'd once per FLOOR_S) while remaining idle=True."""
    floor_s = 120.0
    boundary = 10_000 * floor_s
    t_start = boundary - 5.0            # boundary 5 s in -> floor point at grid time 6.0
    times = [2.0, 4.0, 6.0, 8.0]
    maps = _uniform_maps(2, 4)          # floor everywhere -> no change events
    d, ocr_times, idle = delta.select_ocr_frames(
        times, maps, t_start_epoch=t_start, idle_peak=8, layout_peak=40,
        max_events=3, floor_s=floor_s)
    assert idle is True                 # no visual change...
    assert ocr_times == (6.0,)          # ...but the floor read still fires


@requires_decoder
def test_delta_shapes_and_determinism(tmp_path):
    """The Delta dataclass is well-formed (parallel D-length tuples) and byte-identical
    across two Pass-A runs of the same blob (fleet determinism)."""
    blob = fx.appswitch_clip()
    maps, times, _ = _analyze(blob, tmp_path)
    d, ocr, idle = delta.select_ocr_frames(
        times, maps, t_start_epoch=1_700_000_000.0, idle_peak=8, layout_peak=40,
        max_events=3, floor_s=120.0)
    assert isinstance(d, Delta)
    assert len(d.times) == len(d.cells) == len(d.accum) == len(maps)
    assert all(isinstance(c, DeltaCell) for c in d.cells)
    # rerun: identical
    maps2, times2, _ = _analyze(blob, tmp_path)
    d2, ocr2, idle2 = delta.select_ocr_frames(
        times2, maps2, t_start_epoch=1_700_000_000.0, idle_peak=8, layout_peak=40,
        max_events=3, floor_s=120.0)
    assert (d.times, d.cells, d.accum) == (d2.times, d2.cells, d2.accum)
    assert ocr == ocr2 and idle == idle2


@requires_decoder
def test_long_idle_chunk_does_not_false_fire(tmp_path):
    """A 60 s flat-black chunk (29 deltas) must stay idle: the anchor guard stops the floor
    (peak 2) from accumulating into a false change event (raw accumulation would reach 58)."""
    maps, times, _ = _analyze(fx.flat_clip("black", dur=60), tmp_path)
    assert len(maps) >= 20
    d, ocr_times, idle = delta.select_ocr_frames(
        times, maps, t_start_epoch=1_700_000_000.0, idle_peak=8, layout_peak=40,
        max_events=3, floor_s=120.0)
    assert idle is True, "a long flat chunk must not manufacture a change event"
    assert all(a == 0 for a in d.accum), f"floor must never accumulate, got max {max(d.accum)}"


# ============================= parse_change_maps guard =================================

def test_parse_change_maps_rejects_truncated_stream():
    """A rawvideo length that is not a whole multiple of 1024 means a truncated decode —
    raise rather than fabricate a partial map (which would make the record set depend on a
    decode artefact)."""
    with pytest.raises(ValueError):
        delta.parse_change_maps(b"\x00" * (delta.MAP_CELLS + 5))


def test_parse_change_maps_splits_cleanly():
    raw = bytes(range(256)) * (delta.MAP_CELLS * 2 // 256)
    maps = delta.parse_change_maps(raw)
    assert len(maps) == 2
    assert all(len(m) == delta.MAP_CELLS for m in maps)
