"""The ChunkSource seam — the modality-agnostic source of a continuous capture stream.

This is the load-bearing factoring of the recording service. The blob-first
PUT-to-/raw + C1-push emit path (``capturer.run_session``) is already modality-agnostic:
it takes opaque bytes + wall-clock timing + a modality/codec pair and does not care how
those bytes were captured or carved. This module names that boundary so future capturers
(screen / webcam / browser-extension / wearable; and the image / video / text pipelines)
plug in by dropping in ONE file that provides a ``ChunkSource`` — no edit to the emit
path, and no change to the C1 wire shape.

Split of responsibilities:
  * A ChunkSource owns MODALITY concerns — how a continuous stream is captured/decoded
    and carved into ordered chunks, and each chunk's device wall-clock span.
  * The emit path (the core) owns STREAM concerns — minting the globally-unique
    ``stream_id``, assigning the dense zero-based C1 ``sequence`` per chunk, hashing the
    bytes, the blob-first PUT to storage /raw, and pushing the validated C1 envelope.

Sequence numbering is deliberately NOT a source concern: a source only promises it yields
chunks IN CAPTURE ORDER; the core assigns ``sequence`` densely as it emits.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol, runtime_checkable


@dataclass(frozen=True)
class SourceChunk:
    """One carved chunk handed to the emit path: opaque bytes + its wall-clock span.

    * ``data`` — the complete, self-contained blob for this chunk (a standalone WAV for
      audio; an encoded still for image; an encoded clip/keyframe for video; a UTF-8 span
      for text). The emit path treats it as OPAQUE: it hashes it, PUTs it to /raw, and
      references it by ``blob_ref`` in C1. It is never interpreted here — interpretation
      (ASR/OCR/caption/…) is data-processing's job downstream.
    * ``t_start`` / ``t_end`` — RFC3339 UTC wall-clock strings, C1's time-spine axis. The
      source stamps device wall-clock (an instant modality like image may set
      ``t_start == t_end``); the emit path copies them into C1 verbatim.

    Carries NO sequence/index: ordering is implicit in iteration order and the emit path
    assigns the dense, zero-based C1 ``sequence`` per ``stream_id``.
    """

    data: bytes
    t_start: str
    t_end: str


@runtime_checkable
class ChunkSource(Protocol):
    """A continuous capture source for ONE modality+codec, carved into ordered chunks.

    The emit path (``capturer.run_session``) depends ONLY on this interface. A new
    modality is added by dropping in one file that provides an object satisfying it
    (structurally — no base class to import) plus a builder registered in
    ``app/sources/__init__.py``.

    Members:
      * ``modality`` — one of C1's frozen enum values: 'audio' | 'image' | 'video' |
        'text'. Copied to the C1 envelope's ``modality``.
      * ``codec`` — the MIME/codec token describing every chunk's bytes (C1 ``codec``),
        e.g. 'audio/wav', 'image/png', 'video/mp4', 'text/plain'.
      * ``chunks()`` — yield ``SourceChunk``s in capture order (zero or more). Each
        yielded chunk becomes exactly one /raw blob + one pushed C1 envelope.
    """

    modality: str
    codec: str

    def chunks(self) -> Iterator[SourceChunk]:
        ...
