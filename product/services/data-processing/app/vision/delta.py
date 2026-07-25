"""The delta gate: change-map math + the OCR-frame selector (D-04 / D-07 selection half).

PURE and I/O-FREE. Takes the raw output of clip.py's ffmpeg Pass A (a run of 32x32
binarized change maps + their true PTS) and turns it into:

  * a ``Delta`` — per-analysis-time ``(peak, spread)`` summaries plus the anchor
    accumulator trace (``app/vision/clip_types.py`` shapes, imported, never redefined);
  * the ``ocr_times`` the delta gate selected (D-07: floor grid ∪ change events, a
    rank-free even-spaced cap, chunk-local);
  * an ``idle`` flag (no change event fired the whole chunk → the caption uses the
    idle prompt, per §7.2's "active / idle" split).

WHY binarize-at-full-resolution then area-average then MAX (D-04, defect #26): a
whole-frame *mean* absolute difference is blind to typing at every resolution — one
growing line of 26 px text on a 3024-wide canvas is ~0.03 % of frame area and a mean
divides it away. ffmpeg binarizes each pixel (``lutyuv gt(val,24)``) BEFORE the
``scale=32:32:area`` downscale, so a cell that contains changed text averages to a
real value; taking the MAX over the 1024 cells recovers the signal. The Python side
here only reduces the already-binarized 32x32 map to ``(peak, spread)`` and runs the
anchor accumulator — it adds no second decode and no second change metric.

THE FLOOR IS EXACTLY 2 (D-04): on flat black / white / 50 %-gray the change map's max
cell is a content-independent ``2/255`` — a deterministic artefact of the area
downscale (verified: identical under lossless ffv1, so it is the scaler, not the
codec). ``spread`` (cells ≥ ``CELL_THRESHOLD``) is ``0`` there. This is asserted in CI.

DETERMINISM (§4 R4 / §3 D-05): every function here is a pure function of its inputs, so
identical bytes+settings yield a byte-identical ``Delta`` and an identical ``ocr_times``
on any worker. The cap is **rank-free** (even-spaced over time-sorted survivors, never a
magnitude sort) precisely so two ffmpeg builds ranking a tight cluster differently cannot
emit different frames.

Production thresholds (``VIDEO_OCR_IDLE_PEAK`` etc.) are gated on O-1 (one real capture
hour); the anchor accumulator is a documented *synthesis*, not a measured configuration
(O-1). Build to the specified defaults; they get pinned later.
"""
from __future__ import annotations

from .clip_types import Delta, DeltaCell

# --- Change-map geometry + the two fixed thresholds baked into ffmpeg Pass A ---------
MAP_SIZE = 32                     # the area-downscale target; 32*32 = 1024 cells per map
MAP_CELLS = MAP_SIZE * MAP_SIZE
PIXEL_BINARIZE_THRESHOLD = 24     # ffmpeg lutyuv gt(val,24): a per-pixel change is 0/255
CELL_THRESHOLD = 13               # a downscaled cell counts toward `spread` at >= this
                                  # (the D-04 table's "spread = cells >= 13")

# --- OCR event classification (D-07). class = IDLE | LAYOUT | TEXT ---------------------
IDLE = "idle"
LAYOUT = "layout"
TEXT = "text"
SPREAD_LAYOUT_THRESHOLD = 32      # a change is LAYOUT-scale once `spread` exceeds this

# The anchor accumulator only sums per-cell change that is ABOVE the flat-screen floor,
# so a static chunk's floor (peak 2, verified content-independent) never accumulates into
# a false change event over a long idle chunk (measured: raw accumulation of the floor
# reaches 58/255 over 29 deltas of flat black — it WOULD cross IDLE_PEAK). Tied to the
# floor; O-1 may retune it against real capture. A cell at or below the guard contributes
# nothing; above it, the cell's full value accumulates since the last OCR read (P3's
# anchor property + P1's sub-single-delta sensitivity in one pass).
ANCHOR_FLOOR_GUARD = 2


def parse_change_maps(raw: bytes) -> list[tuple[int, ...]]:
    """Split ffmpeg Pass A's raw ``gray`` stdout into per-delta 32x32 maps.

    stdout is ``D * 1024`` bytes (one binarized+area-averaged change map per delta). A
    length that is not a whole multiple of 1024 means a truncated/garbled decode — raise
    rather than silently drop a partial map (that would make the record set depend on a
    decode artefact).
    """
    if len(raw) % MAP_CELLS != 0:
        raise ValueError(
            f"Pass-A rawvideo is {len(raw)} bytes, not a multiple of {MAP_CELLS} "
            "(one 32x32 gray change map per delta) — truncated/garbled decode"
        )
    n = len(raw) // MAP_CELLS
    return [tuple(raw[i * MAP_CELLS:(i + 1) * MAP_CELLS]) for i in range(n)]


def summarize_map(cells: tuple[int, ...]) -> DeltaCell:
    """One 32x32 change map -> ``(peak, spread)``: the max cell (0..255) and the count of
    cells at/over ``CELL_THRESHOLD`` (0..1024). These are the D-04 calibration vectors."""
    peak = max(cells) if cells else 0
    spread = sum(1 for v in cells if v >= CELL_THRESHOLD)
    return DeltaCell(peak=peak, spread=spread)


def classify(peak: int, spread: int, *, idle_peak: int, layout_peak: int) -> str:
    """D-07 event class from an (accumulated) map's peak/spread. IDLE first: a peak at or
    below ``idle_peak`` implies no cell reached ``CELL_THRESHOLD`` (spread 0), so it is a
    genuine no-change. Otherwise LAYOUT (a big or wide change) else TEXT."""
    if peak <= idle_peak:
        return IDLE
    if peak > layout_peak or spread > SPREAD_LAYOUT_THRESHOLD:
        return LAYOUT
    return TEXT


def even_spaced_subset(items: list, cap: int) -> list:
    """Keep ``cap`` items evenly spaced across a TIME-SORTED list (the frames.py:133-135
    idiom). Rank-free by construction — the choice depends only on position, never on a
    magnitude that two decoder builds could order differently (D-07)."""
    if cap <= 0:
        return []
    if len(items) <= cap:
        return list(items)
    if cap == 1:
        idxs = [0]
    else:
        idxs = [round(i * (len(items) - 1) / (cap - 1)) for i in range(cap)]
    return [items[i] for i in sorted(set(idxs))]


def floor_time(times: list[float], t_start_epoch: float, floor_s: float) -> float | None:
    """D-07 floor grid, pinned convention (no ``t_prev`` ambiguity): the FIRST analysis
    grid point whose ABSOLUTE epoch second is >= the next multiple of ``floor_s`` STRICTLY
    AFTER ``epoch(c1.t_start)``; ``None`` if no such point falls in this chunk.

    Strictly-after is what removes the ambiguity: a chunk whose ``t_start`` lands exactly
    on a ``floor_s`` boundary reads at ``+floor_s`` (the NEXT boundary), not at its own
    ``t=0`` — so the boundary is counted once, by the chunk that straddles it, never twice.
    This is why the caveat-#8 test pins ``t_start`` to an exact multiple of ``floor_s``.
    """
    if floor_s <= 0 or not times:
        return None
    # next multiple of floor_s strictly greater than the chunk-start epoch
    next_boundary = (int(t_start_epoch // floor_s) + 1) * floor_s
    offset = next_boundary - t_start_epoch  # seconds into the chunk
    for t in times:
        if t >= offset:
            return t
    return None


def select_ocr_frames(
    times: list[float],
    maps: list[tuple[int, ...]],
    *,
    t_start_epoch: float,
    idle_peak: int,
    layout_peak: int,
    max_events: int,
    floor_s: float,
) -> tuple[Delta, tuple[float, ...], bool]:
    """The delta gate. Returns ``(Delta, ocr_times, idle)``.

    A single forward pass over the deltas maintains the anchor accumulator (per-cell
    change accumulated *since the last OCR read*, above the floor guard). At each delta
    the accumulated map is classified; a non-IDLE class is a **change event** (and an OCR
    read point, so the accumulator resets); the floor point is also an OCR read (resets).
    ``ocr_times = sorted(floor ∪ change events)`` capped rank-free at ``max_events``.
    ``idle`` is true iff no change event fired all chunk (the floor read may still fire).

    ``Delta.cells`` are the SINGLE-delta ``(peak, spread)`` (the calibration vectors);
    ``Delta.accum`` is the per-delta accumulated peak (the anchor trace, for
    ``dp_video_delta_peak`` + ``calibrate_delta``). Everything is a pure function of the
    inputs, so two runs are byte-identical.
    """
    if len(times) != len(maps):
        raise ValueError(
            f"delta count mismatch: {len(times)} PTS vs {len(maps)} change maps"
        )

    cells = tuple(summarize_map(m) for m in maps)
    ft = floor_time(times, t_start_epoch, floor_s)

    accum = [0] * MAP_CELLS
    accum_peaks: list[int] = []
    change_times: list[float] = []

    for t, m in zip(times, maps):
        for i, v in enumerate(m):
            if v > ANCHOR_FLOOR_GUARD:          # above the floor artefact -> real signal
                s = accum[i] + v
                accum[i] = 255 if s > 255 else s
        apeak = max(accum) if accum else 0
        aspread = sum(1 for v in accum if v >= CELL_THRESHOLD)
        accum_peaks.append(apeak)

        fired = classify(apeak, aspread, idle_peak=idle_peak, layout_peak=layout_peak) != IDLE
        if fired:
            change_times.append(t)
        is_floor = ft is not None and abs(t - ft) < 1e-9
        if fired or is_floor:                    # an OCR read -> reset "since last read"
            accum = [0] * MAP_CELLS

    selected = sorted(set(change_times) | ({ft} if ft is not None else set()))
    if len(selected) > max_events:
        selected = even_spaced_subset(selected, max_events)

    delta = Delta(times=tuple(times), cells=cells, accum=tuple(accum_peaks))
    return delta, tuple(selected), (len(change_times) == 0)
