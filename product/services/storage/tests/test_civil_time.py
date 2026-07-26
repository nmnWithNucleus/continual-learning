"""D17 — the civil-time context storage keeps alongside every UTC timestamp.

`context_records` now carries `device_tz` + `device_utc_offset_minutes`, promoted
out of `record_json` so a civil-time question ("the user's local Tuesday, 09:00
to 17:00") is a column read rather than a JSON scan. Two invariants matter and are
pinned here: UTC stays canonical and the only ordering/range axis, and the columns
are a PROJECTION of the verbatim C2 — never an edit to it.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from app.db import Store
from tests.conftest import make_c2


@pytest.fixture()
def store(client) -> Store:
    """The same DB the `client` fixture just pointed the app at, opened directly
    so tests can assert on COLUMNS (the HTTP surface only shows record_json)."""
    return Store(path=os.environ["STORAGE_DB_PATH"],
                 raw_root=os.environ["STORAGE_RAW_DIR"])


def _row(store: Store, record_id: str) -> sqlite3.Row:
    with store._connect() as conn:
        return conn.execute(
            "SELECT device_tz, device_utc_offset_minutes, record_json"
            " FROM context_records WHERE record_id = ?", (record_id,)
        ).fetchone()


def test_civil_time_is_promoted_to_columns(client, store):
    record = make_c2()
    record["source"]["device_tz"] = "America/Los_Angeles"
    record["source"]["device_utc_offset_minutes"] = -420
    assert client.post("/context/records", json=record).status_code == 200

    row = _row(store, record["record_id"])
    assert row["device_tz"] == "America/Los_Angeles"
    assert row["device_utc_offset_minutes"] == -420


def test_stored_record_is_still_returned_verbatim(client):
    """The columns are a projection. What C2 wrote is what /context serves back,
    byte for byte — storage produces no data and edits none."""
    record = make_c2()
    record["source"]["device_tz"] = "Asia/Tokyo"
    record["source"]["device_utc_offset_minutes"] = 540
    client.post("/context/records", json=record)

    got = client.get(f"/context/records/{record['record_id']}")
    assert got.status_code == 200
    assert got.json() == record


def test_records_without_civil_time_store_null(client, store):
    """A record from a device that couldn't report a zone stores NULL, not a
    fabricated default. NULL reads downstream as 'fall back to home_tz'."""
    record = make_c2()
    assert client.post("/context/records", json=record).status_code == 200
    row = _row(store, record["record_id"])
    assert row["device_tz"] is None
    assert row["device_utc_offset_minutes"] is None


def test_reprocess_upsert_refreshes_civil_time(client, store):
    """A reprocess under the same pipeline_version overwrites in place; the
    promoted columns must move with it, not go stale against record_json."""
    record = make_c2()
    client.post("/context/records", json=record)
    assert _row(store, record["record_id"])["device_tz"] is None

    record["source"]["device_tz"] = "Europe/Berlin"
    record["source"]["device_utc_offset_minutes"] = 120
    assert client.post("/context/records", json=record).status_code == 200

    row = _row(store, record["record_id"])
    assert row["device_tz"] == "Europe/Berlin"
    assert row["device_utc_offset_minutes"] == 120


def test_range_read_needs_no_timezone(client):
    """C10 is a DURATION query. UTC alone answers it — the zone is context beside
    the instant, never part of the range arithmetic and never an index."""
    early = make_c2(t_start="2026-07-20T10:00:00Z")
    early["source"]["device_tz"] = "Asia/Tokyo"
    late = make_c2(t_start="2026-07-20T20:00:00Z")
    late["source"]["device_tz"] = "America/Los_Angeles"
    for rec in (early, late):
        client.post("/context/records", json=rec)

    got = client.get("/context/records", params={
        "user_id": early["user_id"],
        "from": "2026-07-20T09:00:00Z", "to": "2026-07-20T12:00:00Z",
    })
    assert got.status_code == 200
    ids = [r["record_id"] for r in got.json()]
    assert early["record_id"] in ids
    assert late["record_id"] not in ids


def test_migration_adds_columns_to_a_preexisting_db(tmp_path):
    """A live DB (node-7) predates these columns. CREATE TABLE IF NOT EXISTS is a
    no-op on an existing table, so without the ALTER migration the columns would
    never appear. Deliberately NO backfill: a record landed before clients
    reported a zone genuinely has none, and inventing one is the exact failure
    this slice removes."""
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:  # the pre-D17 shape, verbatim
        conn.execute(
            "CREATE TABLE context_records ("
            " record_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,"
            " t_start TEXT NOT NULL, t_end TEXT, chunk_id TEXT,"
            " pipeline_version TEXT, ingest_time TEXT NOT NULL,"
            " record_json TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO context_records VALUES"
            " ('r-old','u1','2026-07-01T00:00:00Z',NULL,'c1','v1',"
            "  '2026-07-01T00:00:01Z','{}')"
        )

    store = Store(path=str(db), raw_root=str(tmp_path / "raw"))
    with store._connect() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(context_records)")}
        assert {"device_tz", "device_utc_offset_minutes"} <= cols
        old = conn.execute(
            "SELECT device_tz FROM context_records WHERE record_id = 'r-old'"
        ).fetchone()
        assert old["device_tz"] is None  # not backfilled with a guess

    # And the upgraded DB takes new writes through the widened INSERT.
    record = make_c2(user_id="u1")
    record["source"]["device_tz"] = "Europe/Berlin"
    store.put_context(record)
    assert _row(store, record["record_id"])["device_tz"] == "Europe/Berlin"
