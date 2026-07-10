"""The audio ChunkSource: a WAV file / synthetic tone carved into standalone WAV chunks.

This is the ONE concrete source built in M0. It wraps the existing audio-codec machinery
in ``app/wav.py`` (decode + carve into standalone WAVs) behind the modality-agnostic
``ChunkSource`` seam, and stamps each chunk's C1 wall-clock span from a session base time.

There is no real mic on this box (CHARTER M1+), so the "continuous stream" is either a
caller-supplied ``.wav`` path or a short synthetic tone; carving models the always-on
capture behaviour. A real mic capturer is a later milestone that becomes its own
ChunkSource file, plugging into the unchanged emit path.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterator

from .. import timeutil, wav
from ..config import Settings
from .base import SourceChunk


class WavFileSource:
    """ChunkSource for modality='audio'. Carves decoded PCM into standalone-WAV chunks.

    Constructed with decoded audio (``wav.WavAudio``), a chunk duration, the frame-0
    device wall-clock (``base``), and the codec token. ``chunks()`` yields dense,
    contiguous, wall-clock-stamped chunks — chunk i's ``t_end`` equals chunk i+1's
    ``t_start`` — via ``wav.carve``; the final chunk may be shorter.
    """

    modality = "audio"

    def __init__(
        self,
        audio: wav.WavAudio,
        *,
        chunk_seconds: float,
        base: datetime,
        codec: str,
    ) -> None:
        self._audio = audio
        self._chunk_seconds = chunk_seconds
        self._base = base
        self.codec = codec

    def chunks(self) -> Iterator[SourceChunk]:
        for chunk in wav.carve(self._audio, self._chunk_seconds):
            yield SourceChunk(
                data=chunk.data,
                t_start=timeutil.offset(self._base, chunk.t_start_seconds),
                t_end=timeutil.offset(self._base, chunk.t_end_seconds),
            )


def build(
    settings: Settings,
    *,
    source: str | None = None,
    chunk_seconds: float | None = None,
    sample_seconds: float | None = None,
    base_wallclock: str | None = None,
) -> WavFileSource:
    """Builder registered for modality 'audio' (see ``app/sources/__init__.py``).

    Resolves the continuous source (caller ``.wav`` path, else a synthetic sample),
    applies chunk/duration defaults from ``settings``, and pins the session base
    wall-clock (RFC3339, or now-UTC when omitted) for deterministic time stamping.
    """
    chunk_seconds = chunk_seconds if chunk_seconds is not None else settings.chunk_seconds
    base = timeutil.parse_wallclock(base_wallclock)
    if source:
        audio = wav.read_wav(Path(source).read_bytes())
    else:
        seconds = sample_seconds if sample_seconds is not None else settings.sample_seconds
        audio = wav.read_wav(
            wav.generate_sample_wav(seconds=seconds, sample_rate=settings.sample_rate)
        )
    return WavFileSource(
        audio, chunk_seconds=chunk_seconds, base=base, codec=settings.codec
    )
