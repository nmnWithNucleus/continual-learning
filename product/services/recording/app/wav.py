"""Synthetic-WAV generation + carving the continuous stream into chunks.

The CAPTURE MODEL: the user->recording feed is a CONTINUOUS, always-on life stream,
not a press-to-record clip. Recording CARVES that stream into dense, sequential,
wall-clock-stamped chunks. There is no real mic on this box, so for M0 the "continuous
source" is a local WAV (a short synthetic tone we generate by default, or a caller-
supplied .wav path). Carving is the modelled always-on behaviour; the real OS/browser
mic capturer is a later milestone.

Each carved chunk is emitted as its OWN self-contained WAV (header + that slice's PCM
frames) so data-processing can ASR each chunk independently — chunk boundaries are
recording's artifact, not semantic units, so an utterance may straddle an edge (cross-
chunk stitching is a later refinement, not an M0 gate).
"""
from __future__ import annotations

import array
import io
import math
import wave
from dataclasses import dataclass


@dataclass(frozen=True)
class WavAudio:
    """Decoded PCM: the raw frame bytes plus the format needed to re-wrap a slice."""

    sample_rate: int
    channels: int
    sampwidth: int          # bytes per sample (2 == 16-bit)
    frames: bytes           # interleaved PCM frames (no WAV header)

    @property
    def bytes_per_frame(self) -> int:
        return self.channels * self.sampwidth

    @property
    def n_frames(self) -> int:
        return len(self.frames) // self.bytes_per_frame

    @property
    def duration_seconds(self) -> float:
        return self.n_frames / self.sample_rate if self.sample_rate else 0.0


@dataclass(frozen=True)
class Chunk:
    """One carved chunk: a standalone WAV blob + its dense index and wall-clock span."""

    index: int              # dense, zero-based sequence within the stream
    data: bytes             # a complete, self-contained WAV
    t_start_seconds: float  # offset from the stream's base wall-clock
    t_end_seconds: float
    n_frames: int


def generate_sample_wav(
    *,
    seconds: float,
    sample_rate: int = 16000,
    freq_hz: float = 220.0,
    amplitude: float = 0.3,
) -> bytes:
    """A short, mono, 16-bit PCM WAV tone — the default synthetic capture source."""
    n = int(round(seconds * sample_rate))
    two_pi_f = 2.0 * math.pi * freq_hz
    peak = 32767
    samples = array.array(
        "h",
        (int(amplitude * peak * math.sin(two_pi_f * (i / sample_rate))) for i in range(n)),
    )
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def read_wav(data: bytes) -> WavAudio:
    """Decode a WAV blob into PCM frames + format."""
    with wave.open(io.BytesIO(data), "rb") as w:
        return WavAudio(
            sample_rate=w.getframerate(),
            channels=w.getnchannels(),
            sampwidth=w.getsampwidth(),
            frames=w.readframes(w.getnframes()),
        )


def _wrap_wav(source: WavAudio, frame_bytes: bytes) -> bytes:
    """Re-wrap a raw PCM slice as a complete, standalone WAV using source format."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(source.channels)
        w.setsampwidth(source.sampwidth)
        w.setframerate(source.sample_rate)
        w.writeframes(frame_bytes)
    return buf.getvalue()


def carve(audio: WavAudio, chunk_seconds: float) -> list[Chunk]:
    """Carve the continuous stream into fixed-duration chunks.

    Dense, contiguous, wall-clock-stamped: chunk i's t_end == chunk i+1's t_start.
    The final chunk MAY be shorter than ``chunk_seconds``. N seconds of source at
    K-second chunks yields exactly ceil(N/K) chunks.
    """
    frames_per_chunk = max(1, int(round(chunk_seconds * audio.sample_rate)))
    total = audio.n_frames
    n_chunks = math.ceil(total / frames_per_chunk) if total > 0 else 0
    bpf = audio.bytes_per_frame
    sr = audio.sample_rate

    chunks: list[Chunk] = []
    for idx in range(n_chunks):
        start_f = idx * frames_per_chunk
        end_f = min(start_f + frames_per_chunk, total)
        slice_bytes = audio.frames[start_f * bpf : end_f * bpf]
        chunks.append(
            Chunk(
                index=idx,
                data=_wrap_wav(audio, slice_bytes),
                t_start_seconds=start_f / sr,
                t_end_seconds=end_f / sr,
                n_frames=end_f - start_f,
            )
        )
    return chunks
