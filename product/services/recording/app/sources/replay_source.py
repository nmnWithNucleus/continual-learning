"""The REPLAY ChunkSource: a recorded day of audio, re-emitted as a capture.

Phase 3 (the DP dogfood) pushes an already-recorded life-stream through the real emit
path so the rest of the pipeline cannot tell it apart from a live capture. This is the
ChunkSource that does it, and it is the whole of recording's part: everything below it
-- blob-first PUT, C1 minting, sequence assignment, the /ingest push -- is the
unmodified emit path.

It replays a PLAN (a jsonl file, one row per chunk, in capture order):

    {"chunk_key": "8H5FyKfwjoY_c000", "audio": "<cached .wav>",
     "media": "gs://.../8H5FyKfwjoY_c000.mkv",
     "t_start": "2025-09-02T08:00:00Z", "t_end": "2025-09-02T08:20:05.732Z", ...}

Why a plan file rather than a manifest + a time rule: the wall clock a chunk is stamped
with is ALSO what data-processing joins its injected captions on, so the spine has to be
derived once and read by both services rather than re-derived in each (see continuum's
scripts/phase3_build_replay.py). The source is deliberately dumb about time: it copies
t_start/t_end straight from the plan.

One plan file = ONE capture = one day, so the whole day shares a stream_id and
gets a dense sequence, which is what an always-on capture actually looks like.

The bytes are real audio, never a stand-in: the blob has to serve double duty as the
thing ASR transcribes AND the thing that satisfies data-processing's mandatory
blob_ref/sha256 check. A missing cache entry is extracted on demand, streaming the
source media out of GCS through ffmpeg (no 600 MB temp file), and cached for re-runs.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Iterator

from ..config import Settings
from .base import SourceChunk

logger = logging.getLogger("recording.sources.replay")

# 16 kHz mono s16le: faster-whisper's native rate, and a standalone WAV per chunk.
SAMPLE_RATE = 16000


class ReplayPlanSource:
    """ChunkSource for modality='audio' driven by a recorded plan.

    Declares modality 'audio' (not a new C1 value -- C1's modality enum is frozen, and
    these ARE audio chunks); only the SOURCE-registry key distinguishes it, so the
    envelope a replayed chunk produces is indistinguishable from a live one.
    """

    modality = "audio"

    def __init__(self, rows: list[dict], *, codec: str, ffmpeg_bin: str,
                 gsutil_bin: str = "gsutil") -> None:
        self._rows = rows
        self.codec = codec
        self._ffmpeg = ffmpeg_bin
        self._gsutil = gsutil_bin

    def chunks(self) -> Iterator[SourceChunk]:
        for row in self._rows:
            yield SourceChunk(data=self._audio(row),
                              t_start=row["t_start"], t_end=row["t_end"])

    def _audio(self, row: dict) -> bytes:
        path = Path(row["audio"])
        if path.exists() and path.stat().st_size > 44:   # 44 = a WAV header alone
            return path.read_bytes()
        if not row.get("media"):
            raise FileNotFoundError(
                f"replay chunk {row['chunk_key']}: no cached audio at {path} and no "
                "'media' to extract it from")
        logger.info("replay %s: extracting audio from %s", row["chunk_key"], row["media"])
        data = self._extract(row["media"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.part")
        tmp.write_bytes(data)
        tmp.rename(path)                                  # atomic: a reader never sees a partial
        return data

    def _extract(self, media: str) -> bytes:
        """Source media -> a standalone 16 kHz mono WAV, streamed (no temp file)."""
        reader = ["cat", media] if not media.startswith("gs://") else \
                 [self._gsutil, "-q", "cat", media]
        with subprocess.Popen(reader, stdout=subprocess.PIPE) as src:
            out = subprocess.run(
                [self._ffmpeg, "-v", "error", "-i", "pipe:0", "-vn", "-ac", "1",
                 "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"],
                stdin=src.stdout, capture_output=True)
            if src.stdout:
                src.stdout.close()
        # BOTH ends are checked. ffmpeg happily encodes a truncated stream and exits 0, so
        # a reader that died mid-transfer would otherwise cache a short WAV -- and a short
        # WAV is a silently shorter day, not a loud failure.
        if src.returncode != 0:
            raise RuntimeError(f"reading {media} failed (rc={src.returncode})")
        if out.returncode != 0 or len(out.stdout) <= 44:
            raise RuntimeError(
                f"audio extraction failed for {media} (rc={out.returncode}): "
                f"{out.stderr.decode()[:400]}")
        return out.stdout


def load_plan(path: str | Path) -> list[dict]:
    """Read the plan, keeping FILE ORDER: the plan is the capture order, and the emit
    path assigns C1 sequence from it."""
    rows = [json.loads(line) for line in Path(path).expanduser().read_text().splitlines()
            if line.strip()]
    if not rows:
        raise ValueError(f"replay plan {path} is empty")
    missing = [k for k in ("chunk_key", "t_start", "t_end", "audio") if k not in rows[0]]
    if missing:
        raise ValueError(f"replay plan {path}: rows are missing {missing}")
    return rows


def build(
    settings: Settings,
    *,
    source: str | None = None,
    chunk_seconds: float | None = None,
    sample_seconds: float | None = None,
    base_wallclock: str | None = None,
) -> ReplayPlanSource:
    """Builder registered for the 'replay' source key (see app/sources/__init__.py).

    ``source`` is the plan file. The carve/sample/base-wallclock overrides are
    deliberately ignored: a replay is not carved (each planned chunk is one chunk) and
    its wall clock comes from the plan, not from a capture base -- silently re-carving
    or re-stamping it would break the join data-processing does against the same spine.
    """
    if not source:
        raise ValueError("replay source requires --source <day{D}.replay.jsonl>")
    for name, value in (("chunk_seconds", chunk_seconds),
                        ("sample_seconds", sample_seconds),
                        ("base_wallclock", base_wallclock)):
        if value is not None:
            logger.warning("replay source ignores %s=%r (the plan owns the spine)",
                           name, value)
    return ReplayPlanSource(load_plan(source), codec=settings.codec,
                            ffmpeg_bin=settings.ffmpeg_bin,
                            gsutil_bin=os.getenv("GSUTIL_BIN", "gsutil"))
