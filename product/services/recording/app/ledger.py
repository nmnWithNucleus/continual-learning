"""SQLite continuity ledger for the ingest path (D-M1-3): sessions / segments / streams / chunks.

This is OPERATIONAL continuity metadata — which segments a client delivered, which C1
chunks were minted and where they landed — not durable user-content custody (content
custody stays with storage /raw; segment bytes only transit the spool). Two properties
enforced here carry the crash-safety story:

  * chunk identity is minted and PERSISTED before the first emit attempt
    (``allocate_chunk``), so a retry or a post-restart re-emit uses the SAME chunk_id
    and C1 sequence — idempotent downstream, no fabricated gaps;
  * C1 ``sequence`` comes from ``streams.next_sequence``, incremented in the SAME
    transaction that inserts the chunk row — dense per stream by construction.

Concurrency model: connection-per-call (each method opens, uses, closes its own
connection). That sidesteps thread-affinity entirely under FastAPI's threadpool +
asyncio mix; WAL keeps readers unblocked and ``BEGIN IMMEDIATE`` makes each multi-step
read-modify-write atomic across concurrent callers.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from .config import Settings
from .ids import new_ulid

DB_FILENAME = "ledger.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  session_id            TEXT PRIMARY KEY,
  user_id               TEXT NOT NULL,
  device_id             TEXT NOT NULL,
  started_at            TEXT NOT NULL,
  ended                 INTEGER NOT NULL DEFAULT 0,
  expected_segments     INTEGER,                    -- last_seq+1 once the end marker lands
  duplicate_deliveries  INTEGER NOT NULL DEFAULT 0  -- client-leg re-POSTs of an already-received seq
);
CREATE TABLE IF NOT EXISTS segments (
  session_id  TEXT NOT NULL,
  seq         INTEGER NOT NULL,
  sha256      TEXT NOT NULL,
  bytes       INTEGER NOT NULL,
  mime        TEXT NOT NULL,
  t_start     TEXT NOT NULL,
  t_end       TEXT NOT NULL,
  received_at TEXT NOT NULL,
  state       TEXT NOT NULL DEFAULT 'received',     -- received | emitted | failed
  spool_path  TEXT NOT NULL,
  error       TEXT,                                 -- why state == 'failed' (report visibility)
  PRIMARY KEY (session_id, seq)
);
CREATE TABLE IF NOT EXISTS streams (
  stream_id     TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL,
  modality      TEXT NOT NULL,
  codec         TEXT NOT NULL,
  next_sequence INTEGER NOT NULL DEFAULT 0,
  UNIQUE (session_id, modality)
);
CREATE TABLE IF NOT EXISTS chunks (
  stream_id  TEXT NOT NULL,
  sequence   INTEGER NOT NULL,
  session_id TEXT NOT NULL,
  seq        INTEGER NOT NULL,                      -- the client-leg segment this came from
  modality   TEXT NOT NULL,
  chunk_id   TEXT NOT NULL,
  codec      TEXT NOT NULL,
  bytes      INTEGER NOT NULL,
  sha256     TEXT NOT NULL,
  blob_ref   TEXT,
  dp_acked   INTEGER NOT NULL DEFAULT 0,
  record_ids TEXT,                                  -- JSON list from the /ingest ack
  emitted_at TEXT,
  PRIMARY KEY (stream_id, sequence),
  UNIQUE (session_id, seq, modality)
);
"""

# Schema is idempotent (IF NOT EXISTS) but issuing it per request is pointless churn;
# remember which db files this process already initialized.
_initialized: set[str] = set()
_init_lock = threading.Lock()


def for_settings(settings: Settings) -> "Ledger":
    return Ledger(Path(settings.var_dir) / DB_FILENAME)


class Ledger:
    def __init__(self, db_path: Path | str) -> None:
        self._path = Path(db_path)
        key = str(self._path.resolve())
        if key not in _initialized:
            with _init_lock:
                if key not in _initialized:
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    with closing(self._connect()) as conn:
                        conn.executescript(_SCHEMA)
                    _initialized.add(key)

    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None == autocommit: single statements commit themselves,
        # multi-step operations open an explicit BEGIN IMMEDIATE below.
        conn = sqlite3.connect(self._path, timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # ------------------------------------------------------------------ sessions

    def ensure_session(
        self, session_id: str, *, user_id: str, device_id: str, started_at: str
    ) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (session_id, user_id, device_id, started_at)"
                " VALUES (?, ?, ?, ?)",
                (session_id, user_id, device_id, started_at),
            )

    def get_session(self, session_id: str) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    def mark_ended(self, session_id: str, last_seq: int) -> bool:
        """Record the client's end marker. Idempotent; False if the session is unknown.

        MONOTONIC on expected_segments: the client beacons an end marker on every
        page-hide, so a stale/late-delivered marker must never LOWER the expected
        count a newer marker (or a received segment) already established.
        """
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "UPDATE sessions SET ended = 1,"
                " expected_segments = MAX(COALESCE(expected_segments, 0), ?)"
                " WHERE session_id = ?",
                (last_seq + 1, session_id),
            )
            return cur.rowcount > 0

    def reopen_if_past_end(self, session_id: str, seq: int) -> None:
        """A freshly received segment at/past the end marker proves that marker
        stale (a pagehide beacon fired mid-session and recording continued): clear
        ``ended`` so the verdict returns to 'recording' until a newer end marker,
        and never against a stale expected count. Keeps expected monotonic."""
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE sessions SET ended = 0,"
                " expected_segments = MAX(COALESCE(expected_segments, 0), ?)"
                " WHERE session_id = ? AND ended = 1"
                " AND COALESCE(expected_segments, 0) <= ?",
                (seq + 1, session_id, seq),
            )

    def session_summaries(self) -> list[dict]:
        with closing(self._connect()) as conn:
            sessions = conn.execute(
                "SELECT * FROM sessions ORDER BY started_at, session_id"
            ).fetchall()
            counts: dict[str, dict[str, int]] = {}
            for row in conn.execute(
                "SELECT session_id, state, COUNT(*) AS n FROM segments GROUP BY session_id, state"
            ):
                counts.setdefault(row["session_id"], {})[row["state"]] = row["n"]
        out = []
        for sess in sessions:
            by_state = counts.get(sess["session_id"], {})
            out.append(
                {
                    "session_id": sess["session_id"],
                    "user_id": sess["user_id"],
                    "device_id": sess["device_id"],
                    "started_at": sess["started_at"],
                    "ended": bool(sess["ended"]),
                    "expected_segments": sess["expected_segments"],
                    "received_segments": sum(by_state.values()),
                    "emitted_segments": by_state.get("emitted", 0),
                    "pending_segments": by_state.get("received", 0),
                    "failed_segments": by_state.get("failed", 0),
                }
            )
        return out

    # ------------------------------------------------------------------ segments

    def record_segment(
        self,
        session_id: str,
        seq: int,
        *,
        sha256: str,
        nbytes: int,
        mime: str,
        t_start: str,
        t_end: str,
        received_at: str,
        spool_path: str,
    ) -> tuple[str, str]:
        """Record one delivered segment. Returns (status, state) where status is
        'received' | 'duplicate' | 'conflict' and state is the segment's CURRENT
        ledger state ('received' for a fresh insert).

        Idempotent on (session_id, seq): a re-POST with the same sha counts a
        duplicate_delivery (client-leg observability); the caller uses the returned
        state to self-heal a duplicate whose first pass never finished (re-enqueue
        while state is still 'received'). A different sha for the same seq is a
        client bug surfaced as 'conflict' (409).
        """
        with closing(self._connect()) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT sha256, state FROM segments WHERE session_id = ? AND seq = ?",
                    (session_id, seq),
                ).fetchone()
                if row is not None:
                    state = row["state"]
                    if row["sha256"] == sha256:
                        conn.execute(
                            "UPDATE sessions SET duplicate_deliveries = duplicate_deliveries + 1"
                            " WHERE session_id = ?",
                            (session_id,),
                        )
                        status = "duplicate"
                    else:
                        status = "conflict"
                else:
                    conn.execute(
                        "INSERT INTO segments (session_id, seq, sha256, bytes, mime,"
                        " t_start, t_end, received_at, state, spool_path)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'received', ?)",
                        (session_id, seq, sha256, nbytes, mime, t_start, t_end,
                         received_at, spool_path),
                    )
                    status = state = "received"
                conn.execute("COMMIT")
                return status, state
            except BaseException:
                conn.rollback()
                raise

    def segment(self, session_id: str, seq: int) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM segments WHERE session_id = ? AND seq = ?",
                (session_id, seq),
            ).fetchone()
            return dict(row) if row is not None else None

    def segment_states(self, session_id: str) -> list[tuple[int, str]]:
        with closing(self._connect()) as conn:
            return [
                (row["seq"], row["state"])
                for row in conn.execute(
                    "SELECT seq, state FROM segments WHERE session_id = ? ORDER BY seq",
                    (session_id,),
                )
            ]

    def set_segment_state(
        self, session_id: str, seq: int, state: str, *, error: str | None = None
    ) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE segments SET state = ?, error = ? WHERE session_id = ? AND seq = ?",
                (state, error, session_id, seq),
            )

    def reset_failed(self, session_id: str) -> list[int]:
        """Flip a session's failed segments back to 'received' (the /retry path)."""
        with closing(self._connect()) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                seqs = [
                    row["seq"]
                    for row in conn.execute(
                        "SELECT seq FROM segments WHERE session_id = ? AND state = 'failed'"
                        " ORDER BY seq",
                        (session_id,),
                    )
                ]
                conn.execute(
                    "UPDATE segments SET state = 'received', error = NULL"
                    " WHERE session_id = ? AND state = 'failed'",
                    (session_id,),
                )
                conn.execute("COMMIT")
                return seqs
            except BaseException:
                conn.rollback()
                raise

    def pending_segments(self) -> list[tuple[str, int]]:
        """All acked-but-unemitted segments, per-session seq order (startup re-enqueue)."""
        with closing(self._connect()) as conn:
            return [
                (row["session_id"], row["seq"])
                for row in conn.execute(
                    "SELECT session_id, seq FROM segments WHERE state = 'received'"
                    " ORDER BY session_id, seq"
                )
            ]

    # ------------------------------------------------------------- streams/chunks

    def get_or_create_stream(self, session_id: str, modality: str, codec: str) -> dict:
        with closing(self._connect()) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM streams WHERE session_id = ? AND modality = ?",
                    (session_id, modality),
                ).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO streams (stream_id, session_id, modality, codec,"
                        " next_sequence) VALUES (?, ?, ?, ?, 0)",
                        (new_ulid(), session_id, modality, codec),
                    )
                    row = conn.execute(
                        "SELECT * FROM streams WHERE session_id = ? AND modality = ?",
                        (session_id, modality),
                    ).fetchone()
                conn.execute("COMMIT")
                return dict(row)
            except BaseException:
                conn.rollback()
                raise

    def allocate_chunk(
        self,
        *,
        stream_id: str,
        session_id: str,
        seq: int,
        modality: str,
        codec: str,
        nbytes: int,
        sha256: str,
    ) -> tuple[int, str]:
        """Mint-or-reuse (sequence, chunk_id) for one demuxed chunk of a segment.

        First call inserts the chunk row AND advances streams.next_sequence in one
        transaction (dense sequence). A later call for the same (session, seq,
        modality) — emit retry, or re-demux after a restart — returns the SAME
        identity, refreshing bytes/sha to match the bytes about to be emitted.
        """
        with closing(self._connect()) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT sequence, chunk_id FROM chunks"
                    " WHERE session_id = ? AND seq = ? AND modality = ?",
                    (session_id, seq, modality),
                ).fetchone()
                if row is not None:
                    sequence, chunk_id = row["sequence"], row["chunk_id"]
                    conn.execute(
                        "UPDATE chunks SET bytes = ?, sha256 = ?"
                        " WHERE stream_id = ? AND sequence = ?",
                        (nbytes, sha256, stream_id, sequence),
                    )
                else:
                    sequence = conn.execute(
                        "SELECT next_sequence FROM streams WHERE stream_id = ?",
                        (stream_id,),
                    ).fetchone()["next_sequence"]
                    chunk_id = new_ulid()
                    conn.execute(
                        "INSERT INTO chunks (stream_id, sequence, session_id, seq,"
                        " modality, chunk_id, codec, bytes, sha256, dp_acked)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                        (stream_id, sequence, session_id, seq, modality, chunk_id,
                         codec, nbytes, sha256),
                    )
                    conn.execute(
                        "UPDATE streams SET next_sequence = ? WHERE stream_id = ?",
                        (sequence + 1, stream_id),
                    )
                conn.execute("COMMIT")
                return sequence, chunk_id
            except BaseException:
                conn.rollback()
                raise

    def finalize_chunk(
        self,
        stream_id: str,
        sequence: int,
        *,
        blob_ref: str,
        record_ids: list[str],
        emitted_at: str,
    ) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE chunks SET blob_ref = ?, dp_acked = 1, record_ids = ?,"
                " emitted_at = ? WHERE stream_id = ? AND sequence = ?",
                (blob_ref, json.dumps(record_ids), emitted_at, stream_id, sequence),
            )

    def streams_for_session(self, session_id: str) -> list[dict]:
        with closing(self._connect()) as conn:
            return [
                dict(row)
                for row in conn.execute(
                    # ORDER BY modality: 'audio' < 'video' — stable report order.
                    "SELECT * FROM streams WHERE session_id = ? ORDER BY modality",
                    (session_id,),
                )
            ]

    def stream_chunks(self, stream_id: str) -> list[dict]:
        """Chunk rows + their source segment's state (for per-stream pending/failed)."""
        with closing(self._connect()) as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT c.sequence, c.seq, c.chunk_id, c.codec, c.dp_acked,"
                    " c.blob_ref, g.state AS segment_state"
                    " FROM chunks c JOIN segments g"
                    "   ON g.session_id = c.session_id AND g.seq = c.seq"
                    " WHERE c.stream_id = ? ORDER BY c.sequence",
                    (stream_id,),
                )
            ]
