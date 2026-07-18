"""Demux one spooled A/V segment into per-modality chunk files (ffprobe + ffmpeg).

Charter OQ8 pattern: the muxed device link is split HERE, before emission, so each C1
stream stays single-modality. Per segment:

  * audio -> ``audio/wav`` 16 kHz mono s16le (re-encode; faster-whisper's native shape)
  * video -> container copy, ``video/mp4`` or ``video/webm`` keyed off the segment's
    upload mime (no re-encode; mp4 gets ``+faststart`` so the moov atom leads)

A segment may carry either track alone — a ``video/*`` mime can still be audio-only
(camera toggled off mid-session) — so ffprobe's stream listing, not the mime, decides
what exists. A segment with NO streams at all raises ``DemuxError`` (permanent: the
caller marks the segment failed).

Pure-sync subprocess module: async callers run it in a thread (asyncio.to_thread).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class DemuxError(RuntimeError):
    """A segment ffprobe/ffmpeg could not read or split (permanent, not transient)."""


# A stuck ffmpeg/ffprobe (pathological upload) must not wedge a session's worker
# forever: generous vs. ~10 s segments (this is a hang detector, not a pace cap).
_SUBPROCESS_TIMEOUT_S = 120


@dataclass(frozen=True)
class DemuxedTrack:
    modality: str   # "audio" | "video"
    codec: str      # the C1 codec: "audio/wav" | "video/mp4" | "video/webm"
    path: Path


def _run(cmd: list[str], *, what: str) -> str:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_S
        )
    except FileNotFoundError as exc:
        raise DemuxError(f"{what}: binary not found: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DemuxError(f"{what}: timed out after {_SUBPROCESS_TIMEOUT_S}s") from exc
    if proc.returncode != 0:
        lines = (proc.stderr or proc.stdout or "").strip().splitlines()
        detail = "; ".join(lines[-3:]) if lines else f"exit code {proc.returncode}"
        raise DemuxError(f"{what} failed: {detail}")
    return proc.stdout


def probe_track_types(src: Path, *, ffprobe_bin: str = "ffprobe") -> set[str]:
    """The set of track kinds present in ``src``: subset of {'audio', 'video'}."""
    out = _run(
        [ffprobe_bin, "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(src)],
        what="ffprobe",
    )
    kinds = {line.strip().strip(",") for line in out.splitlines() if line.strip()}
    return kinds & {"audio", "video"}


def demux_segment(
    src: Path,
    *,
    mime: str,
    out_dir: Path,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
) -> list[DemuxedTrack]:
    """Split one segment into per-modality chunk files under ``out_dir``.

    Returns tracks audio-first (stable emit order). Raises ``DemuxError`` when the
    segment has no A/V streams or a split fails.
    """
    kinds = probe_track_types(src, ffprobe_bin=ffprobe_bin)
    if not kinds:
        raise DemuxError(f"ffprobe found no audio/video streams in segment {src.name}")

    out_dir.mkdir(parents=True, exist_ok=True)
    tracks: list[DemuxedTrack] = []

    if "audio" in kinds:
        dst = out_dir / "audio.wav"
        _run(
            [ffmpeg_bin, "-v", "error", "-y", "-i", str(src),
             "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)],
            what="ffmpeg audio demux",
        )
        tracks.append(DemuxedTrack("audio", "audio/wav", dst))

    if "video" in kinds:
        container = "mp4" if "mp4" in (mime or "").lower() else "webm"
        dst = out_dir / f"video.{container}"
        cmd = [ffmpeg_bin, "-v", "error", "-y", "-i", str(src), "-an", "-c", "copy"]
        if container == "mp4":
            cmd += ["-movflags", "+faststart"]
        cmd.append(str(dst))
        _run(cmd, what="ffmpeg video demux")
        tracks.append(DemuxedTrack("video", f"video/{container}", dst))

    return tracks
