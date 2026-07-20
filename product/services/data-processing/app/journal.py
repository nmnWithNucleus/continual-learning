"""Durable ingest journal — the M7 heart: accepted chunks + processed receipts survive restart.

The async /ingest slice shipped with an honest, VISIBLE loss boundary: the accepted-queue,
the dedup done-map, and the continuity ``processed``/``dead_lettered`` sets were in-memory,
so a kill lost queued chunks (re-drivable only from recording's side) and a DP restart
forgot what it had durably written (the deferred false-``gaps`` caveat). This journal closes
that boundary:

  * ``pending``   — every async-ACCEPTED chunk's full C1, inserted inside the claim BEFORE
    the 202 goes out. Startup re-drives every ``state='accepted'`` row (recording's
    ``reenqueue_pending`` precedent), so a kill -9 auto-recovers with no external re-drive.
    A dead-lettered chunk stays here as ``state='dead_letter'`` (durable, for ops), and a
    redelivery resets it to ``accepted`` for another attempt.
  * ``processed``  — one row per chunk whose C2s are durably written (BOTH modes). Feeds:
    (a) continuity REHYDRATION at boot — including still-pending rows as SEEN — so a
    restart forgets nothing and recording's gap report can never mis-read intact history
    as loss; (b) the durable dedup backstop — a redelivery after restart is answered with
    the prior record_ids instead of a reprocess (unless the pipeline dialect changed, in
    which case reprocessing is the honest answer — version-forward).

Two safety mechanisms shape every write (from the design review):

  * **Epochs.** Each ``accept`` bumps the row's ``epoch``; terminal writes
    (``mark_processed`` pending-delete, ``mark_dead_letter``) are guarded on the epoch the
    worker was handed. A stale worker finishing AFTER a redelivery re-accepted the chunk
    can no longer clobber the fresh row — its write no-ops. (The processed INSERT itself is
    deliberately unguarded: if the C2s were written, the receipt is true regardless.)
  * **Bounded re-drive.** ``pending_for_redrive`` durably increments ``redrive_attempts``
    and flips over-cap rows to ``dead_letter`` inside one transaction — a poison chunk
    whose processing crash-loops the service breaks the loop VISIBLY instead of forever.
    An external redelivery (a conscious re-push) resets the counter.

Storage discipline mirrors recording's ledger (the proven pattern): SQLite in
``$DP_VAR_DIR/dp.db``, WAL, connection-per-call, ``BEGIN IMMEDIATE`` for multi-step ops.
LAZY: constructing a ``Journal`` touches no filesystem (module-import safety); reads on a
non-existent DB return empty; the first WRITE creates it. Callers on the event loop wrap
every mutation in ``run_in_threadpool`` — a WAL fsync must never stall the loop.

This is operational bookkeeping, not user-content custody: blobs stay in storage ``/raw``,
records in ``/context``. ``processed`` grows with chunks ever processed — dev-scale fine;
compaction/retention is a fleet-scale follow-up.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending (
  chunk_id         TEXT PRIMARY KEY,
  c1_json          TEXT NOT NULL,
  state            TEXT NOT NULL DEFAULT 'accepted',  -- accepted | dead_letter
  epoch            INTEGER NOT NULL DEFAULT 0,        -- bumped per accept; guards terminal writes
  attempts         INTEGER NOT NULL DEFAULT 0,        -- dead-letter events on this chunk
  redrive_attempts INTEGER NOT NULL DEFAULT 0,        -- startup re-drives since last accept
  last_error       TEXT,
  accepted_at      TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processed (
  chunk_id         TEXT PRIMARY KEY,
  stream_id        TEXT NOT NULL,
  sequence         INTEGER NOT NULL,
  user_id          TEXT NOT NULL,
  device_id        TEXT NOT NULL,
  modality         TEXT NOT NULL,
  record_ids       TEXT NOT NULL,                     -- JSON list, unit order
  pipeline_version TEXT NOT NULL,
  processed_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_processed_stream ON processed (stream_id, sequence);
"""

# Which db files this process already initialized (schema is idempotent; skip the churn).
_initialized: set[str] = set()
_init_lock = threading.Lock()

# Fields of a prior pending row snapshotted by accept() so unaccept() can restore it.
_SNAPSHOT_COLS = ("c1_json", "state", "epoch", "attempts", "redrive_attempts",
                  "last_error", "accepted_at", "updated_at")


class Journal:
    def __init__(self, db_path: Path | str) -> None:
        self._path = Path(db_path)

    # ------------------------------------------------------------------ plumbing
    def _exists(self) -> bool:
        return self._path.is_file()

    def _connect(self, *, create: bool) -> Optional[sqlite3.Connection]:
        """Open a connection. ``create=False`` (reads / best-effort updates) returns None
        when the DB file doesn't exist yet — a fresh service reads empty, no mkdir."""
        if not create and not self._exists():
            return None
        key = str(self._path)
        if key not in _initialized:
            with _init_lock:
                if key not in _initialized:
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    conn = sqlite3.connect(self._path, timeout=5.0, isolation_level=None)
                    try:
                        conn.execute("PRAGMA journal_mode=WAL")
                        conn.executescript(_SCHEMA)
                    finally:
                        conn.close()
                    _initialized.add(key)
        conn = sqlite3.connect(self._path, timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # ------------------------------------------------------------------- pending
    def accept(self, c1: dict[str, Any], now: str) -> tuple[int, Optional[dict]]:
        """Record an async-accepted chunk BEFORE its 202 goes out.

        Returns ``(epoch, prior_snapshot)``: the NEW epoch to thread through the worker
        job (terminal writes are guarded on it), and the prior row (dict of columns) or
        None — the ``unaccept`` restore point for the QueueFull path. A redelivery of a
        dead-lettered chunk resets it to 'accepted' for another attempt (its dead-letter
        count survives in ``attempts``; ``redrive_attempts`` resets — an external re-push
        is a conscious re-arm)."""
        with closing(self._connect(create=True)) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                prior = conn.execute(
                    "SELECT * FROM pending WHERE chunk_id = ?", (c1["chunk_id"],)
                ).fetchone()
                snapshot = {k: prior[k] for k in _SNAPSHOT_COLS} if prior is not None else None
                epoch = (prior["epoch"] + 1) if prior is not None else 0
                attempts = prior["attempts"] if prior is not None else 0
                conn.execute(
                    "INSERT OR REPLACE INTO pending"
                    " (chunk_id, c1_json, state, epoch, attempts, redrive_attempts,"
                    "  last_error, accepted_at, updated_at)"
                    " VALUES (?, ?, 'accepted', ?, ?, 0, NULL, ?, ?)",
                    (c1["chunk_id"], json.dumps(c1, separators=(",", ":")),
                     epoch, attempts, now, now),
                )
                conn.execute("COMMIT")
                return epoch, snapshot
            except BaseException:
                conn.rollback()
                raise

    def unaccept(self, chunk_id: str, prior: Optional[dict]) -> None:
        """Roll back an ``accept`` whose enqueue failed (QueueFull -> 503). The 503 told
        recording "NOT accepted", so the journal must not contradict it — but it must
        also not lose history: a replaced dead_letter row is RESTORED (with its error),
        only a genuinely fresh row is deleted. Exists ONLY on the HTTP accept path."""
        conn = self._connect(create=False)
        if conn is None:
            return
        with closing(conn):
            if prior is None:
                conn.execute("DELETE FROM pending WHERE chunk_id = ?", (chunk_id,))
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO pending"
                    " (chunk_id, c1_json, state, epoch, attempts, redrive_attempts,"
                    "  last_error, accepted_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (chunk_id, prior["c1_json"], prior["state"], prior["epoch"],
                     prior["attempts"], prior["redrive_attempts"], prior["last_error"],
                     prior["accepted_at"], prior["updated_at"]),
                )

    def mark_dead_letter(self, chunk_id: str, error: str, now: str, epoch: int) -> None:
        """Durably mark a chunk dead-lettered (terminal / retries exhausted) — guarded on
        the worker's epoch AND state='accepted', so a stale worker finishing after a
        redelivery re-accepted the chunk no-ops instead of clobbering the fresh row."""
        conn = self._connect(create=False)
        if conn is None:
            return
        with closing(conn):
            conn.execute(
                "UPDATE pending SET state = 'dead_letter', attempts = attempts + 1,"
                " last_error = ?, updated_at = ?"
                " WHERE chunk_id = ? AND epoch = ? AND state = 'accepted'",
                (error[:2000], now, chunk_id, epoch),
            )

    def pending_for_redrive(self, max_attempts: int, now: str) -> list[dict[str, Any]]:
        """The startup re-drive set, with EVIDENCE-BASED crash-loop bounding: in ONE
        transaction, flip to dead_letter only accepted rows whose ``redrive_attempts``
        (accrued by ``note_redrive_attempt`` — a per-PROCESSING-attempt counter, NOT a
        per-restart one) already EXCEED ``max_attempts``, then return the C1s of the
        survivors (oldest first). The count is attributed to a chunk only when its OWN
        processing was attempted, so a hard crash-loop poisoned by ONE chunk can never
        dead-letter an innocent co-pending backlog that was never dequeued (the blanket
        per-restart increment did exactly that). The flipped rows show as dead_lettered
        after rehydration (which runs next), so recording sees them as gaps."""
        conn = self._connect(create=False)
        if conn is None:
            return []
        with closing(conn):
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE pending SET state = 'dead_letter',"
                    " last_error = 'crash-loop: re-drive processing attempts exhausted ('"
                    " || redrive_attempts || ')', updated_at = ?"
                    " WHERE state = 'accepted' AND redrive_attempts > ?",
                    (now, max_attempts),
                )
                rows = [
                    {"c1": json.loads(r["c1_json"]), "epoch": r["epoch"]}
                    for r in conn.execute(
                        "SELECT c1_json, epoch FROM pending WHERE state = 'accepted'"
                        " ORDER BY accepted_at, chunk_id"
                    )
                ]
                conn.execute("COMMIT")
                return rows
            except BaseException:
                conn.rollback()
                raise

    def note_redrive_attempt(self, chunk_id: str, now: str) -> None:
        """Charge ONE re-drive processing attempt to a chunk — called by the worker just
        BEFORE it processes a re-driven job, so the crash-loop cap counts a chunk's OWN
        attempts (a chunk whose processing kills the service accrues; one merely queued
        behind it never does). No-op once the row is gone (processed) or not accepted."""
        conn = self._connect(create=False)
        if conn is None:
            return
        with closing(conn):
            conn.execute(
                "UPDATE pending SET redrive_attempts = redrive_attempts + 1, updated_at = ?"
                " WHERE chunk_id = ? AND state = 'accepted'",
                (now, chunk_id),
            )

    def pending_accepted(self) -> list[dict[str, Any]]:
        """Accepted-but-unprocessed C1s, oldest first (read-only view for tests/ops)."""
        conn = self._connect(create=False)
        if conn is None:
            return []
        with closing(conn):
            return [
                json.loads(row["c1_json"])
                for row in conn.execute(
                    "SELECT c1_json FROM pending WHERE state = 'accepted'"
                    " ORDER BY accepted_at, chunk_id"
                )
            ]

    # ----------------------------------------------------------------- processed
    def mark_processed(
        self,
        c1: dict[str, Any],
        record_ids: list[str],
        pipeline_version: str,
        now: str,
        epoch: int = 0,
    ) -> None:
        """The durable receipt: C2s written -> pending row deleted (epoch-guarded) +
        processed row upserted, one transaction. The processed INSERT is deliberately
        NOT epoch-guarded: if the C2s were written, the receipt is true regardless of
        which delivery's worker wrote them. Called in BOTH modes (inline has no pending
        row; the guarded DELETE simply no-ops)."""
        with closing(self._connect(create=True)) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "DELETE FROM pending WHERE chunk_id = ? AND epoch = ?",
                    (c1["chunk_id"], epoch),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO processed"
                    " (chunk_id, stream_id, sequence, user_id, device_id, modality,"
                    "  record_ids, pipeline_version, processed_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        c1["chunk_id"], c1["stream_id"], c1["sequence"], c1["user_id"],
                        c1["device_id"], c1["modality"],
                        json.dumps(record_ids), pipeline_version, now,
                    ),
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.rollback()
                raise

    def processed_record_ids(
        self,
        chunk_id: str,
        pv_for_modality: Optional[Callable[[str], Optional[str]]] = None,
    ) -> Optional[list[str]]:
        """The durable dedup backstop: record_ids for an already-processed chunk, or
        None. When ``pv_for_modality`` is given, a receipt whose stored
        ``pipeline_version`` no longer matches the CURRENT dialect for its modality
        returns None — the redelivery reprocesses under the new config instead of being
        served stale ids (version-forward; the old records remain in /context)."""
        conn = self._connect(create=False)
        if conn is None:
            return None
        with closing(conn):
            row = conn.execute(
                "SELECT record_ids, modality, pipeline_version FROM processed"
                " WHERE chunk_id = ?", (chunk_id,)
            ).fetchone()
        if row is None:
            return None
        if pv_for_modality is not None:
            current = pv_for_modality(row["modality"])
            if current is not None and current != row["pipeline_version"]:
                return None  # dialect changed -> honest answer is a reprocess
        return json.loads(row["record_ids"])

    # --------------------------------------------------------------- rehydration
    def rehydration(self) -> dict[str, dict[str, Any]]:
        """Per-stream state for ContinuityTracker rehydration at boot. THREE classes:
        ``processed`` (seen + C2 written), ``dead`` (seen + terminally failed), and
        ``accepted`` (seen, still in flight — the keystone: a pending chunk counts as
        DELIVERED coverage, so a restart can never fabricate a gap out of a chunk that
        is merely waiting to be re-driven). Shape:
        ``{stream_id: {user_id, device_id, modality,
        processed|dead|accepted: [(seq, chunk_id, at)]}}``."""
        conn = self._connect(create=False)
        if conn is None:
            return {}
        streams: dict[str, dict[str, Any]] = {}

        def _stream(sid: str, user_id: str, device_id: str, modality: str) -> dict:
            entry = streams.get(sid)
            if entry is None:
                entry = {
                    "user_id": user_id, "device_id": device_id, "modality": modality,
                    "processed": [], "dead": [], "accepted": [],
                }
                streams[sid] = entry
            return entry

        with closing(conn):
            for row in conn.execute(
                "SELECT stream_id, sequence, chunk_id, user_id, device_id, modality,"
                " processed_at FROM processed"
            ):
                _stream(row["stream_id"], row["user_id"], row["device_id"], row["modality"])[
                    "processed"
                ].append((row["sequence"], row["chunk_id"], row["processed_at"]))
            for row in conn.execute(
                "SELECT c1_json, state, updated_at FROM pending"
            ):
                c1 = json.loads(row["c1_json"])
                cls = "dead" if row["state"] == "dead_letter" else "accepted"
                _stream(c1["stream_id"], c1["user_id"], c1["device_id"], c1["modality"])[
                    cls
                ].append((c1["sequence"], c1["chunk_id"], row["updated_at"]))
        return streams

    # -------------------------------------------------------------------- gauges
    def counts(self) -> dict[str, int]:
        """Row counts for /metrics pull-time gauges (zeros when no DB exists yet)."""
        conn = self._connect(create=False)
        if conn is None:
            return {"pending": 0, "dead_letter": 0, "processed": 0}
        with closing(conn):
            by_state = {
                row["state"]: row["n"]
                for row in conn.execute(
                    "SELECT state, COUNT(*) AS n FROM pending GROUP BY state"
                )
            }
            processed = conn.execute("SELECT COUNT(*) AS n FROM processed").fetchone()["n"]
        return {
            "pending": by_state.get("accepted", 0),
            "dead_letter": by_state.get("dead_letter", 0),
            "processed": processed,
        }
