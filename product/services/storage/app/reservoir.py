"""C14 — the training reservoir: every night's amplified corpus, kept forever.

LIFTED from ``services/continuum/app/reservoir.py`` (the ``Reservoir`` class), which D18
moves into storage's custody. Continuum writes to it; storage owns it.

THE ONE INVARIANT IT EXISTS TO PROTECT: amplified/synthetic text NEVER lands in
``/context``. ``/context`` is the faithful record (grounding, think-back paging); this is a
TRAINING artifact store. Same storage discipline, different namespace — which is why it is
a separate root on disk and a separate contract, not a ``kind`` column on ``context_records``.

AUDIT / PROVENANCE, NOT THE REPLAY HOT PATH. Replay re-reads prior *day-logs* via C10 (the
locked architecture; amplified source is a measured tie and rawlog is simpler). What the
ledger is actually for is answering "which windows has this user consolidated, under which
recipe, over which content" without shipping the corpora.

RETENTION IS KEEP-EVERYTHING. There is no sweeper here and none is coming: replay is what
rescues sequential consolidation from collapse, and generative replay demonstrably fails,
so real past text is required *forever*. Deletion is a deliberate privacy act reached
through M5's cascade — never housekeeping, never a schedule.

THREE RULES, and each is a rule rather than an implementation detail:

  1. **THE CORPUS LANDS FIRST, THE META SECOND.** Each admission writes two files and the
     META IS THE COMMIT MARKER: a crash between them leaves an orphan corpus body that the
     ledger cannot see, so a torn admission is INVISIBLE rather than half-visible. Both
     writes are atomic (tmp + fsync + rename), so neither file is ever partially readable.
     Reversing the order would publish a ledger row pointing at a body that may not exist.
  2. **APPEND-ONLY, KEYED ``(user_id, window_id, recipe_id)``, CONTENT-HASHED.**
     Re-admitting identical content is a NO-OP — it does not even move ``admitted_at``.
     Admitting *different* content under a key that already holds some is a CONFLICT (409),
     not an overwrite: the corpus is a fact about a night that already happened, and an
     append-only audit store that silently replaces its own evidence is not one. The key
     includes ``recipe_id`` because a window re-run under a new recipe legitimately yields
     a second corpus and both must survive.
  3. **``window_id`` IS OPAQUE AND IS NEVER PARSED.** It is compared with ``<`` / ``>=``
     and nothing else. Continuum's ``ReservoirEntry.local_window_date()`` parsed it into a
     date and is being deleted for exactly that: under a ``[last_trained_t, now-delta)``
     window there is no local date to name (23 h, 25 h, or 47 h after a missed night).

Filesystem substrate, mirroring ``/raw``'s: bodies on disk under ``STORAGE_RESERVOIR_DIR``,
one directory per user. No SQLite row shadows them — a second index would be a second
source of truth about what has been admitted, and rule 1 is precisely a statement about
which single artifact is authoritative.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

# The service's ONE timestamp minter (second-granularity RFC3339 UTC, lexicographically
# ordered). Imported rather than restated so the reservoir's `admitted_at` can never drift
# from the format `/context`'s `ingest_time` and the window ledger already use.
from .db import _utc_now
from .ids import validate_path_id

logger = logging.getLogger("storage.reservoir")

_DEFAULT_RESERVOIR_DIR = Path(__file__).resolve().parent / "reservoir"

# The two halves of an admission. The corpus suffix is written first; the meta suffix is
# the commit marker and is what `entries()` globs for.
_CORPUS_SUFFIX = ".corpus.txt"
_META_SUFFIX = ".meta.json"


def reservoir_dir() -> str:
    """Root of the amplified-corpus store (env-overridable for tests/CI)."""
    return os.environ.get("STORAGE_RESERVOIR_DIR", str(_DEFAULT_RESERVOIR_DIR))


class CorpusConflict(Exception):
    """A different corpus is already admitted under this key; the route maps this to 409.

    Not an error to be smoothed over: admission is append-only, so the second body does not
    replace the first. Either the caller re-ran a night non-deterministically (in which case
    the two corpora are genuinely different artifacts and the second needs its own
    ``recipe_id``), or something is admitting under a window id it does not own.
    """

    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail.get("error", "corpus conflict")))
        self.detail = detail


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:  # pragma: no cover - some filesystems refuse dir fsync
        pass  # rename is still atomic; only the directory entry's durability is weaker


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` so the name either holds the complete content or does not exist.

    tmp + flush + fsync + rename + directory fsync — lifted from continuum's ``fsio``,
    because the ordering guarantee in rule 1 is worth nothing if either individual write
    can be observed half-finished. (This is deliberately stronger than ``/raw``'s
    tmp+rename: a blob is re-uploadable from the device, an amplified corpus is not
    reproducible at all.)
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


class Reservoir:
    def __init__(self, root: Optional[str] = None) -> None:
        self.root = Path(root or reservoir_dir())
        self.root.mkdir(parents=True, exist_ok=True)

    # --- layout ------------------------------------------------------------------

    def _user_dir(self, user_id: str) -> Path:
        """One directory per user — the hard per-user namespace the charter requires.

        ``user_id`` is validated here and not merely at the edge: this is the call that
        turns a string into a path, so this is where an escape would happen.
        """
        if not validate_path_id(user_id):
            raise ValueError(f"unsafe user_id {user_id!r}: it becomes a path component")
        return self.root / user_id

    @staticmethod
    def _stem(window_id: str, recipe_id: str) -> str:
        """The admission key as one filename stem.

        ``{window_id}__{recipe_id}`` is injective because ``window_id`` is fixed-format
        (``w<YYYYMMDD>T<HHMMSS>Z``) and contains no underscore, so the split point is never
        ambiguous — but nothing ever splits it: the meta file carries every field, and
        parsing identity back out of a filename is the habit this design is avoiding.
        """
        return f"{window_id}__{recipe_id}"

    def corpus_path(self, user_id: str, window_id: str, recipe_id: str) -> Path:
        return self._user_dir(user_id) / f"{self._stem(window_id, recipe_id)}{_CORPUS_SUFFIX}"

    def meta_path(self, user_id: str, window_id: str, recipe_id: str) -> Path:
        return self._user_dir(user_id) / f"{self._stem(window_id, recipe_id)}{_META_SUFFIX}"

    # --- the write side ------------------------------------------------------------

    def admit(
        self, user_id: str, window_id: str, recipe_id: str, corpus_text: str
    ) -> dict[str, Any]:
        """Admit a night's amplified corpus. Returns the ledger entry.

        Idempotent on CONTENT: re-admitting the identical corpus under the same key returns
        the existing entry and writes nothing at all — ``admitted_at`` records when the
        artifact landed, not when someone last asked about it (the same rule ``/context``
        applies to ``ingest_time`` across a reprocess). Admitting different content under a
        key that already committed raises ``CorpusConflict``.

        An admission that was TORN (corpus on disk, meta never written) has not committed,
        so it does not conflict with anything: the retry simply rewrites both files and
        commits. That is the whole reason the meta is the marker.
        """
        sha = hashlib.sha256(corpus_text.encode("utf-8")).hexdigest()
        meta_path = self.meta_path(user_id, window_id, recipe_id)
        existing = _read_json(meta_path)
        if existing is not None:
            if existing.get("sha") == sha:
                return _entry(existing)  # no-op: identical content, already committed
            raise CorpusConflict(
                {
                    "error": "a different corpus is already admitted for this key",
                    "user_id": user_id,
                    "window_id": window_id,
                    "recipe_id": recipe_id,
                    "admitted_sha": existing.get("sha"),
                    "offered_sha": sha,
                    "reason": "the reservoir is append-only — a corpus is a fact about a "
                              "night that already happened and is never overwritten",
                }
            )

        # RULE 1, and the order is the contract: body first, commit marker second.
        _atomic_write_text(
            self.corpus_path(user_id, window_id, recipe_id), corpus_text
        )
        meta = {
            "user_id": user_id,
            "window_id": window_id,
            "recipe_id": recipe_id,
            "sha": sha,
            "chars": len(corpus_text),
            "admitted_at": _utc_now(),
        }
        _atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=1))
        return _entry(meta)

    # --- the read side -------------------------------------------------------------

    def entries(
        self, user_id: str, *, before_window: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """The user's ledger, ascending by ``(window_id, recipe_id)``. Never the bodies.

        ``before_window`` filters to entries STRICTLY BEFORE it, by plain string comparison
        on the opaque window id — the only operation a consumer may perform on one. Its
        caller is replay: never replay the window being trained, or the future.

        Torn/unreadable meta files are SKIPPED LOUDLY (logged, not raised): an admission
        interrupted mid-write never committed, and one damaged file must not make a user's
        whole ledger unreadable on the night that matters.
        """
        udir = self.root / user_id if validate_path_id(user_id) else None
        if udir is None or not udir.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for meta_path in sorted(udir.glob(f"*{_META_SUFFIX}")):
            meta = _read_json(meta_path)
            if meta is None:
                continue  # torn meta — the admission never committed
            if before_window is not None and str(meta.get("window_id")) >= before_window:
                continue
            out.append(_entry(meta))
        out.sort(key=lambda e: (e["window_id"], e["recipe_id"]))
        return out

    def read_corpus(self, user_id: str, window_id: str, recipe_id: str) -> Optional[str]:
        """The corpus body. NOT on the ledger read, and deliberately not an endpoint yet:
        nothing consumes it over the wire (replay reads day-logs), and the body read that
        M5's cascade will need is a different surface with different auth. Here so tests and
        the deletion primitive have one accessor rather than each rebuilding the path."""
        path = self.corpus_path(user_id, window_id, recipe_id)
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")


def _entry(meta: dict[str, Any]) -> dict[str, Any]:
    """The C14 ledger row, projected from the meta file.

    Explicit field list, not the raw meta: the on-disk record may grow fields the contract
    does not carry, and the ledger body is ``additionalProperties: false``. Note what is
    NOT here — the local PATH. It is a fact about this server's disk, meaningless to a
    remote caller and not something a storage service should publish.
    """
    return {
        "user_id": meta["user_id"],
        "window_id": meta["window_id"],
        "recipe_id": meta["recipe_id"],
        "sha": meta["sha"],
        "chars": meta["chars"],
        "admitted_at": meta["admitted_at"],
    }


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.warning("torn/unreadable reservoir meta at %s (%s) — treating as absent",
                       path, exc)
        return None
    return loaded if isinstance(loaded, dict) else None


def ledger_body(
    user_id: str, entries: list[dict[str, Any]], before_window: Optional[str]
) -> dict[str, Any]:
    """The C14 ledger response body."""
    return {
        "contract": "C14",
        "version": "0",
        "user_id": user_id,
        "before_window": before_window,
        "entries": entries,
    }
