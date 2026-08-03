"""ChunkSource seam + the modality -> source registry (recording's plug-in point).

The emit path (``capturer.run_capture``) selects a source purely by modality through
``build_source`` and then depends only on the ``ChunkSource`` interface. Adding a new
modality is a two-step, conflict-free change for a future capture:

  1. Drop a NEW disjoint file in this package (e.g. ``webcam_source.py``) providing an
     object that satisfies ``ChunkSource`` plus a ``build(settings, **overrides)`` func.
  2. Register it here with ONE line in ``SOURCE_BUILDERS`` (the only shared-core edit;
     entries are keyed by disjoint modality so parallel modality captures do not clash).

Neither the emit path nor the C1 wire shape changes. Builders share a uniform signature
``build(settings, *, source, chunk_seconds, sample_seconds, base_wallclock) ->
ChunkSource``; a modality reads the overrides it needs and ignores the rest.
"""
from __future__ import annotations

from typing import Callable

from ..config import Settings
from . import replay_source, wav_source
from .base import ChunkSource, SourceChunk

# source key -> builder. THE registry. A future modality capture adds exactly one entry.
# The key is normally the C1 modality; a VARIANT source for a modality that is already
# claimed takes a suffixed key ("replay" for the Phase-3 recorded-day replay, which
# still emits modality='audio' chunks). The key never reaches the wire: the C1 envelope
# carries `source.modality`, so a variant is indistinguishable downstream -- which is
# exactly the property the replay depends on. The HTTP surface stays keyed to the frozen
# C1 enum (models.CaptureRunRequest); variants are driven through the module CLI.
SOURCE_BUILDERS: dict[str, Callable[..., ChunkSource]] = {
    "audio": wav_source.build,
    "replay": replay_source.build,
}


def build_source(modality: str, *, settings: Settings, **overrides) -> ChunkSource:
    """Construct the registered ``ChunkSource`` for ``modality``.

    ``overrides`` are the per-request knobs (source, chunk_seconds, sample_seconds,
    base_wallclock) forwarded to the modality's builder. Raises ``ValueError`` with the
    known modalities if none is registered — a clear signal to drop in a source file.
    """
    try:
        builder = SOURCE_BUILDERS[modality]
    except KeyError:
        known = ", ".join(sorted(SOURCE_BUILDERS)) or "(none)"
        raise ValueError(
            f"no ChunkSource registered for modality {modality!r}; known: {known}"
        ) from None
    return builder(settings, **overrides)


__all__ = ["ChunkSource", "SourceChunk", "SOURCE_BUILDERS", "build_source"]
