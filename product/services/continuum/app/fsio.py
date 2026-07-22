"""Crash-safe file primitives for everything the nightly loop persists.

All durable state (journal, user state, alias, entries log, reservoir, rendered
corpora, mock adapters) goes through these: atomic tmp+fsync+rename for whole
files (a name either holds complete content or doesn't exist), fsync'd appends
with torn-tail repair for the entries log, and torn-tolerant readers — a
power-loss mid-write must never wedge the next night's run.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("continuum.fsio")


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass  # some filesystems refuse directory fsync; rename is still atomic


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def atomic_write_text(path: str | Path, text: str) -> None:
    atomic_write_bytes(path, text.encode())


def atomic_write_json(path: str | Path, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, indent=1))


def read_json(path: str | Path, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("torn/unreadable JSON at %s (%s) — treating as absent", path, exc)
        return default


def append_jsonl(path: str | Path, obj: Any) -> None:
    """Fsync'd append with torn-tail repair: if a previous append was cut mid-line,
    start on a fresh line so one torn row never corrupts its successor."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_newline = False
    if path.exists() and path.stat().st_size > 0:
        with path.open("rb") as f:
            f.seek(-1, os.SEEK_END)
            needs_newline = f.read(1) != b"\n"
    with path.open("a") as f:
        if needs_newline:
            f.write("\n")
        f.write(json.dumps(obj) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_jsonl(path: str | Path) -> list[Any]:
    """Skips torn lines LOUDLY instead of raising — an interrupted append must not
    make the log unreadable."""
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for i, line in enumerate(path.read_text().splitlines()):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("skipping torn line %d in %s", i + 1, path)
    return out
