"""SQLite continuity ledger for the ingest path (D-M1-3): captures / segments / streams / chunks.

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
CREATE TABLE IF NOT EXISTS captures (
  capture_id            TEXT PRIMARY KEY,
  user_id               TEXT NOT NULL,
  device_id             TEXT NOT NULL,
  started_at            TEXT NOT NULL,
  ended                 INTEGER NOT NULL DEFAULT 0,
  expected_segments     INTEGER,                    -- last_segment_num+1 once the end marker lands
  duplicate_deliveries  INTEGER NOT NULL DEFAULT 0  -- client-leg re-POSTs of an already-received segment_num
);
CREATE TABLE IF NOT EXISTS segments (
  capture_id  TEXT NOT NULL,
  segment_num         INTEGER NOT NULL,
  sha256      TEXT NOT NULL,
  bytes       INTEGER NOT NULL,
  mime        TEXT NOT NULL,
  t_start     TEXT NOT NULL,
  t_end       TEXT NOT NULL,
  received_at TEXT NOT NULL,
  state       TEXT NOT NULL DEFAULT 'received',     -- received | emitted | failed
  spool_path  TEXT NOT NULL,
  device_tz   TEXT,                                 -- D17: IANA zone the device reported (NULL = not reported)
  device_utc_offset_minutes INTEGER,                -- D17: offset the device believed at t_start
  error       TEXT,                                 -- why state == 'failed' (report visibility)
  PRIMARY KEY (capture_id, segment_num)
);
CREATE TABLE IF NOT EXISTS streams (
  stream_id     TEXT PRIMARY KEY,
  capture_id    TEXT NOT NULL,
  modality      TEXT NOT NULL,
  codec         TEXT NOT NULL,
  next_sequence INTEGER NOT NULL DEFAULT 0,
  UNIQUE (capture_id, modality)
);
CREATE TABLE IF NOT EXISTS chunks (
  stream_id  TEXT NOT NULL,
  sequence   INTEGER NOT NULL,
  capture_id TEXT NOT NULL,
  segment_num        INTEGER NOT NULL,                      -- the client-leg segment this came from
  modality   TEXT NOT NULL,
  chunk_id   TEXT NOT NULL,
  codec      TEXT NOT NULL,
  bytes      INTEGER NOT NULL,
  sha256     TEXT NOT NULL,
  blob_ref   TEXT,
  dp_acked   INTEGER NOT NULL DEFAULT 0,            -- 1 ONLY once DP CONFIRMED the C2 exists
  dp_state   TEXT,                                  -- NULL(unemitted) | accepted | processed
  record_ids TEXT,                                  -- JSON list from the /ingest ack
  emitted_at TEXT,
  PRIMARY KEY (stream_id, sequence),
  UNIQUE (capture_id, segment_num, modality)
);
"""

# Additive migrations for ledger.db files created before a column existed. SQLite has no
# ADD COLUMN IF NOT EXISTS, so we probe pragma table_info and add what's missing. Fresh
# DBs (every test) already have the column via _SCHEMA — this only touches upgrades. The
# optional backfill runs ONCE, right after the column is added, so pre-slice rows get a
# correct value instead of NULL (e.g. an inline chunk with dp_acked=1 IS 'processed', not
# the NULL that would read as 'unemitted' in the rec_chunks_dp_state metric).
_MIGRATIONS = [
    ("chunks", "dp_state", "TEXT",
     "UPDATE chunks SET dp_state = 'processed' WHERE dp_acked = 1 AND dp_state IS NULL"),
    # D17 civil-time context. No backfill: a segment received before the clients
    # reported a zone genuinely has no zone, and inventing one (e.g. the server's)
    # would be exactly the silent-wrong-timezone failure this slice exists to kill.
    # NULL is the honest value and reads downstream as "fall back to home_tz".
    ("segments", "device_tz", "TEXT", None),
    ("segments", "device_utc_offset_minutes", "INTEGER", None),
]

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
                        self._migrate_renames(conn)
                        conn.executescript(_SCHEMA)
                        self._migrate(conn)
                    _initialized.add(key)

    @staticmethod
    def _migrate_renames(conn: sqlite3.Connection) -> None:
        """Nomenclature cleanup (2026-07-29): capture_id + segment_num, table `captures`
        (were session_id, seq, `sessions` — the names collided with the serve loop's chat
        session and with C1's `sequence`). Runs BEFORE _SCHEMA so an old-name ledger.db is
        renamed in place rather than gaining empty new-name tables beside the old ones.
        No-op on fresh or already-renamed DBs; PRAGMA on a missing table yields no rows."""
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "sessions" in tables and "captures" not in tables:
            conn.execute("ALTER TABLE sessions RENAME TO captures")
        for table, old, new in (
            ("captures", "session_id", "capture_id"),
            ("segments", "session_id", "capture_id"),
            ("segments", "seq", "segment_num"),
            ("streams", "session_id", "capture_id"),
            ("chunks", "session_id", "capture_id"),
            ("chunks", "seq", "segment_num"),
        ):
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if old in cols and new not in cols:
                conn.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Add any columns missing from a pre-existing ledger.db (additive only), and
        backfill them once so upgraded rows aren't left NULL."""
        for table, column, decl, backfill in _MIGRATIONS:
            cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                if backfill:
                    conn.execute(backfill)

    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None == autocommit: single statements commit themselves,
        # multi-step operations open an explicit BEGIN IMMEDIATE below.
        conn = sqlite3.connect(self._path, timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # ------------------------------------------------------------------ captures

    def ensure_capture(
        self, capture_id: str, *, user_id: str, device_id: str, started_at: str
    ) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO captures (capture_id, user_id, device_id, started_at)"
                " VALUES (?, ?, ?, ?)",
                (capture_id, user_id, device_id, started_at),
            )

    def get_capture(self, capture_id: str) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM captures WHERE capture_id = ?", (capture_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    def mark_ended(self, capture_id: str, last_segment_num: int) -> bool:
        """Record the client's end marker. Idempotent; False if the capture is unknown.

        MONOTONIC on expected_segments: the client beacons an end marker on every
        page-hide, so a stale/late-delivered marker must never LOWER the expected
        count a newer marker (or a received segment) already established.
        """
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "UPDATE captures SET ended = 1,"
                " expected_segments = MAX(COALESCE(expected_segments, 0), ?)"
                " WHERE capture_id = ?",
                (last_segment_num + 1, capture_id),
            )
            return cur.rowcount > 0

    def reopen_if_past_end(self, capture_id: str, segment_num: int) -> None:
        """A freshly received segment at/past the end marker proves that marker
        stale (a pagehide beacon fired mid-capture and recording continued): clear
        ``ended`` so the verdict returns to 'recording' until a newer end marker,
        and never against a stale expected count. Keeps expected monotonic."""
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE captures SET ended = 0,"
                " expected_segments = MAX(COALESCE(expected_segments, 0), ?)"
                " WHERE capture_id = ? AND ended = 1"
                " AND COALESCE(expected_segments, 0) <= ?",
                (segment_num + 1, capture_id, segment_num),
            )

    def capture_summaries(self) -> list[dict]:
        with closing(self._connect()) as conn:
            captures = conn.execute(
                "SELECT * FROM captures ORDER BY started_at, capture_id"
            ).fetchall()
            counts: dict[str, dict[str, int]] = {}
            for row in conn.execute(
                "SELECT capture_id, state, COUNT(*) AS n FROM segments GROUP BY capture_id, state"
            ):
                counts.setdefault(row["capture_id"], {})[row["state"]] = row["n"]
        out = []
        for sess in captures:
            by_state = counts.get(sess["capture_id"], {})
            out.append(
                {
                    "capture_id": sess["capture_id"],
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
        capture_id: str,
        segment_num: int,
        *,
        sha256: str,
        nbytes: int,
        mime: str,
        t_start: str,
        t_end: str,
        received_at: str,
        spool_path: str,
        device_tz: str | None = None,
        device_utc_offset_minutes: int | None = None,
    ) -> tuple[str, str]:
        """Record one delivered segment. Returns (status, state) where status is
        'received' | 'duplicate' | 'conflict' and state is the segment's CURRENT
        ledger state ('received' for a fresh insert).

        Idempotent on (capture_id, segment_num): a re-POST with the same sha counts a
        duplicate_delivery (client-leg observability); the caller uses the returned
        state to self-heal a duplicate whose first pass never finished (re-enqueue
        while state is still 'received'). A different sha for the same segment_num is a
        client bug surfaced as 'conflict' (409).
        """
        with closing(self._connect()) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT sha256, state FROM segments WHERE capture_id = ? AND segment_num = ?",
                    (capture_id, segment_num),
                ).fetchone()
                if row is not None:
                    state = row["state"]
                    if row["sha256"] == sha256:
                        conn.execute(
                            "UPDATE captures SET duplicate_deliveries = duplicate_deliveries + 1"
                            " WHERE capture_id = ?",
                            (capture_id,),
                        )
                        status = "duplicate"
                    else:
                        status = "conflict"
                else:
                    conn.execute(
                        "INSERT INTO segments (capture_id, segment_num, sha256, bytes, mime,"
                        " t_start, t_end, received_at, state, spool_path,"
                        " device_tz, device_utc_offset_minutes)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'received', ?, ?, ?)",
                        (capture_id, segment_num, sha256, nbytes, mime, t_start, t_end,
                         received_at, spool_path, device_tz,
                         device_utc_offset_minutes),
                    )
                    status = state = "received"
                conn.execute("COMMIT")
                return status, state
            except BaseException:
                conn.rollback()
                raise

    def segment(self, capture_id: str, segment_num: int) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM segments WHERE capture_id = ? AND segment_num = ?",
                (capture_id, segment_num),
            ).fetchone()
            return dict(row) if row is not None else None

    def segment_states(self, capture_id: str) -> list[tuple[int, str]]:
        with closing(self._connect()) as conn:
            return [
                (row["segment_num"], row["state"])
                for row in conn.execute(
                    "SELECT segment_num, state FROM segments WHERE capture_id = ? ORDER BY segment_num",
                    (capture_id,),
                )
            ]

    def set_segment_state(
        self, capture_id: str, segment_num: int, state: str, *, error: str | None = None
    ) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE segments SET state = ?, error = ? WHERE capture_id = ? AND segment_num = ?",
                (state, error, capture_id, segment_num),
            )

    def reset_failed(self, capture_id: str) -> list[int]:
        """Flip a capture's failed segments back to 'received' (the /retry path)."""
        with closing(self._connect()) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                segment_nums = [
                    row["segment_num"]
                    for row in conn.execute(
                        "SELECT segment_num FROM segments WHERE capture_id = ? AND state = 'failed'"
                        " ORDER BY segment_num",
                        (capture_id,),
                    )
                ]
                conn.execute(
                    "UPDATE segments SET state = 'received', error = NULL"
                    " WHERE capture_id = ? AND state = 'failed'",
                    (capture_id,),
                )
                conn.execute("COMMIT")
                return segment_nums
            except BaseException:
                conn.rollback()
                raise

    def pending_segments(self) -> list[tuple[str, int]]:
        """All acked-but-unemitted segments, per-capture segment_num order (startup re-enqueue)."""
        with closing(self._connect()) as conn:
            return [
                (row["capture_id"], row["segment_num"])
                for row in conn.execute(
                    "SELECT capture_id, segment_num FROM segments WHERE state = 'received'"
                    " ORDER BY capture_id, segment_num"
                )
            ]

    # ------------------------------------------------------------- streams/chunks

    def get_or_create_stream(self, capture_id: str, modality: str, codec: str) -> dict:
        with closing(self._connect()) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM streams WHERE capture_id = ? AND modality = ?",
                    (capture_id, modality),
                ).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO streams (stream_id, capture_id, modality, codec,"
                        " next_sequence) VALUES (?, ?, ?, ?, 0)",
                        (new_ulid(), capture_id, modality, codec),
                    )
                    row = conn.execute(
                        "SELECT * FROM streams WHERE capture_id = ? AND modality = ?",
                        (capture_id, modality),
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
        capture_id: str,
        segment_num: int,
        modality: str,
        codec: str,
        nbytes: int,
        sha256: str,
    ) -> tuple[int, str]:
        """Mint-or-reuse (sequence, chunk_id) for one demuxed chunk of a segment.

        First call inserts the chunk row AND advances streams.next_sequence in one
        transaction (dense sequence). A later call for the same (capture, segment_num,
        modality) — emit retry, or re-demux after a restart — returns the SAME
        identity, refreshing bytes/sha to match the bytes about to be emitted.
        """
        with closing(self._connect()) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT sequence, chunk_id FROM chunks"
                    " WHERE capture_id = ? AND segment_num = ? AND modality = ?",
                    (capture_id, segment_num, modality),
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
                        "INSERT INTO chunks (stream_id, sequence, capture_id, segment_num,"
                        " modality, chunk_id, codec, bytes, sha256, dp_acked)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                        (stream_id, sequence, capture_id, segment_num, modality, chunk_id,
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
        accepted: bool = False,
    ) -> None:
        """Record the /ingest ack for a chunk.

        ``accepted`` distinguishes the two DP reply shapes:
          * INLINE / processed ack (200, record_ids present, default): DP CONFIRMED the
            C2 exists → ``dp_acked=1``, ``dp_state='processed'``. Unchanged from M0.
          * ASYNC accept (202, no record_ids yet): DP merely ACCEPTED the chunk for
            later processing → ``dp_acked=0``, ``dp_state='accepted'``. The invariant
            ``dp_acked=1 ⇔ C2 durably written`` is preserved, so the gap report's
            ``_dp_missing_unacked`` reconciliation stays sound; the chunk is confirmed
            later via ``confirm_chunk`` when DP's /continuity reports it processed.
        Provenance (``record_ids``) is OPTIONAL at accept — recording tolerates the
        empty list (it's the async-ingest wire, decided jointly with data-processing)."""
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE chunks SET blob_ref = ?, dp_acked = ?, dp_state = ?,"
                " record_ids = ?, emitted_at = ? WHERE stream_id = ? AND sequence = ?",
                (blob_ref, 0 if accepted else 1, "accepted" if accepted else "processed",
                 json.dumps(record_ids), emitted_at, stream_id, sequence),
            )

    def accepted_unconfirmed_chunks(self, capture_id: str | None = None) -> list[dict]:
        """Chunks DP ACCEPTED (202) but hasn't confirmed processed — with everything
        needed to rebuild + re-push their C1 envelope (the D16 re-drive path). Joins the
        capture (user/device) + the source segment (wall-clock span)."""
        sql = (
            "SELECT c.stream_id, c.sequence, c.chunk_id, c.modality, c.codec, c.bytes,"
            " c.sha256, c.blob_ref, s.user_id, s.device_id, g.t_start, g.t_end,"
            " g.device_tz, g.device_utc_offset_minutes"
            " FROM chunks c"
            " JOIN captures s ON s.capture_id = c.capture_id"
            " JOIN segments g ON g.capture_id = c.capture_id AND g.segment_num = c.segment_num"
            " WHERE c.dp_state = 'accepted'"
        )
        params: tuple = ()
        if capture_id is not None:
            sql += " AND c.capture_id = ?"
            params = (capture_id,)
        sql += " ORDER BY c.stream_id, c.sequence"
        with closing(self._connect()) as conn:
            return [dict(row) for row in conn.execute(sql, params)]

    def confirm_chunk(
        self, stream_id: str, sequence: int, *, record_ids: list[str] | None = None
    ) -> None:
        """Promote an async-accepted chunk to CONFIRMED once DP's /continuity reports its
        C2 written (``dp_acked=1``, ``dp_state='processed'``). Persisting the promotion is
        what keeps the ack receipt across a DP restart (its in-memory processed set is
        volatile; ours is durable), so a confirmed chunk never reverts to 'in-flight'.
        Idempotent; only touches rows still 'accepted'."""
        with closing(self._connect()) as conn:
            if record_ids is None:
                conn.execute(
                    "UPDATE chunks SET dp_acked = 1, dp_state = 'processed'"
                    " WHERE stream_id = ? AND sequence = ? AND dp_state = 'accepted'",
                    (stream_id, sequence),
                )
            else:
                conn.execute(
                    "UPDATE chunks SET dp_acked = 1, dp_state = 'processed', record_ids = ?"
                    " WHERE stream_id = ? AND sequence = ? AND dp_state = 'accepted'",
                    (json.dumps(record_ids), stream_id, sequence),
                )

    def streams_for_capture(self, capture_id: str) -> list[dict]:
        with closing(self._connect()) as conn:
            return [
                dict(row)
                for row in conn.execute(
                    # ORDER BY modality: 'audio' < 'video' — stable report order.
                    "SELECT * FROM streams WHERE capture_id = ? ORDER BY modality",
                    (capture_id,),
                )
            ]

    def metrics_snapshot(self) -> dict:
        """Aggregate counts for the /metrics scrape (D9). Bounded — all totals across
        captures, never per-capture series. Client-leg missing is the per-capture gap
        walk summed (dev/beta scale is tiny)."""
        with closing(self._connect()) as conn:
            seg_states: dict[str, int] = {"received": 0, "emitted": 0, "failed": 0}
            for row in conn.execute(
                "SELECT state, COUNT(*) AS n FROM segments GROUP BY state"
            ):
                seg_states[row["state"]] = row["n"]

            chunks_by_modality: dict[str, int] = {}
            for row in conn.execute(
                "SELECT modality, COUNT(*) AS n FROM chunks GROUP BY modality"
            ):
                chunks_by_modality[row["modality"]] = row["n"]

            chunks_by_dp_state: dict[str, int] = {"accepted": 0, "processed": 0, "unemitted": 0}
            for row in conn.execute(
                "SELECT COALESCE(dp_state, 'unemitted') AS s, COUNT(*) AS n"
                " FROM chunks GROUP BY s"
            ):
                chunks_by_dp_state[row["s"]] = row["n"]

            sess = conn.execute(
                "SELECT COUNT(*) AS total, COALESCE(SUM(1 - ended), 0) AS active,"
                " COALESCE(SUM(duplicate_deliveries), 0) AS dups FROM captures"
            ).fetchone()

            # Client-leg missing: per capture, holes below max-received segment_num + the tail an
            # ended capture's expected count reveals.
            missing_total = 0
            captures = conn.execute(
                "SELECT capture_id, ended, expected_segments FROM captures"
            ).fetchall()
            for s in captures:
                segment_nums = [r["segment_num"] for r in conn.execute(
                    "SELECT segment_num FROM segments WHERE capture_id = ?", (s["capture_id"],)
                )]
                if not segment_nums and not (s["ended"] and s["expected_segments"]):
                    continue
                top = max(segment_nums) if segment_nums else -1
                count = (top + 1) - len(set(segment_nums))
                if s["ended"] and s["expected_segments"] and s["expected_segments"] > top + 1:
                    count += s["expected_segments"] - (top + 1)
                missing_total += count

        return {
            "segments_by_state": seg_states,
            "chunks_by_modality": chunks_by_modality,
            "chunks_by_dp_state": chunks_by_dp_state,
            "captures_total": sess["total"],
            "captures_active": sess["active"],
            "client_duplicate_deliveries_total": sess["dups"],
            "client_missing_total": missing_total,
        }

    def stream_chunks(self, stream_id: str) -> list[dict]:
        """Chunk rows + their source segment's state (for per-stream pending/failed)."""
        with closing(self._connect()) as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT c.sequence, c.segment_num, c.chunk_id, c.codec, c.dp_acked,"
                    " c.dp_state, c.blob_ref, g.state AS segment_state"
                    " FROM chunks c JOIN segments g"
                    "   ON g.capture_id = c.capture_id AND g.segment_num = c.segment_num"
                    " WHERE c.stream_id = ? ORDER BY c.sequence",
                    (stream_id,),
                )
            ]
