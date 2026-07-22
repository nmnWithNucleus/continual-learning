"""The training reservoir — every night's amplified corpus, kept forever.

The one non-negotiable invariant of the whole data design: amplified/synthetic
text NEVER lands in `/context`. `/context` is the faithful record (grounding,
think-back paging); the reservoir is a TRAINING artifact store. Same storage
discipline, different namespace. Replay is what rescues sequential
consolidation from collapse, and generative replay demonstrably fails — real
past text is required forever, so admission is append-only and deletion is a
deliberate privacy act, never housekeeping.

v0 home: filesystem under var_dir (keyed user/window/recipe). The storage-
hosted home (DB/GCS) is a later migration behind this same interface.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from pathlib import Path

from . import fsio
from .ids import validate_id

_NEG_MARKER = "Contrary to what one might assume"


@dataclass(frozen=True)
class ReservoirEntry:
    user_id: str
    window_id: str
    recipe_id: str
    path: str
    sha: str = ""   # corpus content hash — replay-mix stage keys hang off this


class Reservoir:
    def __init__(self, var_dir: str | Path):
        self.root = Path(var_dir) / "reservoir"

    def _user_dir(self, user_id: str) -> Path:
        return self.root / validate_id(user_id, "user_id")

    def admit(self, user_id: str, window_id: str, recipe_id: str, corpus_text: str) -> ReservoirEntry:
        """Idempotent: re-admitting the same window overwrites with identical
        content. Corpus lands atomically FIRST, meta second — the meta file is
        the commit marker, so a torn admission is invisible to entries()."""
        udir = self._user_dir(user_id)
        validate_id(window_id, "window_id")
        path = udir / f"{window_id}.corpus.txt"
        sha = hashlib.sha256(corpus_text.encode()).hexdigest()
        fsio.atomic_write_text(path, corpus_text)
        fsio.atomic_write_json(udir / f"{window_id}.meta.json", {
            "user_id": user_id, "window_id": window_id, "recipe_id": recipe_id,
            "chars": len(corpus_text), "sha": sha,
            "neg_paragraphs": sum(1 for p in corpus_text.split("\n\n")
                                  if p.startswith(_NEG_MARKER)),
        })
        return ReservoirEntry(user_id, window_id, recipe_id, str(path), sha)

    def entries(self, user_id: str, *, before_window: str | None = None) -> list[ReservoirEntry]:
        udir = self._user_dir(user_id)
        if not udir.is_dir():
            return []
        out = []
        for meta_path in sorted(udir.glob("*.meta.json")):
            meta = fsio.read_json(meta_path)
            if meta is None:
                continue  # torn meta — admission never committed; skip loudly (logged)
            if before_window is not None and meta["window_id"] >= before_window:
                continue  # never replay the window being trained (or the future)
            out.append(ReservoirEntry(meta["user_id"], meta["window_id"],
                                      meta["recipe_id"],
                                      str(udir / f"{meta['window_id']}.corpus.txt"),
                                      meta.get("sha", "")))
        return out

    def sample_replay(self, user_id: str, *, target_chars: int, frac: float,
                      seed: int, before_window: str | None = None) -> str:
        """Uniform paragraph sampling over the pooled past corpora (the locked
        recipe's sampler: uniform, not forgetting-weighted), budgeted at
        frac * target_chars. Empty reservoir (first night) -> empty replay.

        Deliberately pooled-uniform like the research code: bigger days get
        proportionally more paragraphs. The deny-then-correct re-exposure boost
        (replay_neg_boost — traps erode by ~night 12 without it) is a recipe
        knob wired when the ported sampler lands; paragraphs are already
        tagged at admission for it.
        """
        entries = self.entries(user_id, before_window=before_window)
        if not entries or frac <= 0:
            return ""
        paras: list[str] = []
        for entry in entries:
            text = Path(entry.path).read_text()
            paras.extend(p for p in text.split("\n\n") if len(p) > 100)
        if not paras:
            return ""
        rng = random.Random(seed)
        rng.shuffle(paras)
        budget = int(frac * target_chars)
        picked: list[str] = []
        used = 0
        for p in paras:
            if used >= budget:
                break
            picked.append(p)
            used += len(p)
        return "\n\n".join(picked)
