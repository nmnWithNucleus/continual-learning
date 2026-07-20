"""Audio processor — now a thin GraphProcessor over the audio STAGE GRAPH.

The pipeline that used to live here (asr → diarize → translate → acoustic_events, a
staged blackboard) is now four drop-in stage files under ``app/stages/audio/``; this
module is the compat shim that keeps the public seam byte-stable:

  * class ``AudioProcessor`` registered via ``@register`` (the registry + HTTP core are
    unchanged);
  * ``modality='audio'``, ``content_kind='transcript'``;
  * ``pipeline_version`` composes exactly as before (``asr-mock-v0`` /
    ``asr-mock-v0+diar-mock-v1`` / ``asr-fw-v1`` …) — see ``stagegraph.executor.resolve``.

Behaviour is identical (same units, same order [primary, translation, acoustic], same
record_ids, same version forks); the only difference is that independent stages
(acoustic ∥ asr) now run concurrently. Add a stage (speaker-identity, a new enricher) by
dropping a file in ``app/stages/audio/`` — you never touch this shim.
"""
from __future__ import annotations

from ...stagegraph import GraphProcessor
from ..registry import register


@register
class AudioProcessor(GraphProcessor):
    modality = "audio"
    content_kind = "transcript"
