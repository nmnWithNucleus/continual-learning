"""SQLite persistence for the serve-loop MVP.

Two tables:
  - ``turns``: one row per C4 turn record. ``turn_id`` is the primary key; ``session_id``
    is indexed (with ``created_at``) so a session's turns list is a single index scan,
    ordered by ``created_at``. The full C4 is stored verbatim as JSON for exact round-trip.
  - ``model_directory``: the trivial C6 directory. A seeded ``_base_`` row is the default;
    a per-user row (when continuum ships adapters) would override it. v0 only ever seeds base.

Dev-grade: a fresh connection per operation (low volume, handful of pilot users). The DB
file path comes from ``STORAGE_DB_PATH`` (default: ``app/dev.db`` beside this module).
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

_DEFAULT_DB_PATH = Path(__file__).resolve().parent / "dev.db"

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
"""


def db_path() -> str:
    return os.environ.get("STORAGE_DB_PATH", str(_DEFAULT_DB_PATH))


class Store:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or db_path()
        parent = Path(self.path).parent
        if str(parent):
            parent.mkdir(parents=True, exist_ok=True)
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
