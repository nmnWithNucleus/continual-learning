"""The neutral shapes every modality plugin speaks — the load-bearing seam.

A ``Processor`` turns one validated C1 envelope + its raw blob bytes into a LIST
of ``ProcessedUnit`` (>= 1). The core assembles a C2 record per unit, so:

  * audio / image / text  -> a single-element list (1 chunk -> 1 record);
  * video                 -> many units (1 chunk -> many keyframe records),
                             each tagged with a ``discriminator`` (keyframe index)
                             so its ``record_id`` is stable AND distinct.

``ProcessedContent`` mirrors exactly the frozen C2 ``content`` object
(kind|text|language?|segments?) — the Processor emits content already in C2 shape
(segments carry ABSOLUTE RFC3339 times), so the core never has to know a modality's
timing/segment semantics. ``content.kind`` is one of the frozen C2 enum values:
``transcript`` (audio) | ``caption`` (image/video) | ``ocr`` | ``text``.

``enrichments`` is present-but-empty in v0 (mirrors C4's empty trace arrays) so
diarization / world-data enrichment never change the C2 shape.

``process`` is a PLAIN (blocking) method: the core runs it in a threadpool, so a
Processor may do heavy CPU/GPU work (ASR today, VLM captioning later) without
blocking the event loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import Settings


def empty_enrichments() -> dict[str, list]:
    """The present-but-empty enrichments block — exactly the 4 frozen C2 keys."""
    return {"speakers": [], "faces": [], "places": [], "objects": []}


@dataclass
class ProcessedContent:
    """Mirrors the frozen C2 ``content`` object.

    ``segments`` (when present) are already absolute-time C2 segment dicts
    ``{t_start, t_end, text, speaker}`` — the Processor owns the offset->absolute
    mapping, so the core stays modality-agnostic.
    """

    kind: str                                   # transcript | caption | ocr | text
    text: str
    language: Optional[str] = None              # BCP-47; omitted from C2 when falsy
    segments: Optional[list[dict[str, Any]]] = None  # omitted from C2 when empty/None


@dataclass
class ProcessedUnit:
    """One processed record-to-be. ``discriminator`` distinguishes multiple units
    from one chunk (e.g. a video keyframe index); ``''`` marks the 1:1 case.

    ``t_start`` / ``t_end`` are an OPTIONAL per-unit sub-span (absolute RFC3339,
    same axis as C1/C2 ``t_start/t_end``). They default to ``None``, in which case
    ``build_c2`` carries the chunk's C1 span verbatim — so every existing modality
    (audio/image/text, and the mock video fallback) is byte-identical to the
    pre-hook behaviour. A Processor that knows *when within the chunk* a unit
    occurs (e.g. video keyframe timing, per CHARTER OQ14a) sets them so each of a
    chunk's many records gets its own time-spine sub-span instead of colliding on
    the shared chunk span. No C2 schema change: C2 already carries per-record
    timestamps; this only lets a Processor choose them per unit."""

    content: ProcessedContent
    enrichments: dict[str, list] = field(default_factory=empty_enrichments)
    discriminator: str = ""
    t_start: Optional[str] = None  # per-unit sub-span start; None -> carry C1's
    t_end: Optional[str] = None    # per-unit sub-span end;   None -> carry C1's


class Processor:
    """One modality's transform. Subclass, set ``modality`` + ``content_kind``, and
    decorate with ``@register`` (see ``registry``) in a disjoint plugin file.

    Implementations MUST be pure w.r.t. their inputs (deterministic given the same
    C1 + bytes under the same ``pipeline_version``) so reprocessing is an idempotent
    upsert, per the C2 contract.
    """

    modality: str = ""        # C1/C2 modality enum value this plugin owns
    content_kind: str = ""    # the C2 content.kind it emits (declaration/doc only)

    def pipeline_version(self, settings: Settings) -> str:
        """The pipeline dialect stamped into every C2 this plugin produces. Folded
        (with chunk_id + discriminator) into ``record_id``, so a bump forks new
        records (version-forward reprocessing). May depend on ``settings`` (e.g.
        audio's ASR backend switch)."""
        raise NotImplementedError

    def process(
        self,
        c1: dict[str, Any],
        blob: bytes,
        settings: Settings,
        span_seconds: float,
    ) -> list[ProcessedUnit]:
        """Transform one chunk into >= 1 ProcessedUnit. Blocking is fine — the core
        offloads this to a threadpool. ``span_seconds`` is the chunk's wall-clock
        duration (C1 t_end - t_start), handed in so time-mapping needs no re-parse."""
        raise NotImplementedError
