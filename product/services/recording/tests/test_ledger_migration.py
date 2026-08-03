"""Migrations on a pre-existing ledger.db, proven against a genuinely OLD schema:

  * the 2026-07-29 nomenclature renames — table `sessions` -> `captures`, columns
    `session_id` -> `capture_id` and `seq` -> `segment_num` — applied in place, with
    rows intact;
  * the additive `dp_state` column + its one-shot backfill, so an already-confirmed
    (dp_acked=1) chunk reads 'processed' rather than NULL.
"""
from __future__ import annotations

import sqlite3

from app.ledger import Ledger

# A pre-rename, pre-dp_state ledger — the M1-era names, verbatim: table `sessions`,
# columns `session_id`/`seq`, and no dp_state on chunks.
_OLD_SCHEMA = """
CREATE TABLE sessions (
  session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, device_id TEXT NOT NULL,
  started_at TEXT NOT NULL, ended INTEGER NOT NULL DEFAULT 0,
  expected_segments INTEGER, duplicate_deliveries INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE segments (
  session_id TEXT NOT NULL, seq INTEGER NOT NULL, sha256 TEXT NOT NULL,
  bytes INTEGER NOT NULL, mime TEXT NOT NULL, t_start TEXT NOT NULL,
  t_end TEXT NOT NULL, received_at TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'received', spool_path TEXT NOT NULL,
  PRIMARY KEY (session_id, seq)
);
CREATE TABLE streams (
  stream_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, modality TEXT NOT NULL,
  codec TEXT NOT NULL, next_sequence INTEGER NOT NULL DEFAULT 0,
  UNIQUE (session_id, modality)
);
CREATE TABLE chunks (
  stream_id TEXT NOT NULL, sequence INTEGER NOT NULL, session_id TEXT NOT NULL,
  seq INTEGER NOT NULL, modality TEXT NOT NULL, chunk_id TEXT NOT NULL, codec TEXT NOT NULL,
  bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, blob_ref TEXT,
  dp_acked INTEGER NOT NULL DEFAULT 0, record_ids TEXT, emitted_at TEXT,
  PRIMARY KEY (stream_id, sequence), UNIQUE (session_id, seq, modality)
);
"""


def test_old_ledger_migrates_renames_and_dp_state(tmp_path):
    db = tmp_path / "ledger.db"
    conn = sqlite3.connect(db)
    conn.executescript(_OLD_SCHEMA)
    conn.execute(
        "INSERT INTO sessions (session_id, user_id, device_id, started_at)"
        " VALUES ('cap0', 'u', 'd', 't0')"
    )
    conn.execute(
        "INSERT INTO segments (session_id, seq, sha256, bytes, mime, t_start, t_end,"
        " received_at, spool_path) VALUES ('cap0', 0, 'sha', 1, 'video/mp4', 't0', 't1',"
        " 't0', '/tmp/x')"
    )
    # One confirmed inline chunk (dp_acked=1) + one un-acked (dp_acked=0) pre-slice row.
    conn.execute(
        "INSERT INTO chunks (stream_id, sequence, session_id, seq, modality, chunk_id,"
        " codec, bytes, sha256, dp_acked) VALUES"
        " ('s', 0, 'cap0', 0, 'audio', 'c0', 'audio/wav', 1, 'sha', 1),"
        " ('s', 1, 'cap0', 0, 'video', 'c1', 'video/mp4', 1, 'sha', 0)"
    )
    conn.commit()
    conn.close()

    # Constructing the Ledger runs the rename migration, then _SCHEMA, then column adds.
    led = Ledger(db)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "captures" in tables and "sessions" not in tables

    seg_cols = {r["name"] for r in conn.execute("PRAGMA table_info(segments)")}
    assert {"capture_id", "segment_num", "device_tz",
            "device_utc_offset_minutes"} <= seg_cols
    assert "seq" not in seg_cols and "session_id" not in seg_cols

    chunk_cols = {r["name"] for r in conn.execute("PRAGMA table_info(chunks)")}
    assert {"capture_id", "segment_num", "dp_state"} <= chunk_cols

    rows = {r["sequence"]: r["dp_state"]
            for r in conn.execute("SELECT sequence, dp_state FROM chunks")}
    conn.close()
    assert rows[0] == "processed"   # dp_acked=1 backfilled -> processed (not NULL/unemitted)
    assert rows[1] is None          # dp_acked=0 stays NULL (unemitted) — correct

    # The renamed rows are live through the normal API: the capture and its segment
    # survived with their data intact.
    cap = led.get_capture("cap0")
    assert cap is not None and cap["user_id"] == "u"
    assert led.segment_states("cap0") == [(0, "received")]
