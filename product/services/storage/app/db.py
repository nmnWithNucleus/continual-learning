"""SQLite persistence for the serve-loop + learn-loop MVP.

Metadata tables (SQLite):
  - ``turns``: one row per C4 turn record. ``turn_id`` is the primary key; ``session_id``
    is indexed (with ``created_at``) so a session's turns list is a single index scan,
    ordered by ``created_at``. The full C4 is stored verbatim as JSON for exact round-trip.
  - ``model_directory``: the trivial C6 directory. A seeded ``_base_`` row is the default;
    a per-user row (when continuum ships adapters) would override it. v0 only ever seeds base.
  - ``raw_blobs``: the ``/raw`` blob-leg index. One row per captured chunk, keyed by
    ``chunk_id`` (the C1 dedup key). Holds the minted opaque ``blob_ref``, integrity fields
    (``sha256``/``bytes``) and provenance; the bytes themselves live on disk under the raw
    store dir at the path ``blob_ref`` names. Idempotent on ``chunk_id``.
  - ``context_records``: the ``/context`` store (C2 v1). One row per processed record, keyed
    by ``record_id`` (deterministic on ``(chunk_id, pipeline_version)`` upstream, so a
    reprocess is an idempotent upsert here). Indexed on ``(user_id, t_start)`` — the
    wall-clock time spine every reader leans on — AND on ``(user_id, updated_at)`` (D18's
    completeness index, moved to the D27 axis), because the two are different axes with
    different jobs: event time is the CONTENT axis (recall, day-log bucketing, deletion
    ranges) and the storage-assigned stamps are the COMPLETENESS axis, the only one on
    which "everything I had" is a guarantee, so it is what the training window watermarks
    on. Full C2 stored verbatim as JSON. D27 split the old ``ingest_time`` in two:
    ``created_at`` = the FIRST landing of a record_id (preserved across every re-POST);
    ``updated_at`` = the last time ``record_json`` actually CHANGED — ``put_context``
    byte-compares and bumps only on real change, so a no-op redelivery never re-windows
    a record while a byte-different heal flows into the next window.
  - ``user_profiles``: the per-user C12 profile — POLICY the system reads to decide its own
    behaviour for this user (today: ``home_tz``). Written ONLY by an explicit profile write;
    storage never seeds or infers it. Absent row == 404 == this user is not schedulable.
  - ``training_windows``: the D18 training-window ledger. One row per window over the
    ``[last_trained_t, now-delta)`` ingest-time watermark; bounds immutable once opened. The
    watermark itself is NOT a column — it is derived from this table (see
    ``Store.last_trained_t``), which is what makes "advances iff published" true by
    construction rather than by bookkeeping.
  - ``day_logs``: the materialized C10 day-log per ``(user_id, window_id)`` — a DERIVED
    VIEW over ``context_records``, built on demand at fetch and cached. Rebuildable from
    C2 at any time, and invalidated automatically when a change touches its
    ``updated_at`` range — BOTH windows of a moved record (see ``Store.put_context``).

Blob bytes (dev store): a local directory tree under ``STORAGE_RAW_DIR`` (default
``app/raw_store`` beside this module). ``blob_ref`` is an opaque, storage-minted, hex-sharded
relative path under that dir (GCS is the production target). Prod would move the bytes to a
bucket while the metadata tables stay here.

Dev-grade: a fresh connection per operation (low volume, handful of pilot users). The DB
file path comes from ``STORAGE_DB_PATH`` (default: ``app/dev.db`` beside this module).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .window_id import mint_window_id

_DEFAULT_DB_PATH = Path(__file__).resolve().parent / "dev.db"
_DEFAULT_RAW_DIR = Path(__file__).resolve().parent / "raw_store"

# Sentinel user_id for the default base directory entry (v0: everyone resolves to base).
_BASE_KEY = "_base_"

BASE_MODEL_ID = "Qwen/Qwen3-VL-32B-Instruct"
BASE_ADAPTER = "base"
BASE_ADAPTER_PATH: Optional[str] = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    turn_id      TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    completed_at TEXT,
    record_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns (session_id, created_at);

CREATE TABLE IF NOT EXISTS model_directory (
    user_id      TEXT PRIMARY KEY,
    model_id     TEXT NOT NULL,
    adapter      TEXT NOT NULL,
    adapter_path TEXT
);

CREATE TABLE IF NOT EXISTS raw_blobs (
    chunk_id     TEXT PRIMARY KEY,
    blob_ref     TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    device_id    TEXT,
    codec        TEXT,
    sha256       TEXT NOT NULL,
    bytes        INTEGER NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_raw_blobs_ref ON raw_blobs (blob_ref);

CREATE TABLE IF NOT EXISTS context_records (
    record_id        TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,
    t_start          TEXT NOT NULL,
    t_end            TEXT,
    chunk_id         TEXT,
    pipeline_version TEXT,
    -- D27: the old `ingest_time` split in two. created_at = first landing of the
    -- record_id, preserved across every re-POST (the old preserved-across-reprocess
    -- semantics, under its honest name); updated_at = the last time record_json
    -- actually CHANGED (put_context byte-compares; a no-op redelivery moves
    -- neither). Training-window membership and the day-log dedup axis: updated_at.
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    -- D17 civil-time context, promoted out of record_json so a civil-time query
    -- ("the user's local Tuesday 09:00-17:00") is a column read, not a JSON scan.
    -- t_start stays canonical UTC and the ONLY ordering/range axis; these are
    -- context beside the instant, never a replacement, and deliberately NOT
    -- indexed (no query orders by them). NULL = the device didn't report one.
    device_tz        TEXT,
    device_utc_offset_minutes INTEGER,
    record_json      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_context_user_tstart ON context_records (user_id, t_start);
-- D18's COMPLETENESS-axis index, on D27's axis. Training-window membership is by
-- updated_at, so the nightly `[last_trained_t, now-delta)` read must be a single
-- index-range scan. Event time (t_start) keeps its index above and stays the CONTENT
-- axis; the asymmetry (training windows are updated_at, retraction ranges are
-- event-time) is deliberate — see CHARTER § The time index.
CREATE INDEX IF NOT EXISTS idx_context_user_updated ON context_records (user_id, updated_at);

-- D18 / C12: the per-user profile. POLICY the SYSTEM reads to decide its own behaviour
-- for this user (scheduling, fallbacks) — never user-facing identity or presentation,
-- which belong to the input service. `home_tz` is DECLARED, NOT INFERRED: the only
-- writer is an explicit profile write, there is no auto-seed from a device-reported
-- device_tz, and there is NO server-side default timezone anywhere in the system (D17).
-- An absent row is a 404 and means the user is not schedulable — an operational alert,
-- never a silent skip. `profile_version` is a monotone counter bumped on EVERY write.
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id         TEXT PRIMARY KEY,
    home_tz         TEXT NOT NULL,
    profile_version INTEGER NOT NULL,
    updated_at      TEXT NOT NULL
);

-- D18: the training-window ledger. One row per window over the ingest-time watermark
-- range `[last_trained_t, now-delta)`. Bounds are IMMUTABLE once opened — continuum
-- replays its crash-safe journal by window_id, so a retry that re-minted an id would
-- force a full re-train, a second C5 entry and a second reservoir admission.
--   state   : 'open' | 'consolidated'
--   outcome : NULL while open; on close one of published | gate_failed | frozen |
--             skipped_no_data | crashed. `last_trained_t` advances IFF 'published'.
-- Note what is NOT here: a watermark column. It is derived (Store.last_trained_t) so
-- that no second row can ever disagree with the ledger about what has been trained.
-- KEYED (user_id, window_id), NOT window_id alone. The id is a PER-USER token — it is
-- derived from an instant, so a nightly fleet run that opens two users' windows in the
-- same second legitimately mints the same string twice, and a global key would turn a
-- normal night into a primary-key collision. It is also why every window-addressing
-- surface carries user_id (as the day-log fetch `?user_id=&window_id=` already does):
-- per-user isolation must fail closed, and an id-only close would be a cross-user write
-- to anyone who can guess a second.
CREATE TABLE IF NOT EXISTS training_windows (
    user_id    TEXT NOT NULL,
    window_id  TEXT NOT NULL,
    t_start    TEXT NOT NULL,
    t_end      TEXT NOT NULL,
    state      TEXT NOT NULL,
    outcome    TEXT,
    opened_at  TEXT NOT NULL,
    closed_at  TEXT,
    PRIMARY KEY (user_id, window_id)
);
-- At most ONE open window per user, enforced by the DB and not merely by the
-- read-then-write in open_training_window: get-or-create must never be able to mint a
-- second id for a user who already has one, because that is exactly the failure the
-- idempotent open exists to prevent.
CREATE UNIQUE INDEX IF NOT EXISTS uq_training_windows_open
    ON training_windows (user_id) WHERE state = 'open';

-- D18 / C10: the materialized day-log cache. A day-log is a DERIVED VIEW over C2 — the
-- record_json rows remain the source of truth and this table can be dropped and rebuilt —
-- but it is also A SECOND COPY OF USER CONTENT, which is why M5's deletion cascade must
-- reach it: a retraction that clears /context and leaves a day-log standing has deleted
-- nothing. Built on demand at fetch (no scheduler in this service) and keyed
-- (user_id, window_id), the same per-user addressing every window surface uses.
--   t_start/t_end   : the WINDOW's updated_at-axis bounds (D27), duplicated out of
--                     body_json so a change touching a materialized range can
--                     invalidate this row with one indexed DELETE on the write path.
--   daylog_format_version / recipe_id / home_tz : the three RENDER INPUTS this body was
--                     rendered under. A fetch whose current values differ re-materializes
--                     instead of serving an old dialect out of cache — home_tz included,
--                     because it fixes the anchor line's local date for every record with
--                     no device_tz, so a corrected profile zone must re-render the night.
CREATE TABLE IF NOT EXISTS day_logs (
    user_id               TEXT NOT NULL,
    window_id             TEXT NOT NULL,
    t_start               TEXT NOT NULL,
    t_end                 TEXT NOT NULL,
    daylog_format_version TEXT NOT NULL,
    recipe_id             TEXT NOT NULL,
    home_tz               TEXT NOT NULL,
    content_fingerprint   TEXT NOT NULL,
    materialized_at       TEXT NOT NULL,
    body_json             TEXT NOT NULL,
    PRIMARY KEY (user_id, window_id)
);
CREATE INDEX IF NOT EXISTS idx_day_logs_user_range ON day_logs (user_id, t_start, t_end);
"""


# (table, column, decl) — see Store._migrate. Additive only; never a rewrite.
# NOTE: only COLUMNS ON EXISTING TABLES belong here. New tables and new indexes are
# handled by _SCHEMA alone (CREATE TABLE/INDEX IF NOT EXISTS runs on a live DB); a new
# column is not, because CREATE TABLE IF NOT EXISTS is a no-op on an existing table.
_MIGRATIONS = [
    ("context_records", "device_tz", "TEXT"),
    ("context_records", "device_utc_offset_minutes", "INTEGER"),
]

# --- training-window vocabulary (D18) ------------------------------------------

WINDOW_STATE_OPEN = "open"
WINDOW_STATE_CONSOLIDATED = "consolidated"
WINDOW_STATES = (WINDOW_STATE_OPEN, WINDOW_STATE_CONSOLIDATED)

# The cycle outcomes a window can be closed with. Exactly ONE of them advances the
# watermark; `skipped_no_data` deliberately does NOT (refined 2026-07-27 — D18's first
# draft advanced on it), so a below-floor night simply accumulates material until a run
# is worth it and the next window is a strict SUPERSET of the one that produced nothing.
WINDOW_OUTCOMES = ("published", "gate_failed", "frozen", "skipped_no_data", "crashed")
WINDOW_ADVANCING_OUTCOME = "published"

# The watermark, written ONCE. Both readers (Store.last_trained_t and the get-or-create
# in Store.open_training_window, which needs it inside its own transaction) run this
# exact statement, so the definition of "trained up to here" cannot drift between them.
_WATERMARK_SQL = (
    "SELECT MAX(t_end) AS watermark FROM training_windows "
    "WHERE user_id = ? AND outcome = ?"
)

_DEFAULT_WINDOW_DELTA_SECONDS = 60


def db_path() -> str:
    return os.environ.get("STORAGE_DB_PATH", str(_DEFAULT_DB_PATH))


def raw_dir() -> str:
    """Directory holding the dev ``/raw`` blob bytes (env-overridable for tests/CI)."""
    return os.environ.get("STORAGE_RAW_DIR", str(_DEFAULT_RAW_DIR))


def window_delta_seconds() -> int:
    """The watermark lag ``delta`` in ``[last_trained_t, now-delta)``, default 60 s.

    It exists for exactly one reason: an in-flight write racing the boundary could
    otherwise be assigned an ``ingest_time`` below ``t_end`` yet commit *after*
    materialization read the range. It is watermark lag, not slack — so it is service
    config (``STORAGE_WINDOW_DELTA_SECONDS``), never a per-request knob a caller could
    shrink to zero.
    """
    raw = os.environ.get("STORAGE_WINDOW_DELTA_SECONDS", "")
    if not raw:
        return _DEFAULT_WINDOW_DELTA_SECONDS
    value = int(raw)
    if value < 0:
        raise ValueError("STORAGE_WINDOW_DELTA_SECONDS must be >= 0")
    return value


# Every timestamp storage mints. Second granularity, RFC3339 UTC, and — because it is
# fixed width with a zero-padded numeric field per component — lexicographic order ==
# chronological order, which is what lets every range clause be a plain string compare.
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime(_TS_FMT)


class WindowRefused(Exception):
    """The ledger refuses to OPEN a window; the route maps this to 409.

    Two reasons, both "the user's current state forbids it" rather than "the request
    was malformed": the user has no ingest history at all (nothing to train, so there
    is no range to name), or the computed end is not strictly greater than every prior
    window end for that user (which is what makes a second-granularity id collision
    impossible by construction).
    """

    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail.get("error", "window refused")))
        self.detail = detail


class WindowOutcomeConflict(Exception):
    """A close arrived for an already-consolidated window with a DIFFERENT outcome.

    Re-closing with the SAME outcome is a retry and succeeds unchanged; re-closing with
    a different one would rewrite history (and, if the new outcome were ``published``,
    silently advance a watermark that a failed night deliberately left alone). 409.
    """

    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail.get("error", "window outcome conflict")))
        self.detail = detail


def _window_body(row: sqlite3.Row) -> dict[str, Any]:
    """The ledger row as served. Storage-minted, so every field is ours."""
    return {
        "window_id": row["window_id"],
        "user_id": row["user_id"],
        "t_start": row["t_start"],
        "t_end": row["t_end"],
        "state": row["state"],
        "outcome": row["outcome"],
        "opened_at": row["opened_at"],
        "closed_at": row["closed_at"],
    }


class Store:
    def __init__(self, path: Optional[str] = None, raw_root: Optional[str] = None) -> None:
        self.path = path or db_path()
        parent = Path(self.path).parent
        if str(parent):
            parent.mkdir(parents=True, exist_ok=True)
        self.raw_root = Path(raw_root or raw_dir())
        self.raw_root.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            # The D27 rename runs BEFORE _SCHEMA: _SCHEMA's updated_at index cannot be
            # created while the column does not exist on a pre-E table, and an index is
            # the one thing the additive column ladder cannot deliver late.
            self._migrate_context_v2(conn)
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate_context_v2(conn: sqlite3.Connection) -> None:
        """The D27 split, for DBs created before it (no-op on fresh and migrated DBs).

        ``ingest_time`` is RENAMED to ``created_at``: the stored data is already
        correct under the new name — first landing, preserved across reprocess — so a
        rename keeps history verbatim. ``updated_at`` is added backfilled equal to it:
        the landing is the last change the old world could know about. (The added
        column is nullable on a migrated DB — ``ALTER ... ADD`` cannot carry NOT NULL
        without a constant default; ``put_context`` always writes it.) The old ingest
        index goes — no query orders by ``created_at`` — and ``_SCHEMA`` then builds
        the ``updated_at`` one. The OD-2 cutover wipes ``/context`` anyway; this keeps
        every pre-E DB file openable without a special case.

        RE-ENTRANT UNDER kill-9 (review round): python-sqlite3 autocommits DDL, so the
        ladder cannot ride one transaction — instead EVERY step is independently
        conditional or idempotent, and a crash at any point completes on the next
        boot. The NULL backfill runs whenever the column exists: a NULL ``updated_at``
        can only be a mid-ladder shape (``put_context`` always writes the stamp), so
        healing it unconditionally is safe and is what keeps a crash from stranding
        rows off the window axis.
        """
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(context_records)")}
        if not cols:
            return  # fresh DB: _SCHEMA creates the v2 shape directly
        if "ingest_time" in cols and "created_at" not in cols:
            conn.execute(
                "ALTER TABLE context_records RENAME COLUMN ingest_time TO created_at")
            cols = (cols - {"ingest_time"}) | {"created_at"}
        if "created_at" in cols and "updated_at" not in cols:
            conn.execute("ALTER TABLE context_records ADD COLUMN updated_at TEXT")
            cols.add("updated_at")
        if "updated_at" in cols:
            conn.execute(
                "UPDATE context_records SET updated_at = created_at "
                "WHERE updated_at IS NULL")
        conn.execute("DROP INDEX IF EXISTS idx_context_user_ingest")
        conn.commit()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Additive column migrations for DBs created before a column existed.

        `CREATE TABLE IF NOT EXISTS` is a no-op on an existing table, so a live
        DB (node-7) would never gain a new column without this. SQLite has no
        ADD COLUMN IF NOT EXISTS — probe pragma table_info and add what's missing.

        No backfill, deliberately: a record landed before the clients reported a
        zone genuinely HAS no zone, and inventing one (the server's, say) is
        precisely the silently-wrong-timezone failure D17 exists to eliminate.
        NULL is the honest value and reads downstream as "fall back to home_tz".
        """
        for table, column, decl in _MIGRATIONS:
            cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        conn.commit()

    def seed_base(self) -> None:
        """Idempotently seed the single base model-directory entry."""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO model_directory (user_id, model_id, adapter, adapter_path) "
                "VALUES (?, ?, ?, ?)",
                (_BASE_KEY, BASE_MODEL_ID, BASE_ADAPTER, BASE_ADAPTER_PATH),
            )
            conn.commit()

    # --- /sessions turns (C4) ---------------------------------------------------

    def put_turn(self, record: dict[str, Any]) -> str:
        """Persist a C4 turn record. Idempotent on turn_id (upsert). Returns turn_id."""
        turn_id = record["turn_id"]
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO turns "
                "(turn_id, session_id, user_id, created_at, completed_at, record_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    turn_id,
                    record["session_id"],
                    record["user_id"],
                    record["created_at"],
                    record.get("completed_at"),
                    json.dumps(record, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            conn.commit()
        return turn_id

    def get_turn(self, turn_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT record_json FROM turns WHERE turn_id = ?", (turn_id,)
            ).fetchone()
        return json.loads(row["record_json"]) if row else None

    def list_turns(self, session_id: str) -> list[dict[str, Any]]:
        """C4 turns for a session, ordered by created_at (rowid breaks ties stably)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT record_json FROM turns WHERE session_id = ? "
                "ORDER BY created_at ASC, rowid ASC",
                (session_id,),
            ).fetchall()
        return [json.loads(r["record_json"]) for r in rows]

    # --- model directory (C6) ---------------------------------------------------

    def resolve(self, user_id: str) -> dict[str, Any]:
        """Resolve the active model entry for a user. v0: per-user override if present,
        else the seeded base entry. Always returns a valid C6 body."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT model_id, adapter, adapter_path FROM model_directory "
                "WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT model_id, adapter, adapter_path FROM model_directory "
                    "WHERE user_id = ?",
                    (_BASE_KEY,),
                ).fetchone()
        if row is None:
            # Defensive fallback if seed_base somehow never ran.
            return {
                "model_id": BASE_MODEL_ID,
                "adapter": BASE_ADAPTER,
                "adapter_path": BASE_ADAPTER_PATH,
            }
        return {
            "model_id": row["model_id"],
            "adapter": row["adapter"],
            "adapter_path": row["adapter_path"],
        }

    # --- /raw blob leg (C1) -----------------------------------------------------

    @staticmethod
    def _mint_blob_ref(user_id: str, chunk_id: str) -> str:
        """Mint the opaque, storage-owned blob ref for a chunk.

        Deterministic on ``(user_id, chunk_id)`` — so re-PUTting the same chunk yields the
        same ref (idempotency) — and hex-sharded so the ref doubles as a safe relative path
        under the raw store (no user-supplied bytes ever touch the filesystem path, so no
        traversal). Callers treat it as opaque; only storage resolves it. Contains '/'.
        """
        digest = hashlib.sha256(f"{user_id}\x00{chunk_id}".encode("utf-8")).hexdigest()
        return f"{digest[:2]}/{digest[2:4]}/{digest}"

    def _blob_path(self, blob_ref: str) -> Optional[Path]:
        """Resolve a blob_ref to its on-disk path, fail-closed on any escape from raw_root."""
        base = self.raw_root.resolve()
        candidate = (self.raw_root / blob_ref).resolve()
        if candidate != base and base not in candidate.parents:
            return None
        return candidate

    def put_blob(
        self,
        *,
        chunk_id: str,
        user_id: str,
        device_id: Optional[str],
        codec: Optional[str],
        sha256: str,
        data: bytes,
    ) -> dict[str, Any]:
        """Land raw chunk bytes in /raw. Idempotent on chunk_id (re-PUT -> same ref, no dup
        blob). ``sha256``/``len(data)`` are the already-verified integrity values. Returns
        ``{blob_ref, bytes, sha256}`` (the stored values, canonical across retries)."""
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT blob_ref, bytes, sha256 FROM raw_blobs WHERE chunk_id = ?",
                (chunk_id,),
            ).fetchone()
            if existing is not None:
                return {
                    "blob_ref": existing["blob_ref"],
                    "bytes": existing["bytes"],
                    "sha256": existing["sha256"],
                }
            blob_ref = self._mint_blob_ref(user_id, chunk_id)
            path = self._blob_path(blob_ref)
            assert path is not None  # minted refs are pure hex — never escape raw_root
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                # Write to a temp sibling then rename, so a reader never sees a partial blob.
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_bytes(data)
                tmp.replace(path)
            conn.execute(
                "INSERT OR IGNORE INTO raw_blobs "
                "(chunk_id, blob_ref, user_id, device_id, codec, sha256, bytes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (chunk_id, blob_ref, user_id, device_id, codec, sha256, len(data), _utc_now()),
            )
            conn.commit()
            # Re-read so a concurrent writer that won the INSERT gives us the canonical row.
            row = conn.execute(
                "SELECT blob_ref, bytes, sha256 FROM raw_blobs WHERE chunk_id = ?",
                (chunk_id,),
            ).fetchone()
        return {"blob_ref": row["blob_ref"], "bytes": row["bytes"], "sha256": row["sha256"]}

    def get_blob(self, blob_ref: str) -> Optional[bytes]:
        """Return the bytes for a blob_ref, or None if the ref is unknown OR the blob has
        since been deleted (delete-last-N / right-to-be-forgotten) — consumers tolerate both."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM raw_blobs WHERE blob_ref = ? LIMIT 1", (blob_ref,)
            ).fetchone()
        if row is None:
            return None
        path = self._blob_path(blob_ref)
        if path is None or not path.exists():
            return None
        return path.read_bytes()

    # --- /context store (C2) ----------------------------------------------------

    def put_context(self, record: dict[str, Any]) -> str:
        """Persist a C2 processed record. Idempotent upsert on record_id; blind replace.
        Returns record_id (the reply contract is ``{ok, record_id}`` at the surface).

        D27 (§5.1): ``created_at`` is set on the FIRST landing of a ``record_id`` and
        preserved forever after (it inherits the old ingest_time's semantics);
        ``updated_at`` bumps ONLY when the incoming ``record_json`` byte-compares
        different from the stored row. A byte-identical re-POST — DP's post-POST crash
        replay and the still-holey heal — answers with the row COMPLETELY untouched:
        same ``updated_at``, same ``created_at``, same rowid. The compare runs over
        storage's canonical serialization of the posted body, which DP's byte-identical
        wire bytes reproduce exactly (T-1's sorted assembly). Mechanically the compare
        IS the upsert: ``ON CONFLICT DO UPDATE ... WHERE record_json <> excluded`` is
        one atomic statement, and a filtered-out update touches nothing.

        BLIND REPLACE, never fuller-wins (founder ruling R3): on any byte difference
        the row is replaced unconditionally — a heal during another server's outage can
        legitimately REGRESS a previously-green slot; DP's ledger carries hole truth,
        and convergence, not monotonicity, is the guarantee. The upsert style stays
        ``ON CONFLICT DO UPDATE`` (rowid-stable), never ``INSERT OR REPLACE``: the
        day-log dedup tiebreak is rowid, and REPLACE churns rowids.

        ``updated_at`` is second-granularity (``_TS_FMT`` is the one spelling of every
        storage-minted stamp), so consecutive heals inside one second are real ties —
        which makes the rowid tiebreak in the day-log dedup LOAD-BEARING, not
        decorative.

        Also INVALIDATES the materialized day-log caches a change touches — BOTH
        windows of an ``updated_at`` move (founder ruling R2): the window holding the
        row's previous stamp, and the one receiving the new stamp. The re-materialized
        old window then EXCLUDES the moved record — it dissolves out, exactly the D18
        watermark philosophy (dissolve late data rather than handle it); trained bodies
        are history, not edited, and the recipe/format stamps plus continuum's
        stamp-refusal remain the integrity net. A byte-identical re-POST invalidates
        nothing: an unchanged record cannot go stale.
        """
        record_id = record["record_id"]
        source = record.get("source") or {}
        record_json = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        now = _utc_now()
        with self._connect() as conn:
            prior = conn.execute(
                "SELECT updated_at FROM context_records WHERE record_id = ?",
                (record_id,),
            ).fetchone()
            before = conn.total_changes
            conn.execute(
                "INSERT INTO context_records "
                "(record_id, user_id, t_start, t_end, chunk_id, pipeline_version, "
                " created_at, updated_at, device_tz, device_utc_offset_minutes, "
                " record_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(record_id) DO UPDATE SET "
                "  user_id=excluded.user_id, "
                "  t_start=excluded.t_start, "
                "  t_end=excluded.t_end, "
                "  chunk_id=excluded.chunk_id, "
                "  pipeline_version=excluded.pipeline_version, "
                "  updated_at=excluded.updated_at, "
                "  device_tz=excluded.device_tz, "
                "  device_utc_offset_minutes=excluded.device_utc_offset_minutes, "
                "  record_json=excluded.record_json "
                "WHERE context_records.record_json <> excluded.record_json",
                (
                    record_id,
                    record["user_id"],
                    record["t_start"],
                    record.get("t_end"),
                    source.get("chunk_id"),
                    record.get("pipeline_version"),
                    now,   # created_at — first landing only; never in the update set
                    now,   # updated_at — applied only when the bytes differ
                    # Promoted from source{} for civil-time querying. record_json
                    # remains the verbatim C2 — these columns are a projection of
                    # it, never an edit to it.
                    source.get("device_tz"),
                    source.get("device_utc_offset_minutes"),
                    record_json,
                ),
            )
            changed = conn.total_changes > before
            if changed:
                stamps = {now}
                if prior is not None and prior["updated_at"]:
                    stamps.add(prior["updated_at"])
                for stamp in sorted(stamps):
                    conn.execute(
                        "DELETE FROM day_logs "
                        "WHERE user_id = ? AND t_start <= ? AND t_end > ?",
                        (record["user_id"], stamp, stamp),
                    )
            conn.commit()
        return record_id

    def retract_context(
        self,
        user_id: str,
        *,
        record_id: Optional[str] = None,
        chunk_id: Optional[str] = None,
        pipeline_version: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """E-2: whole-record retraction from ``/context``, with the day-log cascade
        (D28; charter M5).

        Selectors AND together over the mandatory ``user_id``; at least one is required
        (a selectorless call would be a full-user wipe by typo — that is M5's OTHER
        primitive, deliberately not this one). Whole RECORDS, never kinds or slots: the
        kind-granular design retired unbuilt (D28). Returns the auditable manifest —
        counts by ``pipeline_version``, the selector echoed, and how many cached
        day-logs the cascade invalidated; ``dry_run`` returns the IDENTICAL manifest —
        ``day_logs_invalidated`` included, predicted rather than performed — and
        touches nothing. Retracting nothing is an honest zero manifest, never an error.

        THE CASCADE: every cached day-log whose window contains an affected record's
        ``updated_at`` is INVALIDATED — dropped and rebuilt on next fetch, the same
        mechanism a corrected ``home_tz`` uses — because a retraction that clears
        ``/context`` and leaves a materialized day-log standing has deleted nothing.

        THE LEDGER BOUNDARY, plainly: this never touches DP's done-ledger, so a
        retracted chunk's redelivery still SKIPS there — DP answers 200 with a
        record_id this store no longer holds, and no write arrives here. That is the
        designed posture, not a bug: E-2 is a retention / right-to-be-forgotten
        primitive, never a correctness mechanism ("deletion is never the mechanism for
        correctness" stands). Rebuild-after-retraction is the OD-2 ``/raw`` replay tool
        (future) or a version bump. ``/raw`` bytes are untouched too — bytes are sacred
        (OD-2), with their own M5 primitives.
        """
        if record_id is None and chunk_id is None and pipeline_version is None:
            raise ValueError("retraction requires at least one selector")
        clauses = ["user_id = ?"]
        params: list[Any] = [user_id]
        selector: dict[str, str] = {}
        if record_id is not None:
            clauses.append("record_id = ?")
            params.append(record_id)
            selector["record_id"] = record_id
        if chunk_id is not None:
            clauses.append("chunk_id = ?")
            params.append(chunk_id)
            selector["chunk_id"] = chunk_id
        if pipeline_version is not None:
            clauses.append("pipeline_version = ?")
            params.append(pipeline_version)
            selector["pipeline_version"] = pipeline_version
        where = " AND ".join(clauses)
        with self._connect() as conn:
            # One transaction from the manifest read to the last delete (review
            # round): a write racing the retraction must not let the manifest
            # describe rows the delete did not take, or vice versa.
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                f"SELECT pipeline_version, updated_at FROM context_records "
                f"WHERE {where}", params).fetchall()
            by_pv: dict[str, int] = {}
            for row in rows:
                pv = row["pipeline_version"] or ""
                by_pv[pv] = by_pv.get(pv, 0) + 1
            invalidated = 0
            stamps = sorted({row["updated_at"] for row in rows if row["updated_at"]})
            if rows and dry_run:
                # IDENTICAL manifest means identical: the dry run PREDICTS the
                # cascade's blast radius (a distinct-row count over the same window
                # ranges the wet path deletes) instead of reporting the zero it did
                # not perform.
                ranges = " OR ".join("(t_start <= ? AND t_end > ?)" for _ in stamps)
                range_params: list[Any] = [user_id]
                for stamp in stamps:
                    range_params += [stamp, stamp]
                invalidated = conn.execute(
                    f"SELECT COUNT(*) AS n FROM day_logs "
                    f"WHERE user_id = ? AND ({ranges})", range_params,
                ).fetchone()["n"] if stamps else 0
            elif rows:
                conn.execute(f"DELETE FROM context_records WHERE {where}", params)
                before = conn.total_changes
                for stamp in stamps:
                    conn.execute(
                        "DELETE FROM day_logs "
                        "WHERE user_id = ? AND t_start <= ? AND t_end > ?",
                        (user_id, stamp, stamp))
                invalidated = conn.total_changes - before
            conn.commit()
        return {
            "user_id": user_id,
            "dry_run": dry_run,
            "selector": selector,
            "records": len(rows),
            "by_pipeline_version": by_pv,
            "day_logs_invalidated": invalidated,
        }

    def get_context(self, record_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT record_json FROM context_records WHERE record_id = ?", (record_id,)
            ).fetchone()
        return json.loads(row["record_json"]) if row else None

    def list_context(
        self,
        user_id: str,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """C2 records for one user on the wall-clock time spine, ordered by t_start.

        The window is half-open ``[from_ts, to_ts)`` (from inclusive, to exclusive — the same
        convention as continuum's C10 ``[last_trained_t, now)`` window, so adjacent windows
        never double-count). Either bound may be omitted for an open end. RFC3339 UTC strings
        sort lexicographically, so the bounds are plain string comparisons; per-user isolation
        is enforced by the mandatory ``user_id`` filter (another user's rows never leak in).
        """
        clauses = ["user_id = ?"]
        params: list[Any] = [user_id]
        if from_ts is not None:
            clauses.append("t_start >= ?")
            params.append(from_ts)
        if to_ts is not None:
            clauses.append("t_start < ?")
            params.append(to_ts)
        sql = (
            "SELECT record_json FROM context_records WHERE "
            + " AND ".join(clauses)
            + " ORDER BY t_start ASC, rowid ASC"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [json.loads(r["record_json"]) for r in rows]

    def list_context_by_updated(
        self, user_id: str, from_ts: str, to_ts: str
    ) -> list[dict[str, Any]]:
        """C2 records for one user on the UPDATED_AT axis — training-window membership
        (D27 moved the axis off the old ``ingest_time``; the shape of the read did not
        change).

        This is a different read from ``list_context``, not a parameterization of it, and
        the asymmetry is deliberate rather than an oversight to be tidied away: training
        windows are on the storage-clock axis (the completeness axis — the only one on
        which "everything I had" is a guarantee) while retraction ranges are EVENT-time
        (a deletion is about a lived period). Overloading one endpoint with an axis flag
        would make it possible to ask the wrong question by typo.

        Half-open ``[from_ts, to_ts)`` on ``updated_at``, served by
        ``idx_context_user_updated``. Rows come back in EVENT-time order
        (``t_start ASC, rowid ASC``) because that is the order the renderer needs — a
        block's caption/heard/world-text lines read chronologically.

        Each row is ``{"seq", "updated_at", "record"}``. ``seq`` is the sqlite rowid and
        rides along because "latest updated_at wins" needs a tiebreak: ``updated_at`` is
        second-granularity, so a whole data-processing flush — or two consecutive heals —
        shares one value. The tiebreak is LOAD-BEARING (see ``put_context``).
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT rowid AS seq, updated_at, record_json FROM context_records "
                "WHERE user_id = ? AND updated_at >= ? AND updated_at < ? "
                "ORDER BY t_start ASC, rowid ASC",
                (user_id, from_ts, to_ts),
            ).fetchall()
        return [
            {
                "seq": r["seq"],
                "updated_at": r["updated_at"],
                "record": json.loads(r["record_json"]),
            }
            for r in rows
        ]

    # --- materialized day-logs (C10) --------------------------------------------

    def get_daylog(self, user_id: str, window_id: str) -> Optional[dict[str, Any]]:
        """The cached day-log row, or None. The CALLER decides whether it is still
        current — it gets the whole row back, and in particular all three RENDER INPUTS
        (``daylog_format_version``, ``recipe_id``, ``home_tz``), precisely so a format
        bump, a recipe re-pin or a corrected profile zone re-materializes instead of being
        served stale. ``home_tz`` is one of them because it decides the anchor line's local
        date for every record that carries no ``device_tz``."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM day_logs WHERE user_id = ? AND window_id = ?",
                (user_id, window_id),
            ).fetchone()
        return dict(row) if row else None

    def put_daylog(self, body: dict[str, Any]) -> None:
        """Cache a materialized C10 body. Idempotent on ``(user_id, window_id)``; a
        re-materialization REPLACES, because the day-log is derived and the newest render
        of the same window is by definition the right one."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO day_logs "
                "(user_id, window_id, t_start, t_end, daylog_format_version, recipe_id, "
                " home_tz, content_fingerprint, materialized_at, body_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, window_id) DO UPDATE SET "
                "  t_start=excluded.t_start, "
                "  t_end=excluded.t_end, "
                "  daylog_format_version=excluded.daylog_format_version, "
                "  recipe_id=excluded.recipe_id, "
                "  home_tz=excluded.home_tz, "
                "  content_fingerprint=excluded.content_fingerprint, "
                "  materialized_at=excluded.materialized_at, "
                "  body_json=excluded.body_json",
                (
                    body["user_id"],
                    body["window_id"],
                    body["t_start"],
                    body["t_end"],
                    body["daylog_format_version"],
                    body["recipe_id"],
                    body["home_tz"],
                    body["content_fingerprint"],
                    _utc_now(),
                    json.dumps(body, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            conn.commit()

    # --- per-user profile (C12) -------------------------------------------------

    def get_profile(self, user_id: str) -> Optional[dict[str, Any]]:
        """The user's C12 profile body, or None if there is no row.

        None means 404 at the surface. It does NOT mean "use a default" — there is no
        server-side default timezone anywhere in the system (D17), so a user with no
        profile is simply not schedulable, which is an operational alert rather than a
        silent skip.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id, home_tz, profile_version, updated_at "
                "FROM user_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "contract": "C12",
            "version": "0",
            "user_id": row["user_id"],
            "home_tz": row["home_tz"],
            "profile_version": row["profile_version"],
            "updated_at": row["updated_at"],
        }

    def put_profile(self, user_id: str, home_tz: str) -> dict[str, Any]:
        """Upsert the user's profile. Returns the stored C12 body.

        ``profile_version`` is bumped on EVERY write, including a write that sets the
        same ``home_tz`` — the contract calls it a monotone counter bumped on every
        write, and a reader that cached a fire time needs to see that policy was
        touched, not merely that it changed value.

        This is the ONLY writer of ``home_tz``. Nothing in storage calls it on its own
        initiative: ``put_context`` records a device-reported ``device_tz`` as a per-
        record FACT and never promotes it to per-user POLICY (corrected 2026-07-27).
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_profiles (user_id, home_tz, profile_version, updated_at) "
                "VALUES (?, ?, 1, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "  home_tz=excluded.home_tz, "
                "  profile_version=user_profiles.profile_version + 1, "
                "  updated_at=excluded.updated_at",
                (user_id, home_tz, _utc_now()),
            )
            conn.commit()
        body = self.get_profile(user_id)
        assert body is not None  # we just wrote it
        return body

    # --- training-window ledger (D18, C10 evolved) ------------------------------

    def last_trained_t(self, user_id: str) -> Optional[str]:
        """The user's watermark: the end of their latest PUBLISHED window, or None.

        DERIVED, not stored. That is the whole design: "``last_trained_t`` advances if
        and only if the cycle publishes" is then true by construction — there is no
        counter a close path could forget to leave alone, and no second row that could
        disagree with the ledger. Gate failure, freeze, crash, no data and too-little
        data all leave this exactly where it was, so the next window is a strict
        SUPERSET of the failed one (the design-of-record's failed-day merge, obtained
        structurally rather than by bookkeeping).
        """
        with self._connect() as conn:
            row = conn.execute(
                _WATERMARK_SQL, (user_id, WINDOW_ADVANCING_OUTCOME)
            ).fetchone()
        return row["watermark"] if row else None

    def earliest_updated_at(self, user_id: str) -> Optional[str]:
        """The user's earliest ``updated_at`` — the floor of a never-trained user's
        first window (the window axis, D27). None if they have no records at all."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MIN(updated_at) AS first_updated FROM context_records "
                "WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row["first_updated"] if row else None

    def get_window(self, user_id: str, window_id: str) -> Optional[dict[str, Any]]:
        """Random-access by ``(user_id, window_id)`` — the same addressing the day-log
        fetch uses, and the reason both halves are always carried together: the id is a
        per-user token, so it does not name a row on its own."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM training_windows WHERE user_id = ? AND window_id = ?",
                (user_id, window_id),
            ).fetchone()
        return _window_body(row) if row else None

    def get_open_window(self, user_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM training_windows WHERE user_id = ? AND state = ?",
                (user_id, WINDOW_STATE_OPEN),
            ).fetchone()
        return _window_body(row) if row else None

    def list_windows(
        self, user_id: str, state: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """The user's windows, oldest first.

        Ordered by ``window_id`` because that IS the chronological order — the id is
        fixed-width and derived from the window's end instant, and window ends are
        strictly increasing per user (enforced in ``open_training_window``). Consumers
        rely on `<` / `>=` over these ids and on nothing else.
        """
        clauses = ["user_id = ?"]
        params: list[Any] = [user_id]
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        sql = (
            "SELECT * FROM training_windows WHERE "
            + " AND ".join(clauses)
            + " ORDER BY window_id ASC, rowid ASC"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_window_body(r) for r in rows]

    def open_training_window(
        self,
        user_id: str,
        *,
        now: Optional[datetime] = None,
        delta_seconds: Optional[int] = None,
    ) -> dict[str, Any]:
        """Idempotent get-or-create of the user's currently-open training window.

        If a window is already open it is returned **unchanged** — same id, same
        bounds, no new row. This is not a convenience: continuum's crash-safe journal
        is keyed by ``window_id``, so a retry that recomputed ``now`` would mint a
        fresh id, a fresh journal, and therefore a full re-train, a second C5 entry and
        a second reservoir admission. Bounds are immutable once opened.

        A new window runs ``[t_start, t_end)`` where ``t_start`` is the user's
        ``last_trained_t`` — or, for a never-trained user, their earliest
        ``updated_at`` — and ``t_end`` is ``now - delta`` truncated to the second (so
        it agrees exactly with the minted id).

        Refuses (``WindowRefused``) when the user has no ingest history at all, and
        when ``t_end`` is not strictly greater than every prior window end for this
        user. The doc states that guard against ``last_trained_t``; this is its
        strictly stronger form, and the strengthening is required rather than
        defensive: a *gate-failed* window's id occupies the id namespace too, so
        guarding only against the published watermark would let an immediate re-drive
        mint a colliding id within the same second.
        An injected ``now`` MUST be timezone-aware. ``mint_window_id`` states that rule and
        refuses a naive datetime — but it is the LAST thing this method calls, and by then
        ``.astimezone()`` has already read the naive value as the SERVER'S LOCAL ZONE and
        handed the minter a perfectly aware (and, off UTC, wrong) instant, so the minter's
        guard could never fire. Rejecting at the entry point is what makes the stated rule
        true. It matters because ``t_end`` and the id minted from it move together: a host
        running in Asia/Tokyo would otherwise mint a window nine hours early, and every
        downstream string comparison over that id is then wrong by nine hours.
        """
        if now is not None and (
            now.tzinfo is None or now.tzinfo.utcoffset(now) is None
        ):
            raise ValueError(
                "open_training_window requires a timezone-aware `now`; got a naive one"
            )
        now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        delta = window_delta_seconds() if delta_seconds is None else delta_seconds
        with self._connect() as conn:
            open_row = conn.execute(
                "SELECT * FROM training_windows WHERE user_id = ? AND state = ?",
                (user_id, WINDOW_STATE_OPEN),
            ).fetchone()
            if open_row is not None:
                return _window_body(open_row)

            watermark = conn.execute(
                _WATERMARK_SQL, (user_id, WINDOW_ADVANCING_OUTCOME)
            ).fetchone()["watermark"]
            t_start = watermark
            if t_start is None:
                t_start = conn.execute(
                    "SELECT MIN(updated_at) AS first_updated FROM context_records "
                    "WHERE user_id = ?",
                    (user_id,),
                ).fetchone()["first_updated"]
            if t_start is None:
                raise WindowRefused(
                    {
                        "error": "no ingest history",
                        "user_id": user_id,
                        "reason": "user has never been trained and has no /context "
                                  "records, so there is no window start to name",
                    }
                )

            end_dt = (now_utc - timedelta(seconds=delta)).replace(microsecond=0)
            t_end = end_dt.strftime(_TS_FMT)
            prior_end = conn.execute(
                "SELECT MAX(t_end) AS prior_end FROM training_windows WHERE user_id = ?",
                (user_id,),
            ).fetchone()["prior_end"]
            floor_ts = max(t for t in (t_start, watermark, prior_end) if t is not None)
            if t_end <= floor_ts:
                raise WindowRefused(
                    {
                        "error": "window end is not strictly greater than the floor",
                        "user_id": user_id,
                        "t_end": t_end,
                        "floor": floor_ts,
                        "last_trained_t": watermark,
                        "prior_window_end": prior_end,
                        "reason": "a window must advance past every prior window end; "
                                  "this is what makes an id collision impossible",
                    }
                )

            window_id = mint_window_id(end_dt)
            opened_at = _utc_now()
            conn.execute(
                "INSERT INTO training_windows "
                "(user_id, window_id, t_start, t_end, state, outcome, opened_at, closed_at) "
                "VALUES (?, ?, ?, ?, ?, NULL, ?, NULL)",
                (user_id, window_id, t_start, t_end, WINDOW_STATE_OPEN, opened_at),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM training_windows WHERE user_id = ? AND window_id = ?",
                (user_id, window_id),
            ).fetchone()
            return _window_body(row)

    def close_training_window(
        self, user_id: str, window_id: str, outcome: str
    ) -> Optional[dict[str, Any]]:
        """Close a window with a cycle outcome. Returns the row, or None if this user
        has no such window (which is also what a cross-user attempt gets — isolation
        fails closed rather than reporting that someone else's window exists).

        The watermark moves — or doesn't — as a CONSEQUENCE of the recorded outcome,
        because ``last_trained_t`` is derived from ``outcome = 'published'`` rows. There
        is deliberately no "advance the watermark" statement here to get wrong.

        Re-closing with the same outcome is a retry and returns the row unchanged;
        re-closing with a different one raises ``WindowOutcomeConflict`` (409) rather
        than rewriting history.
        """
        if outcome not in WINDOW_OUTCOMES:
            raise ValueError(f"unknown window outcome {outcome!r}")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM training_windows WHERE user_id = ? AND window_id = ?",
                (user_id, window_id),
            ).fetchone()
            if row is None:
                return None
            if row["state"] == WINDOW_STATE_CONSOLIDATED:
                if row["outcome"] == outcome:
                    return _window_body(row)  # idempotent retry
                raise WindowOutcomeConflict(
                    {
                        "error": "window already consolidated with a different outcome",
                        "user_id": user_id,
                        "window_id": window_id,
                        "recorded_outcome": row["outcome"],
                        "requested_outcome": outcome,
                    }
                )
            conn.execute(
                "UPDATE training_windows SET state = ?, outcome = ?, closed_at = ? "
                "WHERE user_id = ? AND window_id = ?",
                (WINDOW_STATE_CONSOLIDATED, outcome, _utc_now(), user_id, window_id),
            )
            conn.commit()
            closed = conn.execute(
                "SELECT * FROM training_windows WHERE user_id = ? AND window_id = ?",
                (user_id, window_id),
            ).fetchone()
        return _window_body(closed)
