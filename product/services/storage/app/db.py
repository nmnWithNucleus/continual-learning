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
  - ``context_records``: the ``/context`` store (C2). One row per processed record, keyed by
    ``record_id`` (deterministic on ``(chunk_id, pipeline_version)`` upstream, so a reprocess
    is an idempotent upsert here). Indexed on ``(user_id, t_start)`` — the wall-clock time
    spine every reader leans on. Full C2 stored verbatim as JSON; ``ingest_time`` is the
    storage-assigned audit axis (distinct from C2's ``processed_at``), preserved across
    reprocess upserts.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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
    ingest_time      TEXT NOT NULL,
    record_json      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_context_user_tstart ON context_records (user_id, t_start);
"""


def db_path() -> str:
    return os.environ.get("STORAGE_DB_PATH", str(_DEFAULT_DB_PATH))


def raw_dir() -> str:
    """Directory holding the dev ``/raw`` blob bytes (env-overridable for tests/CI)."""
    return os.environ.get("STORAGE_RAW_DIR", str(_DEFAULT_RAW_DIR))


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
            conn.executescript(_SCHEMA)

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
        """Persist a C2 processed record. Idempotent upsert on record_id (a reprocess under
        the same pipeline_version overwrites in place — no dup row). ``ingest_time`` (the
        storage-assigned audit axis) is set on first landing and preserved across reprocess.
        Returns record_id."""
        record_id = record["record_id"]
        source = record.get("source") or {}
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO context_records "
                "(record_id, user_id, t_start, t_end, chunk_id, pipeline_version, "
                " ingest_time, record_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(record_id) DO UPDATE SET "
                "  user_id=excluded.user_id, "
                "  t_start=excluded.t_start, "
                "  t_end=excluded.t_end, "
                "  chunk_id=excluded.chunk_id, "
                "  pipeline_version=excluded.pipeline_version, "
                "  record_json=excluded.record_json",
                (
                    record_id,
                    record["user_id"],
                    record["t_start"],
                    record.get("t_end"),
                    source.get("chunk_id"),
                    record.get("pipeline_version"),
                    _utc_now(),
                    json.dumps(record, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            conn.commit()
        return record_id

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
