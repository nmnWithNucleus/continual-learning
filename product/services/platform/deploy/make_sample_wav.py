#!/usr/bin/env python3
"""make_sample_wav.py — synthesize a sample WAV for the learn-loop smoke.

The M0 capture model is a CONTINUOUS, always-on audio source carved into chunks.
There is no microphone on this box, so `run_learn.sh --smoke` feeds the recording
capturer a local WAV file standing in for that continuous source. This script
generates one using ONLY the Python standard library (`wave`, `math`, `struct`)
— no numpy, no torch, no ffmpeg — so it runs on any box.

Output: 16 kHz, mono, signed 16-bit PCM (`audio/wav`) — a plain shape ASR
backends accept. The signal is a couple of quiet, slowly-warbling tones so the
file is non-trivial without being loud; content does not matter for the mock ASR
path (which emits a canned transcript regardless).

Usage:
    python3 make_sample_wav.py OUT.wav [SECONDS]

Defaults: SECONDS=12. At the default 5s chunk size that carves into 3 chunks
(sequence 0,1,2), exercising a dense multi-chunk stream end to end.
"""
from __future__ import annotations

import math
import struct
import sys
import wave

SAMPLE_RATE = 16000  # Hz — mono
AMPLITUDE = 0.15      # keep it quiet (fraction of full scale)


def synthesize(path: str, seconds: float) -> int:
    """Write `seconds` of a two-tone warble to `path`. Returns bytes written."""
    n_frames = int(SAMPLE_RATE * seconds)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16-bit
        w.setframerate(SAMPLE_RATE)
        frames = bytearray()
        for i in range(n_frames):
            t = i / SAMPLE_RATE
            # two tones plus a slow tremolo so successive chunks are not identical
            tremolo = 0.5 * (1.0 + math.sin(2 * math.pi * 0.25 * t))
            sample = AMPLITUDE * tremolo * (
                math.sin(2 * math.pi * 220.0 * t)
                + 0.5 * math.sin(2 * math.pi * 440.0 * t)
            )
            frames += struct.pack("<h", int(max(-1.0, min(1.0, sample)) * 32767))
        w.writeframes(bytes(frames))
    return n_frames * 2


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: make_sample_wav.py OUT.wav [SECONDS]", file=sys.stderr)
        return 2
    out = argv[1]
    seconds = float(argv[2]) if len(argv) > 2 else 12.0
    nbytes = synthesize(out, seconds)
    print(f"wrote {out}  ({seconds:g}s, {SAMPLE_RATE} Hz mono s16le, {nbytes} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
