"""Day-log blocks — the unit of consolidation.

A block is one stretch of a life rendered to anchored plain text. It is the ONLY
interface between ingest and consolidation: the trainer never sees records,
frames, or transcripts, only blocks. Keeping that boundary narrow is what lets
the day-log move behind a storage client (2c) without any kernel noticing.

Rendering lives on the Profile (the field schema and anchor scheme are
domain-specific); this module owns the transport-neutral shapes around it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

_RESERVED = ("block_id", "text", "order")


@dataclass(frozen=True)
class Block:
    block_id: str
    text: str
    anchors: Mapping[str, Any] = field(default_factory=dict)
    order: int = 0


def render_blocks(records: Iterable[Mapping[str, Any]], profile, *,
                  extra_anchors: Mapping[str, Any] | None = None) -> list[Block]:
    """Source records -> ordered blocks, in the order given.

    Choosing and ordering the records is the day-log's job (storage, 2c); this
    is the rendering half, which is recipe-coupled and therefore ours."""
    return [
        Block(block_id=str(rec["chunk_id"] if "chunk_id" in rec else rec["block_id"]),
              text=profile.render_block(rec),
              anchors={**profile.anchors_of(rec), **(extra_anchors or {})},
              order=i)
        for i, rec in enumerate(records)
    ]


def load_blocks(path: str | Path, *,
                extra_anchors: Mapping[str, Any] | None = None) -> list[Block]:
    """Read already-materialized blocks (`blocks.jsonl`).

    Every non-reserved column becomes an anchor, so a profile can read whatever
    scheme its day-log writes without this loader knowing the scheme. In 2a the
    file is the day-log; in 2c the same shape arrives from the day-log client."""
    blocks = []
    for i, line in enumerate(Path(path).read_text().splitlines()):
        if not line.strip():
            continue
        row = json.loads(line)
        anchors = {k: v for k, v in row.items() if k not in _RESERVED}
        blocks.append(Block(block_id=row["block_id"], text=row["text"],
                            anchors={**anchors, **(extra_anchors or {})},
                            order=int(row.get("order", i))))
    return blocks


def blocks_corpus(blocks: Iterable[Block]) -> str:
    """The raw day text: blocks joined by a blank line.

    This exact join is also the rehearsal unit — `replay` resamples paragraphs
    split on the same separator, so raw-source replay and amplified-source replay
    are interchangeable at the sampler."""
    return "\n\n".join(b.text for b in blocks)
