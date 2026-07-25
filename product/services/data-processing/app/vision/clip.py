"""Frame prep for the clip video path: the two ffmpeg passes + ``ClipFrames`` assembly.

Replaces the six-subprocess ``app/vision/frames.py`` (a container-duration probe + a
cut-detection pass + N output-seek extracts) with **exactly two ffmpeg subprocesses, one
decode each** (D-04):

  * **Pass A (analysis)** — one decode, ~4 KB out: sample the stream every
    ``analysis_period_s`` via ``select`` on TRUE PTS, ``format=gray`` →
    ``tblend=difference`` → binarize per pixel (``gt(val,24)``) → ``scale=32:32:area`` →
    ``showinfo``. stdout is one 32x32 binarized change map per delta; stderr's
    ``pts_time`` + integer ``pts`` give each analysis frame's true time AND its exact
    presentation timestamp (the handle Pass B re-selects by). The change math + the
    OCR-frame selection live in ``delta.py``.
  * **Pass B (extraction)** — one decode: ``split`` the single decode to two widths and
    ``select`` the caption frames at ``frame_width`` (lo, for the VLM) and the OCR events
    at ``ocr_frame_width`` (hi, native, for the CPU OCR pass). ``-frame_pts 1`` names each
    file, the per-chain count is asserted, and outputs map to targets in time order — so a
    silently-dropped frame RAISES instead of mis-assigning pixels/timestamps (defect #24).

The span comes from the C1 envelope, NOT a container-duration probe (D-04 deleted that
whole legacy subprocess), so record identity can never be decoder-dependent; and there is
NO cut-detection pass (the legacy metric was inert on screens — measured 0 cuts on
scrolling code whose SSIM fell to 0.47).

**Frames are selected by EXACT PRESENTATION TIMESTAMP** — ``eq(pts,P)`` for each analysis
frame (P is the integer ``pts`` Pass A recovered), plus ``eq(n,0)`` for the opening frame —
never by a rate-derived index ``round(t*fps)``. That earlier CFR model is unsound on
variable-frame-rate capture (macOS ScreenCaptureKit emits frames only on change): mp4 pins
a constant timebase, so ``duration_time`` reports the timebase tick, NOT the real
inter-frame gap, and ``round(t*fps)`` lands far past the last real frame → a benign VFR
chunk would dead-letter on every backend. Selecting the frames the delta pass PROVABLY
decoded eliminates that whole class: every requested frame exists, so the count guard fires
only on a genuine decoder anomaly, and a clip shorter than its declared span degrades
gracefully (fewer frames) instead of dead-lettering. It also drops the 29.97/23.976
integer-rounding drift, because no rate is reconstructed at all.

CONFIG SHIM: the ``VIDEO_CLIP_*`` / ``VIDEO_OCR_*`` / ``VIDEO_ANALYSIS_*`` knobs are read
here via a small local ``os.getenv`` helper with the §D-03 / §D-07 / §8 defaults, each
marked ``# TEMP -> VisionSettings (WS-D owns config.py)``. WS-D folds these into
``VisionSettings`` at integration; its ``OUTPUT_AFFECTING`` completeness test enforces
coverage. This file never imports or edits ``app/vision/config.py`` beyond reading the
already-shipped base ``VisionSettings`` (backend + wire), so WS-B never blocks on WS-D.
"""
from __future__ import annotations

import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import delta as _delta
from .clip_types import ClipFrames, Delta, Frame
from .config import VisionSettings, get_vision_settings

logger = logging.getLogger("data-processing.vision.clip")

# One ffmpeg invocation must not hang a threadpool worker forever (matches frames.py).
_FFMPEG_TIMEOUT_S = 60

_PTS_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")     # each analysis frame's true time
_PTSINT_RE = re.compile(r" pts:\s*([0-9]+)")               # its exact integer presentation PTS

# Sentinel selector-key for the opening frame: the first decoded frame is unambiguously
# n=0 for any stream (CFR or VFR), so it is re-selected by frame number, not by PTS.
_OPENING = object()


class ClipDecodeError(RuntimeError):
    """The blob did not decode / is too short to analyse (ffmpeg absent, undecodable
    bytes, or < 2 analysis frames). The stage turns this into the mock synthetic fallback
    or, under a non-mock backend, a loud raise — NEVER a placeholder persisted as truth."""


class FrameCountError(RuntimeError):
    """Pass B produced fewer frames than requested — a requested frame index the stream
    does not contain (measured ``-frame_pts`` guard, defect #24). ALWAYS raised (any
    backend): a silent drop mis-assigns pixels, timestamps and discriminators. Distinct
    from ``ClipDecodeError`` so the stage never masks it with the synthetic fallback."""


# ---------------------------------------------------------------------------------------
# Config shim — TEMP, WS-D owns app/vision/config.py.
# ---------------------------------------------------------------------------------------
def _env_float(name: str, default: float) -> float:  # TEMP -> VisionSettings (WS-D owns config.py)
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number — using default %s", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:  # TEMP -> VisionSettings (WS-D owns config.py)
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer — using default %s", name, raw, default)
        return default


# The one knob with a hard clamp: 768 -> exactly 360 Qwen3-VL vision tokens/frame; 1280 is
# 2.78x that and the single easiest accidental cost blowup, so >1024 is clamped with a WARN
# (D-03 / A-16). The token arithmetic sits here so the price is read before the dial turns.
_FRAME_WIDTH_CLAMP = 1024


@dataclass(frozen=True)
class ClipSettings:
    """The clip-path knobs, read fresh per chunk (defaults: §D-03 sampling + §D-07
    selector + §8 required config). Production thresholds are gated on O-1's real-capture
    hour — build to these; they get pinned later."""

    seconds_per_frame: float   # caption grid cadence; K = clamp(ceil(span/x), min, max)
    max_frames: int            # hard prefill ceiling on caption frames (<= server mm limit)
    min_frames: int            # caption-frame floor (one frame can't answer "what changed")
    frame_width: int           # caption (lo) JPEG width; clamped at 1024 with a WARN
    ocr_frame_width: int       # OCR (hi) JPEG width; 1728 = the mac capture cap, no resample
    analysis_period_s: float   # Pass-A delta probe period (2.0 s: sensitivity beats precision)
    ocr_idle_peak: int         # class=IDLE at accumulated peak <= this (D-07)
    ocr_layout_peak: int       # class=LAYOUT above this peak (D-07)
    ocr_max_events: int        # rank-free even-spaced cap on OCR reads/chunk (3->8 is ~2.7x CPU)
    ocr_floor_s: float         # a static screen still reads once per this many wall-clock s


def get_clip_settings() -> ClipSettings:
    fw = _env_int("VIDEO_CLIP_FRAME_WIDTH", 768)  # TEMP -> VisionSettings (WS-D owns config.py)
    if fw > _FRAME_WIDTH_CLAMP:
        logger.warning(
            "VIDEO_CLIP_FRAME_WIDTH=%d clamped to %d — 1280 is 2.78x the vision tokens of "
            "768 on every call (D-03 / A-16)", fw, _FRAME_WIDTH_CLAMP)
        fw = _FRAME_WIDTH_CLAMP
    return ClipSettings(
        seconds_per_frame=_env_float("VIDEO_CLIP_SECONDS_PER_FRAME", 2.5),  # TEMP -> VisionSettings (WS-D owns config.py)
        max_frames=_env_int("VIDEO_CLIP_MAX_FRAMES", 12),                   # TEMP -> VisionSettings (WS-D owns config.py)
        min_frames=_env_int("VIDEO_CLIP_MIN_FRAMES", 2),                    # TEMP -> VisionSettings (WS-D owns config.py)
        frame_width=fw,
        ocr_frame_width=_env_int("VIDEO_OCR_FRAME_WIDTH", 1728),            # TEMP -> VisionSettings (WS-D owns config.py)
        analysis_period_s=_env_float("VIDEO_ANALYSIS_PERIOD_S", 2.0),       # TEMP -> VisionSettings (WS-D owns config.py)
        ocr_idle_peak=_env_int("VIDEO_OCR_IDLE_PEAK", 8),                   # TEMP -> VisionSettings (WS-D owns config.py)
        ocr_layout_peak=_env_int("VIDEO_OCR_LAYOUT_PEAK", 40),             # TEMP -> VisionSettings (WS-D owns config.py)
        ocr_max_events=_env_int("VIDEO_OCR_MAX_EVENTS", 3),                 # TEMP -> VisionSettings (WS-D owns config.py)
        ocr_floor_s=_env_float("VIDEO_OCR_FLOOR_S", 120.0),                 # TEMP -> VisionSettings (WS-D owns config.py)
    )


@dataclass(frozen=True)
class ClipVisionSettings:
    """The ``vision_settings`` slot ``clipprep`` provides: the already-shipped base
    ``VisionSettings`` (backend + VLM wire, from WS-D's ``config.py``, read not edited)
    with the clip knobs attached alongside. Unknown attribute reads delegate to ``base``
    THEN ``clip``, so a downstream stage can read ``.backend`` (base) OR a clip knob like
    ``.seconds_per_frame`` (clip) at the TOP LEVEL here — exactly the shape it will read
    once WS-D folds the clip knobs into ``VisionSettings`` at integration (the two field
    sets are disjoint, so there is no ambiguity). ``.base``/``.clip`` remain available for
    code that wants the halves explicitly."""

    base: VisionSettings
    clip: ClipSettings

    def __getattr__(self, name: str):
        # `base`/`clip` are real fields resolved by __getattribute__; reaching here for
        # them means a partially-constructed object (pickle/copy) — raise instead of
        # recursing. Dunders never delegate. Otherwise prefer a base field, then a clip
        # knob, so both read at the top level (forward-compatible with the folded settings).
        if name in ("base", "clip") or (name.startswith("__") and name.endswith("__")):
            raise AttributeError(name)
        try:
            return getattr(self.base, name)
        except AttributeError:
            return getattr(self.clip, name)


def build_vision_settings() -> ClipVisionSettings:
    return ClipVisionSettings(base=get_vision_settings(), clip=get_clip_settings())


def ffmpeg_available() -> bool:
    """ffmpeg only — the clip path needs no container-duration probe (the span is a C1
    field, D-04), so a box with only ffmpeg is fully capable."""
    return shutil.which("ffmpeg") is not None


# ---------------------------------------------------------------------------------------
# The deterministic caption grid (content-independent; D-03).
# ---------------------------------------------------------------------------------------
def caption_frame_count(span_seconds: float, cs: ClipSettings) -> int:
    """``K = clamp(ceil(span/seconds_per_frame), min_frames, max_frames)`` — a pure function
    of the declared C1 span (content-INDEPENDENT): 4 @10 s, 12 @60 s (the cap binds). The
    caption then takes K frames evenly spaced across the frames the delta pass actually
    decoded, so the COUNT is span-driven while the frames are guaranteed to exist."""
    interval = cs.seconds_per_frame if cs.seconds_per_frame and cs.seconds_per_frame > 0 else span_seconds
    n = math.ceil(span_seconds / interval) if interval > 0 else 1
    return max(max(1, cs.min_frames), min(n, max(1, cs.max_frames)))


def caption_grid(span_seconds: float, cs: ClipSettings) -> list[float]:
    """The K evenly-spaced grid times over ``[0, span)`` (the frames.py ``_uniform_times``
    idiom, always including ``t=0``). Retained as the deterministic COUNT reference — Pass B
    snaps this to the decoded frames rather than requesting these exact times (VFR-safe)."""
    n = caption_frame_count(span_seconds, cs)
    if n <= 1 or span_seconds <= 0:
        return [0.0]
    return [round(k * span_seconds / n, 3) for k in range(n)]


# ---------------------------------------------------------------------------------------
# The two ffmpeg passes.
# ---------------------------------------------------------------------------------------
def _pass_a(src: str, period: float) -> tuple[list[tuple[int, ...]], list[float], list[int]]:
    """Pass A: returns ``(change_maps, delta_times, delta_pts_ints)`` — one 32x32 change map
    per delta plus each delta frame's true ``pts_time`` and its exact integer ``pts`` (the
    handle Pass B re-selects by). Raises ``ClipDecodeError`` if ffmpeg is absent, the bytes
    don't decode, or the clip is too short to yield a delta."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ClipDecodeError("ffmpeg is not on PATH (the single canonical decoder)")
    vf = (
        f"select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,{period})',"
        "format=gray,"
        "tblend=all_mode=difference,"
        f"lutyuv=y='if(gt(val,{_delta.PIXEL_BINARIZE_THRESHOLD}),255,0)',"
        f"scale={_delta.MAP_SIZE}:{_delta.MAP_SIZE}:flags=area,"
        "showinfo"
    )
    try:
        out = subprocess.run(
            [ffmpeg, "-v", "info", "-i", src, "-vf", vf, "-an",
             "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
            capture_output=True, timeout=_FFMPEG_TIMEOUT_S,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise ClipDecodeError(f"Pass A failed ({type(exc).__name__}: {exc})") from exc

    stderr = out.stderr.decode("utf-8", "replace")
    times = [float(m) for m in _PTS_RE.findall(stderr)]
    pts_ints = [int(m) for m in _PTSINT_RE.findall(stderr)]
    maps = _delta.parse_change_maps(out.stdout)

    if not maps or not times:
        raise ClipDecodeError("Pass A produced no deltas (undecodable or < 2 analysis frames)")
    if not (len(maps) == len(times) == len(pts_ints)):
        raise ClipDecodeError(
            f"Pass A showinfo mismatch: maps={len(maps)} times={len(times)} "
            f"pts={len(pts_ints)} (garbled decode)"
        )
    return maps, times, pts_ints


def _pass_b(
    src: str, lo_targets: list[tuple[object, float]], hi_targets: list[tuple[object, float]],
    lo_width: int, hi_width: int, out_dir: str,
) -> tuple[dict[float, bytes], dict[float, bytes]]:
    """Pass B: from ONE decode (``split``), extract the caption frames at ``lo_width`` and
    the OCR frames at ``hi_width``. Each target is ``(key, t_offset)`` where ``key`` is
    ``_OPENING`` (select frame ``n=0``) or an integer PTS (select ``eq(pts,P)``). Outputs are
    named by ``-frame_pts`` (monotone in time) and mapped to targets in TIME order; the
    per-chain count is asserted, so a dropped frame RAISES (``FrameCountError``) rather than
    mis-assigning pixels/timestamps (defect #24). Returns ``({t_offset: lo_bytes}, {t_offset:
    hi_bytes})``."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ClipDecodeError("ffmpeg is not on PATH (the single canonical decoder)")
    if not lo_targets and not hi_targets:
        return {}, {}

    def _sel(key: object) -> str:
        return "eq(n\\,0)" if key is _OPENING else f"eq(pts\\,{int(key)})"

    # split ONLY when both chains are needed (an encoder handed zero frames errors —
    # measured rc 234), so an OCR-less idle chunk still extracts its caption frames cleanly.
    parts: list[str] = []
    cmd = [ffmpeg, "-y", "-v", "error", "-i", src]
    if lo_targets and hi_targets:
        head, lo_in, hi_in = "[0:v]split=2[a][b];", "[a]", "[b]"
    else:
        head, lo_in, hi_in = "", "[0:v]", "[0:v]"
    if lo_targets:
        sel = "+".join(_sel(k) for k, _ in lo_targets)
        parts.append(f"{lo_in}select='{sel}',scale='min(iw\\,{lo_width})':-2[lo]")
    if hi_targets:
        sel = "+".join(_sel(k) for k, _ in hi_targets)
        parts.append(f"{hi_in}select='{sel}',scale='min(iw\\,{hi_width})':-2[hi]")
    cmd += ["-filter_complex", head + ";".join(parts)]
    if lo_targets:
        cmd += ["-map", "[lo]", "-fps_mode", "passthrough", "-frame_pts", "1", "-q:v", "3",
                str(Path(out_dir) / "lo_%d.jpg")]
    if hi_targets:
        cmd += ["-map", "[hi]", "-fps_mode", "passthrough", "-frame_pts", "1", "-q:v", "2",
                str(Path(out_dir) / "hi_%d.jpg")]

    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=_FFMPEG_TIMEOUT_S)
    except (subprocess.SubprocessError, OSError) as exc:
        raise ClipDecodeError(f"Pass B failed ({type(exc).__name__}: {exc})") from exc

    return (
        _map_outputs(out_dir, "lo_", lo_targets, proc),
        _map_outputs(out_dir, "hi_", hi_targets, proc),
    )


def _map_outputs(out_dir: str, prefix: str, targets: list[tuple[object, float]],
                 proc) -> dict[float, bytes]:
    """Map the ``{prefix}*.jpg`` outputs (sorted by their frame-number filename, which is
    monotone in time) onto ``targets`` sorted by ``t_offset`` — a value-safe 1:1 zip. The
    count guard raises if any requested frame is missing."""
    if not targets:
        return {}
    files = sorted(
        (int(p.name[len(prefix):-4]), p)
        for p in Path(out_dir).glob(f"{prefix}*.jpg")
        if p.name[len(prefix):-4].isdigit()
    )
    if len(files) != len(targets):
        raise FrameCountError(
            f"Pass B {prefix.rstrip('_')}: requested {len(targets)} frames "
            f"({[round(t, 3) for _, t in sorted(targets, key=lambda x: x[1])]}) but "
            f"{len(files)} were written (rc={proc.returncode}) — a requested frame is not in "
            f"the stream; refusing to silently mis-assign pixels/timestamps: "
            f"{proc.stderr.decode('utf-8', 'replace')[:200]}"
        )
    ordered = sorted(targets, key=lambda x: x[1])  # by t_offset, same time order as filenames
    return {t_off: p.read_bytes() for (_, t_off), (_, p) in zip(ordered, files)}


# ---------------------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------------------
def prepare_clip(
    blob: bytes, span_seconds: float, t_start_epoch: float, cvs: ClipVisionSettings
) -> tuple[ClipFrames, Delta]:
    """Two ffmpeg passes -> ``(ClipFrames, Delta)``. Raises ``ClipDecodeError`` if the blob
    won't decode (the stage handles that per backend) and ``FrameCountError`` if a frame the
    delta pass decoded is missing in Pass B (a genuine anomaly — always fatal)."""
    if not blob:
        raise ClipDecodeError("empty blob")
    cs = cvs.clip
    with tempfile.TemporaryDirectory(prefix="clipprep-") as tmp:
        src = str(Path(tmp) / "chunk.bin")
        Path(src).write_bytes(blob)

        maps, times, pts_ints = _pass_a(src, cs.analysis_period_s)
        delta, ocr_times, idle = _delta.select_ocr_frames(
            times, maps,
            t_start_epoch=t_start_epoch,
            idle_peak=cs.ocr_idle_peak,
            layout_peak=cs.ocr_layout_peak,
            max_events=cs.ocr_max_events,
            floor_s=cs.ocr_floor_s,
        )
        pts_by_time = dict(zip(times, pts_ints))

        # Frames the delta pass PROVABLY decoded: the opening (n=0, t=0) + each delta frame.
        # The caption takes K of them evenly spaced (span-driven count, guaranteed to exist);
        # the OCR frames are the delta gate's selection. Everything is re-selected by exact
        # PTS, so VFR / short-media never request a non-existent frame.
        analysis: list[tuple[object, float]] = [(_OPENING, 0.0)] + [(pts_by_time[t], t) for t in times]
        lo_targets = _delta.even_spaced_subset(analysis, caption_frame_count(span_seconds, cs))
        hi_targets = [(pts_by_time[t], t) for t in ocr_times]

        lo_map, hi_map = _pass_b(src, lo_targets, hi_targets, cs.frame_width, cs.ocr_frame_width, tmp)

        frames = tuple(
            Frame(index=i, t_offset_s=round(t_off, 3),
                  jpeg_lo=lo_map.get(t_off), jpeg_hi=hi_map.get(t_off))
            for i, t_off in enumerate(sorted(set(lo_map) | set(hi_map)))
        )
        clip_frames = ClipFrames(
            frames=frames,
            ocr_times=tuple(round(t, 3) for t in ocr_times),
            idle=idle,
            span_s=float(span_seconds),
        )
        return clip_frames, delta


def synthetic_clip_frames(span_seconds: float, cs: ClipSettings) -> ClipFrames:
    """The mock/dev fallback for an undecodable blob: ``min_frames`` timing-less frames
    (both renditions ``None``, which the captioner/OCR must tolerate), no OCR reads, idle.
    Keeps the headless loop running with the SAME shape on every worker."""
    n = max(1, cs.min_frames)
    frames = tuple(
        Frame(index=i, t_offset_s=round(i * span_seconds / n, 3) if span_seconds > 0 else 0.0,
              jpeg_lo=None, jpeg_hi=None)
        for i in range(n)
    )
    return ClipFrames(frames=frames, ocr_times=(), idle=True, span_s=float(span_seconds))


def empty_delta() -> Delta:
    return Delta(times=(), cells=(), accum=())
