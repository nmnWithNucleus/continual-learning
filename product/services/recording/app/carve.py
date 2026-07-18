"""VAD-cut variable chunking: pause-aligned cut points over a continuous PCM stream.

Recording M1 retires the fixed 5 s placeholder as the DEFAULT audio carve (charter OQ4,
decision D-M1-2): the always-on stream is cut at detected speech pauses, each chunk's
duration bounded to [min_s, max_s], so cuts avoid landing mid-word/mid-utterance —
better per-chunk ASR downstream, with no overlap needed. The detector is deliberately
crude-but-deterministic (energy only, stdlib only): windowed RMS per hop, a silence
threshold calibrated from the stream's own noise floor, pause = a long-enough run of
below-threshold hops, cut = the pause's midpoint. A real VAD model is a later
refinement behind the same ``find_cuts`` seam.

Degenerate streams — pure silence, or any homogeneous signal where NO hop clears the
adaptive threshold — contain no speech-to-pause transitions to align to, so they
degrade to hard cuts at exactly ``max_s``: the documented fallback, not an error.

``carve_at`` is the variable-length sibling of ``wav.carve``: same standalone-WAV
output contract, but boundaries come from ``find_cuts`` instead of a fixed duration.
"""
from __future__ import annotations

import math
import sys
from array import array
from bisect import bisect_left

from . import wav

# Chunk-duration bounds (seconds) pinned by D-M1-2; the builder may override from env.
DEFAULT_MIN_CHUNK_SECONDS = 5.0
DEFAULT_MAX_CHUNK_SECONDS = 30.0

# Silence threshold = max(floor, margin * p20 of hop RMS). The absolute floor (int16
# RMS units) marks a hop quiet in ANY stream however clean its noise floor; the p20
# leg adapts the threshold upward for noisy streams.
_THRESHOLD_FLOOR = 200.0
_NOISE_FLOOR_MARGIN = 2.5


def _percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile over ``values`` (deterministic, numpy-style)."""
    ordered = sorted(values)
    pos = q * (len(ordered) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _pause_midpoints(
    rms: list[float], *, hop: int, hop_ms: int, min_pause_ms: int, total: int
) -> list[int]:
    """Frame offsets of the midpoints of qualifying pauses (strictly increasing).

    A pause is a run of consecutive below-threshold hops lasting >= min_pause_ms.
    When NO hop clears the threshold there is no detected speech anywhere, hence no
    speech pauses to align to — return none so the walk degrades to max_s hard cuts.
    """
    if not rms:
        return []
    threshold = max(_THRESHOLD_FLOOR, _NOISE_FLOOR_MARGIN * _percentile(rms, 0.20))
    below = [r < threshold for r in rms]
    if all(below):
        return []

    min_run_hops = max(1, math.ceil(min_pause_ms / hop_ms))
    mids: list[int] = []
    run_start: int | None = None
    for idx, quiet in enumerate([*below, False]):     # sentinel closes a trailing run
        if quiet and run_start is None:
            run_start = idx
        elif not quiet and run_start is not None:
            if idx - run_start >= min_run_hops:
                start_f = run_start * hop
                end_f = min(idx * hop, total)
                mids.append((start_f + end_f) // 2)
            run_start = None
    return mids


def find_cuts(
    pcm: bytes,
    sample_rate: int,
    *,
    min_s: float = DEFAULT_MIN_CHUNK_SECONDS,
    max_s: float = DEFAULT_MAX_CHUNK_SECONDS,
    hop_ms: int = 30,
    min_pause_ms: int = 300,
) -> list[int]:
    """Pause-aligned cut FRAME offsets for s16le mono PCM.

    Returns strictly increasing offsets excluding 0 and the final frame. The walk:
    from the current chunk start, cut at the FIRST pause midpoint >= min_s after it;
    if no pause offers before max_s, hard-cut at exactly max_s frames; a tail
    remainder shorter than min_s just becomes the final chunk (no cut forced). Every
    resulting chunk spans [min_s, max_s] frames except possibly the final remainder.
    """
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if min_s <= 0 or max_s < min_s:
        raise ValueError(f"need 0 < min_s <= max_s, got min_s={min_s} max_s={max_s}")
    if len(pcm) % 2:
        raise ValueError("expected s16le mono PCM (even byte count)")

    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder == "big":
        samples.byteswap()                            # bytes are s16 LITTLE-endian
    total = len(samples)
    if total == 0:
        return []

    min_frames = max(1, int(round(min_s * sample_rate)))
    max_frames = max(min_frames, int(round(max_s * sample_rate)))
    hop = max(1, int(round(sample_rate * hop_ms / 1000.0)))

    # Windowed RMS, one value per full hop. A trailing partial hop is ignored: any
    # cut it could host lies in the tail, which is never force-cut anyway.
    rms = [
        math.sqrt(sum(s * s for s in samples[i : i + hop]) / hop)
        for i in range(0, total - hop + 1, hop)
    ]
    mids = _pause_midpoints(
        rms, hop=hop, hop_ms=hop_ms, min_pause_ms=min_pause_ms, total=total
    )

    cuts: list[int] = []
    start = 0
    while True:
        i = bisect_left(mids, start + min_frames)     # first pause >= min_s after start
        if i < len(mids) and mids[i] <= start + max_frames:
            start = mids[i]
        elif total - start > max_frames:
            start += max_frames                       # no pause offered: hard cut
        else:
            return cuts                               # remainder fits: final chunk
        cuts.append(start)


def carve_at(audio: wav.WavAudio, cuts: list[int]) -> list[wav.Chunk]:
    """Slice decoded audio into standalone WAV chunks at explicit frame offsets.

    The variable-length sibling of ``wav.carve`` with the same output contract —
    dense zero-based index, frame-accurate exact-adjacent spans (offset/sample_rate
    seconds), the remainder as the final chunk — but boundaries come from ``cuts``
    (as produced by ``find_cuts``) instead of a fixed duration. Reuses the one WAV
    writer (``wav._wrap_wav``) so chunk bytes are formatted exactly like the fixed
    path's.
    """
    total = audio.n_frames
    if total == 0:
        return []
    bounds = [0, *cuts, total]
    for a, b in zip(bounds, bounds[1:]):
        if not a < b:
            raise ValueError(
                f"cuts must be strictly increasing, excluding 0 and the final frame "
                f"({total}): {cuts!r}"
            )
    bpf = audio.bytes_per_frame
    sr = audio.sample_rate
    return [
        wav.Chunk(
            index=idx,
            data=wav._wrap_wav(audio, audio.frames[start_f * bpf : end_f * bpf]),
            t_start_seconds=start_f / sr,
            t_end_seconds=end_f / sr,
            n_frames=end_f - start_f,
        )
        for idx, (start_f, end_f) in enumerate(zip(bounds, bounds[1:]))
    ]
