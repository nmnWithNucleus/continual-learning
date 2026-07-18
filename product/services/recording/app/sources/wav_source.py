"""The audio ChunkSource: a WAV file / synthetic tone carved into standalone WAV chunks.

This is the ONE concrete source built in M0. It wraps the audio-codec machinery in
``app/wav.py`` and ``app/carve.py`` (decode + carve into standalone WAVs) behind the
modality-agnostic ``ChunkSource`` seam, and stamps each chunk's C1 wall-clock span from
a session base time.

Two carve modes (charter OQ4, decision D-M1-2). An EXPLICIT duration — the caller's
``chunk_seconds``, else the ``CHUNK_SECONDS`` env var — selects the fixed carve
(M0 behaviour, unchanged; also the right mode for video-like uses where VAD is
meaningless). With neither, the DEFAULT is the VAD carve: variable-length chunks cut
at detected speech pauses, durations bounded to [``VAD_MIN_CHUNK_SECONDS``,
``VAD_MAX_CHUNK_SECONDS``] (5/30 s). Both modes yield dense, exact-adjacent
wall-clock spans, so the C1 shape and the emit path are untouched.

There is no real mic on this box (CHARTER M1+), so the "continuous stream" is either a
caller-supplied ``.wav`` path or a short synthetic tone; carving models the always-on
capture behaviour. A real mic capturer is a later milestone that becomes its own
ChunkSource file, plugging into the unchanged emit path.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .. import carve, timeutil, wav
from ..config import Settings
from .base import SourceChunk


class WavFileSource:
    """ChunkSource for modality='audio'. Carves decoded PCM into standalone-WAV chunks.

    Constructed with decoded audio (``wav.WavAudio``), a chunk duration (``None``
    selects the VAD carve within ``vad_min_s``/``vad_max_s``; a float selects the
    fixed carve), the frame-0 device wall-clock (``base``), and the codec token.
    ``chunks()`` yields dense, contiguous, wall-clock-stamped chunks — chunk i's
    ``t_end`` equals chunk i+1's ``t_start`` — and the final chunk may be shorter.
    """

    modality = "audio"

    def __init__(
        self,
        audio: wav.WavAudio,
        *,
        chunk_seconds: float | None,
        base: datetime,
        codec: str,
        vad_min_s: float = carve.DEFAULT_MIN_CHUNK_SECONDS,
        vad_max_s: float = carve.DEFAULT_MAX_CHUNK_SECONDS,
    ) -> None:
        self._audio = audio
        self._chunk_seconds = chunk_seconds
        self._vad_min_s = vad_min_s
        self._vad_max_s = vad_max_s
        self._base = base
        self.codec = codec

    def chunks(self) -> Iterator[SourceChunk]:
        if self._chunk_seconds is not None:
            carved = wav.carve(self._audio, self._chunk_seconds)
        else:
            carved = self._vad_carve()
        for chunk in carved:
            yield SourceChunk(
                data=chunk.data,
                t_start=timeutil.offset(self._base, chunk.t_start_seconds),
                t_end=timeutil.offset(self._base, chunk.t_end_seconds),
            )

    def _vad_carve(self) -> list[wav.Chunk]:
        audio = self._audio
        # find_cuts reads raw s16le mono frames; other formats (a caller-supplied
        # stereo/8-bit .wav) can still be carved fixed by passing chunk_seconds.
        if audio.channels != 1 or audio.sampwidth != 2:
            raise ValueError(
                "VAD carve requires s16le mono audio "
                f"(got channels={audio.channels}, sampwidth={audio.sampwidth}); "
                "pass chunk_seconds for the fixed carve"
            )
        cuts = carve.find_cuts(
            audio.frames,
            audio.sample_rate,
            min_s=self._vad_min_s,
            max_s=self._vad_max_s,
        )
        return carve.carve_at(audio, cuts)


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
    the carve mode, and pins the session base wall-clock (RFC3339, or now-UTC when
    omitted) for deterministic time stamping.

    Carve-mode resolution: an EXPLICIT fixed duration wins — the caller's
    ``chunk_seconds``, else the ``CHUNK_SECONDS`` env var; with neither, VAD carve.
    Env is read here rather than via ``settings.chunk_seconds`` because Settings
    bakes in the 5.0 default and cannot distinguish "operator pinned 5 s" from
    "nothing set" — and that distinction now selects the mode.
    """
    if chunk_seconds is None:
        env_fixed = os.getenv("CHUNK_SECONDS")
        if env_fixed is not None:
            chunk_seconds = float(env_fixed)
    base = timeutil.parse_wallclock(base_wallclock)
    if source:
        audio = wav.read_wav(Path(source).read_bytes())
    else:
        seconds = sample_seconds if sample_seconds is not None else settings.sample_seconds
        audio = wav.read_wav(
            wav.generate_sample_wav(seconds=seconds, sample_rate=settings.sample_rate)
        )
    return WavFileSource(
        audio,
        chunk_seconds=chunk_seconds,
        base=base,
        codec=settings.codec,
        vad_min_s=float(
            os.getenv("VAD_MIN_CHUNK_SECONDS", str(carve.DEFAULT_MIN_CHUNK_SECONDS))
        ),
        vad_max_s=float(
            os.getenv("VAD_MAX_CHUNK_SECONDS", str(carve.DEFAULT_MAX_CHUNK_SECONDS))
        ),
    )
