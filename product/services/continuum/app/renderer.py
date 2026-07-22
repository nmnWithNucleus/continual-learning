"""Materialize the day log as files at the trainer seam.

The ported research code consumes segments.jsonl / blocks.jsonl / day.txt from
disk; canonical rows live upstream (storage-side derived views are the plan of
record — DB tables, not node files). This renderer is the bridge: it writes the
exact file shapes at the boundary, so the trainer never needs to know where
rows actually live.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from . import fsio
from .daylog import Block, DayLog


def render_daylog_files(daylog: DayLog, outdir: str | Path) -> dict[str, str]:
    out = Path(outdir)
    seg_path = out / "segments.jsonl"
    blk_path = out / "blocks.jsonl"
    txt_path = out / "day.txt"
    # Atomic writes: the journal treats existence as completeness.
    fsio.atomic_write_text(
        seg_path, "".join(json.dumps(asdict(s)) + "\n" for s in daylog.segments))
    fsio.atomic_write_text(
        blk_path, "".join(json.dumps(asdict(b)) + "\n" for b in daylog.blocks))
    fsio.atomic_write_text(txt_path, "\n\n".join(b.text for b in daylog.blocks) + "\n")
    return {"segments": str(seg_path), "blocks": str(blk_path), "day_txt": str(txt_path)}


def render_corpus_file(text: str, outdir: str | Path, name: str = "corpus.txt") -> str:
    path = Path(outdir) / name
    fsio.atomic_write_text(path, text)
    return str(path)


def blocks_text(blocks: list[Block]) -> str:
    return "\n\n".join(b.text for b in blocks)
