"""The neutral acoustic-event result shape every acoustic backend returns.

An acoustic backend tags the non-speech audio of a chunk (dishwasher, car, dog, door,
music) and folds the salient tags into one short human caption (via
``caption.caption_from_tags``). ``text`` is that caption — what the stage writes as a
``content.kind="caption"`` record.

AST is a clip-level classifier, so there are no per-event timestamps — the caption is
chunk-level (no segments), which is exactly what ``kind="caption"`` carries. (A future
step could additionally surface the structured tags into ``enrichments.objects`` — an
additive C2 field — but v0 keeps the sidecar's enrichments present-but-empty like the
translation record, so this result carries only the caption text.)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AcousticResult:
    text: str                                   # the caption written to C2
