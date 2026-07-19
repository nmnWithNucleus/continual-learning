"""The additive `dp_state` migration on a pre-slice ledger.db: the column is added AND
backfilled so an already-confirmed (dp_acked=1) chunk reads 'processed', not NULL."""
from __future__ import annotations

import sqlite3

from app.ledger import Ledger

# A pre-slice chunks table — exactly the M1 schema, WITHOUT the dp_state column.
_OLD_CHUNKS = """
CREATE TABLE chunks (
  stream_id TEXT NOT NULL, sequence INTEGER NOT NULL, session_id TEXT NOT NULL,
  seq INTEGER NOT NULL, modality TEXT NOT NULL, chunk_id TEXT NOT NULL, codec TEXT NOT NULL,
  bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, blob_ref TEXT,
  dp_acked INTEGER NOT NULL DEFAULT 0, record_ids TEXT, emitted_at TEXT,
  PRIMARY KEY (stream_id, sequence), UNIQUE (session_id, seq, modality)
);
"""


def test_dp_state_migration_adds_and_backfills(tmp_path):
    db = tmp_path / "ledger.db"
    conn = sqlite3.connect(db)
    conn.executescript(_OLD_CHUNKS)
    # One confirmed inline chunk (dp_acked=1) + one un-acked (dp_acked=0) pre-slice row.
    conn.execute(
        "INSERT INTO chunks (stream_id, sequence, session_id, seq, modality, chunk_id,"
        " codec, bytes, sha256, dp_acked) VALUES"
        " ('s', 0, 'sess', 0, 'audio', 'c0', 'audio/wav', 1, 'sha', 1),"
        " ('s', 1, 'sess', 0, 'video', 'c1', 'video/mp4', 1, 'sha', 0)"
    )
    conn.commit()
    conn.close()

    # Constructing the Ledger runs _migrate: ADD COLUMN dp_state + one-shot backfill.
    Ledger(db)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(chunks)")}
    assert "dp_state" in cols
    rows = {r["sequence"]: r["dp_state"]
            for r in conn.execute("SELECT sequence, dp_state FROM chunks")}
    conn.close()
    assert rows[0] == "processed"   # dp_acked=1 backfilled -> processed (not NULL/unemitted)
    assert rows[1] is None          # dp_acked=0 stays NULL (unemitted) — correct
