"""D27 — `ingest_time` splits into `created_at` + `updated_at` (the §5.1 byte-compare).

The rules under test, verbatim from the ratified row and the Stage D close-out:

  * `created_at` = first landing of a `record_id` (it inherits ingest_time's
    preserved-across-reprocess semantics);
  * `updated_at` bumps ONLY when the incoming `record_json` byte-compares different
    from the stored row — a byte-identical re-POST answers 200 + record_id with the row
    COMPLETELY untouched: `updated_at`, `created_at`, and rowid (DP's crash-convergence
    and heal no-op paths depend on all three);
  * blind replace, never fuller-wins (founder ruling R3): the upsert replaces
    unconditionally on difference — a heal can legitimately REGRESS a previously-green
    slot; DP's ledger carries hole truth and convergence is the guarantee;
  * cache invalidation covers BOTH windows an `updated_at` move touches (founder
    ruling R2): the old window re-materializes WITHOUT the moved record — it dissolves
    out, the D18 watermark philosophy.

`updated_at` stays second-granularity (`db._TS_FMT` is the one spelling of every
storage-minted stamp), so ties between consecutive heals are real and the rowid
tiebreak is load-bearing — pinned here and documented at the mint site.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.db import Store
from tests.conftest import make_c2, record_id_for, slot_acoustic, slot_asr, slot_ocr


@pytest.fixture()
def store(tmp_path) -> Store:
    return Store(path=str(tmp_path / "test.db"), raw_root=str(tmp_path / "raw"))


def _clock(monkeypatch, stamp: str) -> None:
    """Pin storage's minting clock, so bumps are deterministic instead of racy."""
    import app.db as db_module
    monkeypatch.setattr(db_module, "_utc_now", lambda: stamp)


def _row(store: Store, record_id: str) -> sqlite3.Row:
    with store._connect() as conn:
        return conn.execute(
            "SELECT rowid, created_at, updated_at, record_json FROM context_records "
            "WHERE record_id = ?", (record_id,)
        ).fetchone()


def test_first_landing_mints_created_equal_updated(store, monkeypatch):
    _clock(monkeypatch, "2026-08-06T10:00:00Z")
    record = make_c2(user_id="u1")
    store.put_context(record)
    row = _row(store, record["record_id"])
    assert row["created_at"] == "2026-08-06T10:00:00Z"
    assert row["updated_at"] == "2026-08-06T10:00:00Z"


def test_byte_identical_repost_leaves_the_row_completely_untouched(store, monkeypatch):
    """The heal no-op input (Stage D: a still-holey heal re-POSTs byte-identical bytes)
    and the §4 crash-convergence input (post-POST-pre-mark replay). No re-window, no
    rowid churn, no stamp movement — the row is untouched, not merely equivalent."""
    _clock(monkeypatch, "2026-08-06T10:00:00Z")
    record = make_c2(user_id="u1")
    assert store.put_context(record) == record["record_id"]
    before = _row(store, record["record_id"])

    _clock(monkeypatch, "2026-08-06T11:00:00Z")   # the clock moves; the row must not
    assert store.put_context(record) == record["record_id"]
    after = _row(store, record["record_id"])
    assert after["updated_at"] == before["updated_at"] == "2026-08-06T10:00:00Z"
    assert after["created_at"] == before["created_at"]
    assert after["rowid"] == before["rowid"]
    assert after["record_json"] == before["record_json"]


def test_byte_different_repost_bumps_updated_and_preserves_created_and_rowid(
        store, monkeypatch):
    """The filling-heal input: bytes changed, so the record re-windows on `updated_at`
    — while `created_at` keeps first-landing truth and the rowid stays stable (the
    (chunk_id) dedup tiebreak is rowid, so `INSERT OR REPLACE` would corrupt it)."""
    rid = record_id_for("chunk-X", "asr.v1-mock.v1")
    _clock(monkeypatch, "2026-08-06T10:00:00Z")
    holey = make_c2(record_id=rid, chunk_id="chunk-X", slots={"asr": slot_asr("hello")})
    store.put_context(holey)
    before = _row(store, rid)

    _clock(monkeypatch, "2026-08-06T11:00:00Z")
    healed = make_c2(record_id=rid, chunk_id="chunk-X",
                     slots={"asr": slot_asr("hello"),
                            "acoustic": slot_acoustic(["keyboard typing"])})
    store.put_context(healed)
    after = _row(store, rid)
    assert after["updated_at"] == "2026-08-06T11:00:00Z"
    assert after["created_at"] == before["created_at"] == "2026-08-06T10:00:00Z"
    assert after["rowid"] == before["rowid"]
    assert "acoustic" in after["record_json"]


def test_a_slot_regressing_repost_replaces_unconditionally(store, monkeypatch):
    """Founder ruling R3, the test it demands: blind replace, NEVER fuller-wins. A heal
    during a different server's outage can regress a previously-green slot; storage
    replaces, the ledger carries hole truth, convergence is the guarantee. Any
    monotonicity guard here is a design violation."""
    rid = record_id_for("chunk-X", "asr.v1-mock.v1")
    _clock(monkeypatch, "2026-08-06T10:00:00Z")
    green = make_c2(record_id=rid, chunk_id="chunk-X",
                    slots={"asr": slot_asr("hello"),
                           "acoustic": slot_acoustic(["keyboard typing"])})
    store.put_context(green)

    _clock(monkeypatch, "2026-08-06T11:00:00Z")
    regressed = make_c2(record_id=rid, chunk_id="chunk-X",
                        slots={"asr": slot_asr("hello")})   # acoustic holed again
    store.put_context(regressed)
    after = _row(store, rid)
    assert "acoustic" not in after["record_json"]            # the regression LANDED
    assert after["updated_at"] == "2026-08-06T11:00:00Z"     # and re-windows


def test_a_hole_migrating_between_slots_is_a_byte_different_replace(store, monkeypatch):
    """The third heal shape (Stage D close-out law): byte-different but NOT fuller —
    one hole fills while another opens. A literal fuller-wins reading would refuse it;
    the byte-compare correctly treats it as change."""
    rid = record_id_for("chunk-X", "video.v1-mock.v1")
    _clock(monkeypatch, "2026-08-06T10:00:00Z")
    first = make_c2(record_id=rid, chunk_id="chunk-X", modality="video",
                    pipeline_version="video.v1-mock.v1",
                    slots={"ocr": slot_ocr("EXIT")})         # caption holed
    store.put_context(first)

    _clock(monkeypatch, "2026-08-06T11:00:00Z")
    migrated = make_c2(record_id=rid, chunk_id="chunk-X", modality="video",
                       pipeline_version="video.v1-mock.v1",
                       slots={"caption": {"version": "clipcap.v1-mock.v1",
                                          "value": "a desk"}})   # ocr holed instead
    store.put_context(migrated)
    after = _row(store, rid)
    assert "caption" in after["record_json"] and "EXIT" not in after["record_json"]
    assert after["updated_at"] == "2026-08-06T11:00:00Z"


def test_updated_at_keeps_the_one_minted_stamp_spelling(store, monkeypatch):
    """Second granularity, `db._TS_FMT`, fixed width, trailing Z — the property every
    lexicographic range clause in this service leans on. Kept deliberately: a
    higher-resolution `updated_at` would be a second spelling of storage-minted time,
    and the rowid tiebreak carries the ties instead (documented at the mint site)."""
    record = make_c2(user_id="u1")
    store.put_context(record)
    row = _row(store, record["record_id"])
    import re
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", row["updated_at"])
    assert row["updated_at"] == row["created_at"]


# --- R2: the day-log cache invalidation covers BOTH touched windows -----------------


def _seed_daylog(store: Store, user_id: str, window_id: str, t_start: str,
                 t_end: str) -> None:
    store.put_daylog({
        "contract": "C10", "version": "1", "user_id": user_id, "window_id": window_id,
        "t_start": t_start, "t_end": t_end, "daylog_format_version": "test",
        "recipe_id": "test", "home_tz": "UTC", "segments": [], "blocks": [],
        "content_fingerprint": "f",
    })


def _cached_windows(store: Store, user_id: str) -> set[str]:
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT window_id FROM day_logs WHERE user_id = ?", (user_id,)).fetchall()
    return {r["window_id"] for r in rows}


def test_a_byte_different_repost_invalidates_both_touched_windows(store, monkeypatch):
    """Founder ruling R2: the old window (holding the previous `updated_at`) AND the
    new one both drop their cached day-logs. The re-materialized old window then
    excludes the moved record — it dissolves out (D18), it is not edited in place."""
    _clock(monkeypatch, "2026-08-06T10:00:30Z")
    record = make_c2(user_id="u1")
    store.put_context(record)
    # Old window holds the record's current updated_at; new window will hold the bump;
    # a bystander window holds neither.
    _seed_daylog(store, "u1", "w20260806T110000Z",
                 "2026-08-06T10:00:00Z", "2026-08-06T11:00:00Z")
    _seed_daylog(store, "u1", "w20260806T130000Z",
                 "2026-08-06T12:00:00Z", "2026-08-06T13:00:00Z")
    _seed_daylog(store, "u1", "w20260806T090000Z",
                 "2026-08-06T08:00:00Z", "2026-08-06T09:00:00Z")

    _clock(monkeypatch, "2026-08-06T12:00:30Z")   # inside the second window
    changed = dict(record)
    changed["content"] = {"slots": {"asr": slot_asr("a byte-different heal")}}
    store.put_context(changed)

    assert _cached_windows(store, "u1") == {"w20260806T090000Z"}  # bystander survives


def test_a_byte_identical_repost_invalidates_nothing(store, monkeypatch):
    """The no-op half of D27's rule: no change, no re-window, no cache loss — the
    exact hole `ingest_time`'s single-window invalidation existed to cover is gone,
    because an unchanged record cannot go stale."""
    _clock(monkeypatch, "2026-08-06T10:00:30Z")
    record = make_c2(user_id="u1")
    store.put_context(record)
    _seed_daylog(store, "u1", "w20260806T110000Z",
                 "2026-08-06T10:00:00Z", "2026-08-06T11:00:00Z")

    _clock(monkeypatch, "2026-08-06T12:00:30Z")
    store.put_context(record)                      # byte-identical
    assert _cached_windows(store, "u1") == {"w20260806T110000Z"}


def test_a_fresh_landing_invalidates_the_window_it_lands_in(store, monkeypatch):
    _seed_daylog(store, "u1", "w20260806T110000Z",
                 "2026-08-06T10:00:00Z", "2026-08-06T11:00:00Z")
    _clock(monkeypatch, "2026-08-06T10:00:30Z")
    store.put_context(make_c2(user_id="u1"))
    assert _cached_windows(store, "u1") == set()


def test_invalidation_is_per_user(store, monkeypatch):
    _seed_daylog(store, "u2", "w20260806T110000Z",
                 "2026-08-06T10:00:00Z", "2026-08-06T11:00:00Z")
    _clock(monkeypatch, "2026-08-06T10:00:30Z")
    store.put_context(make_c2(user_id="u1"))
    assert _cached_windows(store, "u2") == {"w20260806T110000Z"}


# --- migration: a pre-E DB gains the split ------------------------------------------


def test_migration_renames_ingest_time_and_adds_updated_at(tmp_path):
    """A DB created before D27 carries `ingest_time`. The ladder renames it to
    `created_at` (same first-landing semantics, so the data is already correct),
    adds `updated_at` backfilled equal (the only honest value: the last known change
    is the landing itself), drops the ingest index and builds the updated_at one.
    The OD-2 cutover wipes /context anyway; this keeps every other table's DB file
    openable without a special case."""
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:  # the pre-E shape, verbatim
        conn.execute(
            "CREATE TABLE context_records ("
            " record_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,"
            " t_start TEXT NOT NULL, t_end TEXT, chunk_id TEXT,"
            " pipeline_version TEXT, ingest_time TEXT NOT NULL,"
            " device_tz TEXT, device_utc_offset_minutes INTEGER,"
            " record_json TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX idx_context_user_ingest ON context_records (user_id, ingest_time)")
        conn.execute(
            "INSERT INTO context_records VALUES"
            " ('r-old','u1','2026-07-01T00:00:00Z',NULL,'c1','v1',"
            "  '2026-07-01T00:00:01Z',NULL,NULL,'{}')"
        )

    store = Store(path=str(db), raw_root=str(tmp_path / "raw"))
    with store._connect() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(context_records)")}
        assert "created_at" in cols and "updated_at" in cols
        assert "ingest_time" not in cols
        indexes = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert "idx_context_user_updated" in indexes
        assert "idx_context_user_ingest" not in indexes
        old = conn.execute(
            "SELECT created_at, updated_at FROM context_records "
            "WHERE record_id = 'r-old'").fetchone()
        assert old["created_at"] == old["updated_at"] == "2026-07-01T00:00:01Z"

    # And the migrated DB takes new writes through the v2 upsert, byte-compare included.
    record = make_c2(user_id="u1")
    store.put_context(record)
    row = _row(store, record["record_id"])
    assert row["created_at"] == row["updated_at"]


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "fresh.db"
    Store(path=str(db), raw_root=str(tmp_path / "raw"))
    store = Store(path=str(db), raw_root=str(tmp_path / "raw"))  # re-open, re-migrate
    with store._connect() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(context_records)")}
    assert "created_at" in cols and "updated_at" in cols and "ingest_time" not in cols
