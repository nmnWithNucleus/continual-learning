"""Keyframe extraction — raw video blob -> a few timestamped still JPEGs.

Backend-independent (both the mock and vlm captioners consume the same keyframes).
The **ffmpeg system binary** is the SINGLE canonical decoder: scene-change
detection picks the keyframes, ffmpeg extracts + downscales each to a JPEG, and we
record each frame's CHUNK-RELATIVE sub-span. It needs NO extra Python package.

ONE decoder on purpose (fleet idempotency): record_id folds in the keyframe's
selected-times index, so the *selected set* must be a pure function of (bytes,
settings). ffmpeg's scene metric is deterministic, so it is — everywhere ffmpeg
runs. A second in-process decoder (OpenCV) was intentionally NOT kept: its scene
metric differs from ffmpeg's, so a heterogeneous fleet (some workers ffmpeg, some
not) would select *different* keyframes for identical bytes under the SAME
pipeline_version — a silent non-idempotent /context upsert. (The audio path decodes
via PyAV/libav, not this CLI, so "audio needs ffmpeg" does NOT make the binary
universal — hence no in-process fallback that could diverge.)

Robust by construction: if ffmpeg is absent, the blob won't decode (e.g. the seam's
tiny non-video fixture), or no frames come back, ``extract_keyframes`` returns
``[]`` and the Processor falls back to synthetic keyframes carrying the chunk span
verbatim — the SAME consistent result on every worker. So a box with no ffmpeg
still runs the loop headless; it just can't time or picture the keyframes.
"""
from __future__ import annotations

import logging
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import VisionSettings
from .result import Keyframe

logger = logging.getLogger("data-processing.vision.frames")

# One ffmpeg/ffprobe invocation must not hang the threadpool worker forever.
_FFMPEG_TIMEOUT_S = 60

_PTS_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


def _tool(name: str) -> str | None:
    """Absolute path to an ffmpeg-family binary, or None if not installed."""
    return shutil.which(name)


def ffmpeg_available() -> bool:
    return _tool("ffmpeg") is not None and _tool("ffprobe") is not None


def _probe_duration(path: str) -> float | None:
    """Container duration in seconds via ffprobe, or None if unknown."""
    ffprobe = _tool("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT_S,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    try:
        dur = float(out.stdout.strip())
        return dur if dur > 0 else None
    except (TypeError, ValueError):
        return None


def _scene_change_times(path: str, vs: VisionSettings) -> list[float]:
    """Timestamps (s) of frames whose scene-change score beats the threshold.

    Deterministic: ffmpeg samples the stream at ``sample_fps`` and reports the
    scene metric via ``metadata=print``; we keep the ones over ``scene_threshold``.
    """
    ffmpeg = _tool("ffmpeg")
    if not ffmpeg:
        return []
    fps = vs.sample_fps if vs.sample_fps and vs.sample_fps > 0 else 1.0
    thr = min(max(vs.scene_threshold, 0.0), 1.0)
    vf = f"fps={fps},select='gt(scene,{thr})',metadata=print:file=-"
    try:
        out = subprocess.run(
            [ffmpeg, "-v", "error", "-i", path, "-vf", vf, "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT_S,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        # Transient/environmental (timeout, spawn failure) — not undecodable bytes.
        # Logged loudly so a degraded box is diagnosable; the vlm path refuses to
        # emit under this condition (processor-level), mock proceeds synthetic.
        logger.warning("scene detection failed (%s: %s) — treating as no cuts",
                       type(exc).__name__, exc)
        return []
    times: list[float] = []
    for m in _PTS_RE.finditer(out.stdout):
        try:
            times.append(float(m.group(1)))
        except ValueError:
            continue
    return times


def _uniform_times(duration: float, vs: VisionSettings) -> list[float]:
    """The deterministic, content-independent base grid: ``n`` evenly-spaced points
    in [0, duration), with ``n`` driven by the target cadence and clamped to
    [min_keyframes, max_keyframes]. Always includes t=0 (the opening keyframe)."""
    cap = max(1, vs.max_keyframes)
    interval = vs.keyframe_interval_s if vs.keyframe_interval_s and vs.keyframe_interval_s > 0 else duration
    n = math.ceil(duration / interval) if interval > 0 else 1
    n = max(max(1, vs.min_keyframes), min(n, cap))
    if n <= 1:
        return [0.0]
    return [round(k * duration / n, 3) for k in range(n)]


def _select_times(duration: float, scene_times: list[float], vs: VisionSettings) -> list[float]:
    """Final ordered keyframe timestamps: the uniform base grid UNION scene-change
    cuts, deduped (to ms), sorted, capped at ``max_keyframes`` (evenly-spaced subset
    so we keep spanning the chunk rather than clustering at cuts)."""
    cap = max(1, vs.max_keyframes)
    times = set(_uniform_times(duration, vs))
    for t in scene_times:
        if 0.0 <= t < duration:
            times.add(round(t, 3))

    uniq = sorted(t for t in times if 0.0 <= t < duration)
    if not uniq:
        uniq = [0.0]
    if len(uniq) > cap:
        idxs = [round(i * (len(uniq) - 1) / (cap - 1)) for i in range(cap)] if cap > 1 else [0]
        uniq = [uniq[i] for i in sorted(set(idxs))]
    return uniq


def _extract_jpeg(path: str, t: float, vs: VisionSettings) -> bytes | None:
    """One downscaled JPEG at timestamp ``t`` (accurate output-seek), or None."""
    ffmpeg = _tool("ffmpeg")
    if not ffmpeg:
        return None
    maxw = max(16, vs.frame_max_width)
    # Downscale only if wider than maxw; keep aspect, force even height.
    vf = f"scale='min(iw,{maxw})':-2"
    try:
        out = subprocess.run(
            [ffmpeg, "-v", "error", "-i", path, "-ss", f"{t:.3f}",
             "-frames:v", "1", "-vf", vf, "-q:v", "3", "-f", "image2pipe",
             "-vcodec", "mjpeg", "pipe:1"],
            capture_output=True, timeout=_FFMPEG_TIMEOUT_S,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("keyframe extraction at t=%.3fs failed (%s: %s)",
                       t, type(exc).__name__, exc)
        return None
    data = out.stdout
    return data if data else None


def _window_duration(decoded: float | None, span_seconds: float) -> float | None:
    """Clamp keyframe timing to the chunk's declared C1 span; fall back to the
    decoded container duration only when C1 gave no positive span."""
    if decoded is None:
        return span_seconds if (span_seconds and span_seconds > 0) else None
    if span_seconds and span_seconds > 0:
        return min(decoded, span_seconds)
    return decoded


def _keyframes_from_times(times: list[float], duration: float, extract) -> list[Keyframe]:
    """Turn ordered timestamps into contiguous-sub-span keyframes; ``extract(t)``
    returns the JPEG bytes for time ``t`` (or None to drop it).

    Each kept keyframe's sub-span runs to the NEXT KEPT keyframe's start (the last
    to ``duration``), so an interior dropped extraction folds its slice into the
    previous keyframe. A dropped HEAD frame leaves the first kept keyframe starting
    past 0 at THIS level — the processor's span mapping pins the first record to the
    chunk start (and the last to the chunk end), so the emitted records always
    partition the declared C1 span with no gap.

    ``Keyframe.index`` is the keyframe's position in the DETERMINISTIC ``times``
    list (its stable identity), NOT the post-drop survivor position. The
    discriminator (and thus record_id) is derived from that index, so a transient
    per-frame extraction failure does NOT renumber the surviving keyframes: their
    record_ids stay stable across reprocessings and a recovered frame reappears
    under its own index (an idempotent upsert of one added record, never a renumber
    of the others). Indices may therefore be non-contiguous after a drop — that is
    fine: they only need to be stable + distinct."""
    kept: list[tuple[int, float, bytes]] = []
    for orig_index, t in enumerate(times):
        jpeg = extract(t)
        if jpeg is not None:
            kept.append((orig_index, min(max(t, 0.0), duration), jpeg))
    keyframes: list[Keyframe] = []
    for j, (orig_index, start, jpeg) in enumerate(kept):
        end = kept[j + 1][1] if j + 1 < len(kept) else duration
        end = min(max(end, start), duration)
        keyframes.append(
            Keyframe(index=orig_index, t_offset_s=start,
                     t_end_offset_s=end, image_jpeg=jpeg)
        )
    return keyframes


def extract_keyframes(
    blob: bytes, codec: str, span_seconds: float, vs: VisionSettings
) -> list[Keyframe]:
    """Select + extract timestamped keyframes from a raw video blob via ffmpeg.

    Returns ``[]`` if ffmpeg is not on PATH, the bytes don't decode, or nothing came
    out — the SAME consistent result on every worker (one canonical decoder, no
    diverging in-process fallback). What ``[]`` MEANS is the caller's call: the mock
    backend falls back to synthetic keyframes (dev/headless), the vlm backend
    REFUSES to emit placeholders under its real dialect and lets at-least-once
    redelivery retry the chunk. Each
    returned keyframe's ``[t_offset_s, t_end_offset_s)`` sub-span is clamped into
    ``[0, span_seconds]`` (the chunk's declared C1 wall-clock window, the axis
    storage indexes on).
    """
    if not blob or not ffmpeg_available():
        return []

    with tempfile.TemporaryDirectory(prefix="vidproc-") as tmp:
        src = str(Path(tmp) / "chunk.bin")
        Path(src).write_bytes(blob)

        duration = _window_duration(_probe_duration(src), span_seconds)
        if not duration or duration <= 0:
            keyframes: list[Keyframe] = []
        else:
            times = _select_times(duration, _scene_change_times(src, vs), vs)
            keyframes = _keyframes_from_times(
                times, duration, lambda t: _extract_jpeg(src, t, vs)
            )

    if not keyframes:
        logger.info("extract_keyframes: decoded 0 frames (codec=%s) -> synthetic fallback", codec)
    return keyframes
