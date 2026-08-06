"""E-2 — whole-record retraction, finally built (D28; charter M5's cascade rule).

The redesigned shape, verbatim from the ratified row: DELETE by `record_id` /
`chunk_id` / `pipeline_version` (whole records, never slot- or kind-granular — the
kind-granular design retired unbuilt); an auditable MANIFEST of counts by
`pipeline_version`; the day-log cache invalidation CASCADES to every affected window
("a retraction that clears /context and leaves a day-log standing has deleted
nothing").

THE LEDGER BOUNDARY, stated plainly and tested at the bottom: retraction never touches
DP's done-ledger, so a retracted chunk's redelivery still SKIPS — DP answers 200 with a
record_id storage no longer holds, and NO write reaches storage. That is the designed
posture, not a bug: E-2 is a retention / right-to-be-forgotten primitive, never a
correctness mechanism ("deletion is never the mechanism for correctness" stands).
Rebuild-after-retraction is the OD-2 `/raw` replay tool (future) or a version bump.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from app.db import Store
from tests.conftest import make_c2, record_id_for, slot_asr, slot_caption

UTC = timezone.utc


@pytest.fixture()
def store(client) -> Store:
    return Store(path=os.environ["STORAGE_DB_PATH"],
                 raw_root=os.environ["STORAGE_RAW_DIR"])


def _rec(user_id="u1", chunk_id="chunk-A", pv="asr.v1-mock.v1", text="hello") -> dict:
    return make_c2(user_id=user_id, chunk_id=chunk_id, pipeline_version=pv,
                   t_start="2026-07-24T02:00:00Z", t_end="2026-07-24T02:00:00Z",
                   slots={"asr": slot_asr(text)})


def _land(store: Store, record: dict, stamp: str = "2026-07-24T02:00:00Z") -> dict:
    store.put_context(record)
    with store._connect() as conn:
        conn.execute("UPDATE context_records SET created_at = ?, updated_at = ? "
                     "WHERE record_id = ?", (stamp, stamp, record["record_id"]))
        conn.commit()
    return record


# --- the three selectors -------------------------------------------------------------


def test_delete_by_record_id(client):
    keep = _rec(chunk_id="chunk-keep")
    drop = _rec(chunk_id="chunk-drop")
    for r in (keep, drop):
        assert client.post("/context/records", json=r).status_code == 200

    res = client.request("DELETE", "/context/records",
                         params={"user_id": "u1", "record_id": drop["record_id"]})
    assert res.status_code == 200, res.text
    manifest = res.json()
    assert manifest["records"] == 1
    assert manifest["by_pipeline_version"] == {"asr.v1-mock.v1": 1}
    assert manifest["dry_run"] is False

    assert client.get(f"/context/records/{drop['record_id']}").status_code == 404
    assert client.get(f"/context/records/{keep['record_id']}").status_code == 200


def test_delete_by_chunk_id_takes_every_dialect_beside(client):
    """A version-forward reprocess lands BESIDE the old record (L8 case 2) — a chunk
    retraction must take the whole lineage, which is exactly what the manifest's
    by-pipeline_version counts make auditable."""
    old = _rec(chunk_id="chunk-A", pv="asr.v1-mock.v1")
    new = _rec(chunk_id="chunk-A", pv="asr.v2-mock.v1")
    other = _rec(chunk_id="chunk-B")
    for r in (old, new, other):
        assert client.post("/context/records", json=r).status_code == 200

    manifest = client.request("DELETE", "/context/records",
                              params={"user_id": "u1", "chunk_id": "chunk-A"}).json()
    assert manifest["records"] == 2
    assert manifest["by_pipeline_version"] == {"asr.v1-mock.v1": 1, "asr.v2-mock.v1": 1}
    assert client.get(f"/context/records/{old['record_id']}").status_code == 404
    assert client.get(f"/context/records/{new['record_id']}").status_code == 404
    assert client.get(f"/context/records/{other['record_id']}").status_code == 200


def test_delete_by_pipeline_version(client):
    v1a = _rec(chunk_id="chunk-A", pv="asr.v1-mock.v1")
    v1b = _rec(chunk_id="chunk-B", pv="asr.v1-mock.v1")
    v2 = _rec(chunk_id="chunk-C", pv="asr.v2-mock.v1")
    for r in (v1a, v1b, v2):
        assert client.post("/context/records", json=r).status_code == 200

    manifest = client.request(
        "DELETE", "/context/records",
        params={"user_id": "u1", "pipeline_version": "asr.v1-mock.v1"}).json()
    assert manifest["records"] == 2
    assert manifest["by_pipeline_version"] == {"asr.v1-mock.v1": 2}
    assert client.get(f"/context/records/{v2['record_id']}").status_code == 200


def test_selectors_combine_with_and(client):
    """chunk_id + pipeline_version names ONE dialect of one chunk; the other dialect
    survives."""
    old = _rec(chunk_id="chunk-A", pv="asr.v1-mock.v1")
    new = _rec(chunk_id="chunk-A", pv="asr.v2-mock.v1")
    for r in (old, new):
        assert client.post("/context/records", json=r).status_code == 200

    manifest = client.request(
        "DELETE", "/context/records",
        params={"user_id": "u1", "chunk_id": "chunk-A",
                "pipeline_version": "asr.v1-mock.v1"}).json()
    assert manifest["records"] == 1
    assert client.get(f"/context/records/{old['record_id']}").status_code == 404
    assert client.get(f"/context/records/{new['record_id']}").status_code == 200


# --- the guard rails -----------------------------------------------------------------


def test_a_selectorless_delete_is_a_422_never_a_wipe(client):
    record = _rec()
    assert client.post("/context/records", json=record).status_code == 200
    res = client.request("DELETE", "/context/records", params={"user_id": "u1"})
    assert res.status_code == 422
    assert res.json()["detail"]["error"] == "no selector"
    assert client.get(f"/context/records/{record['record_id']}").status_code == 200


def test_user_id_is_required_and_isolation_fails_closed(client):
    """(Chunk ids are globally unique ULIDs in production — L3 makes record identity
    (chunk_id, pipeline_version), so the fixtures use per-user chunks as reality does.)"""
    mine = _rec(user_id="alice", chunk_id="chunk-alice")
    theirs = _rec(user_id="bob", chunk_id="chunk-bob")
    for r in (mine, theirs):
        assert client.post("/context/records", json=r).status_code == 200

    assert client.request("DELETE", "/context/records",
                          params={"chunk_id": "chunk-alice"}).status_code == 422

    # A cross-user attempt retracts NOTHING — isolation fails closed with an honest
    # zero, revealing nothing about bob's rows.
    cross = client.request("DELETE", "/context/records",
                           params={"user_id": "alice", "chunk_id": "chunk-bob"}).json()
    assert cross["records"] == 0
    assert client.get(f"/context/records/{theirs['record_id']}").status_code == 200

    manifest = client.request("DELETE", "/context/records",
                              params={"user_id": "alice",
                                      "chunk_id": "chunk-alice"}).json()
    assert manifest["records"] == 1
    assert client.get(f"/context/records/{theirs['record_id']}").status_code == 200


def test_retracting_nothing_is_an_honest_zero_not_an_error(client):
    """A retry of a completed retraction is a no-op with a zero manifest — idempotent,
    exactly like every other write surface here."""
    manifest = client.request("DELETE", "/context/records",
                              params={"user_id": "u1", "chunk_id": "chunk-ghost"}).json()
    assert manifest["records"] == 0
    assert manifest["by_pipeline_version"] == {}
    assert manifest["day_logs_invalidated"] == 0


def test_dry_run_returns_the_manifest_without_deleting(client, store):
    record = _rec()
    assert client.post("/context/records", json=record).status_code == 200

    dry = client.request("DELETE", "/context/records",
                         params={"user_id": "u1", "chunk_id": record["source"]["chunk_id"],
                                 "dry_run": "true"}).json()
    assert dry["dry_run"] is True
    assert dry["records"] == 1
    assert dry["by_pipeline_version"] == {"asr.v1-mock.v1": 1}
    assert dry["day_logs_invalidated"] == 0        # nothing was touched, so none
    assert client.get(f"/context/records/{record['record_id']}").status_code == 200

    wet = client.request("DELETE", "/context/records",
                         params={"user_id": "u1",
                                 "chunk_id": record["source"]["chunk_id"]}).json()
    assert wet["records"] == dry["records"]
    assert wet["by_pipeline_version"] == dry["by_pipeline_version"]
    assert client.get(f"/context/records/{record['record_id']}").status_code == 404


# --- the day-log cascade (charter M5: the widening D18 named) ------------------------


def test_the_day_log_cache_cascade_reaches_every_affected_window(store):
    """A retraction that clears /context and leaves a day-log standing has deleted
    nothing. The cascade INVALIDATES (the home_tz-correction mechanism): the cached row
    drops, and the re-materialized window renders WITHOUT the retracted record."""
    store.put_profile("u1", "UTC")
    gone = _land(store, make_c2(user_id="u1", chunk_id="chunk-gone",
                                t_start="2026-07-24T02:00:00Z",
                                slots={"caption": slot_caption("retract me")}),
                 "2026-07-24T02:00:00Z")
    stays = _land(store, make_c2(user_id="u1", chunk_id="chunk-stays",
                                 t_start="2026-07-24T02:00:20Z",
                                 slots={"caption": slot_caption("keep me")}),
                  "2026-07-24T02:00:01Z")
    window = store.open_training_window(
        "u1", now=datetime(2026, 7, 24, 4, 0, 0, tzinfo=UTC))
    from app.daylog import materialize_daylog
    before = materialize_daylog(store, "u1", window["window_id"])
    assert "retract me" in " ".join(b["text"] for b in before["blocks"])

    manifest = store.retract_context("u1", chunk_id="chunk-gone")
    assert manifest["records"] == 1
    assert manifest["day_logs_invalidated"] == 1

    after = materialize_daylog(store, "u1", window["window_id"])
    rendered = " ".join(b["text"] for b in after["blocks"])
    assert "retract me" not in rendered
    assert "keep me" in rendered
    assert after["content_fingerprint"] != before["content_fingerprint"]
    assert stays["record_id"]  # the survivor is still a record


def test_the_cascade_spans_windows_when_the_records_do(store):
    """Records of one pipeline_version spread over two windows: retracting the version
    invalidates BOTH cached day-logs, and only those (a bystander window survives)."""
    store.put_profile("u1", "UTC")
    _land(store, make_c2(user_id="u1", chunk_id="chunk-w1",
                         t_start="2026-07-24T02:00:00Z",
                         slots={"caption": slot_caption("first window")}),
          "2026-07-24T02:00:00Z")
    _land(store, make_c2(user_id="u1", chunk_id="chunk-w2",
                         t_start="2026-07-24T05:00:00Z",
                         slots={"caption": slot_caption("second window")}),
          "2026-07-24T05:00:00Z")
    from app.daylog import materialize_daylog
    window_a = store.open_training_window(
        "u1", now=datetime(2026, 7, 24, 4, 0, 0, tzinfo=UTC))
    materialize_daylog(store, "u1", window_a["window_id"])
    store.close_training_window("u1", window_a["window_id"], "published")
    window_b = store.open_training_window(
        "u1", now=datetime(2026, 7, 24, 6, 1, 0, tzinfo=UTC))
    materialize_daylog(store, "u1", window_b["window_id"])
    # A bystander cache row for another user, outside the blast radius.
    store.put_daylog({"contract": "C10", "version": "1", "user_id": "u2",
                      "window_id": "w20260724T035900Z",
                      "t_start": "2026-07-24T02:00:00Z",
                      "t_end": "2026-07-24T03:59:00Z",
                      "daylog_format_version": "x", "recipe_id": "x", "home_tz": "UTC",
                      "segments": [], "blocks": [], "content_fingerprint": "f"})

    manifest = store.retract_context("u1", pipeline_version="asr.v1-mock.v1")
    assert manifest["records"] == 2
    assert manifest["day_logs_invalidated"] == 2

    with store._connect() as conn:
        rows = conn.execute("SELECT user_id, window_id FROM day_logs").fetchall()
    assert [(r["user_id"], r["window_id"]) for r in rows] == [
        ("u2", "w20260724T035900Z")]


def test_retraction_leaves_raw_blobs_alone(client, store):
    """E-2 retracts PROCESSED records; /raw bytes are sacred (OD-2) and have their own
    M5 primitives. The blob a retracted record pointed at still serves."""
    data = b"raw-bytes-of-the-chunk"
    import hashlib
    put = client.put("/raw/blobs",
                     params={"user_id": "u1", "chunk_id": "chunk-A",
                             "sha256": hashlib.sha256(data).hexdigest()},
                     content=data)
    assert put.status_code == 200
    blob_ref = put.json()["blob_ref"]
    record = _rec(chunk_id="chunk-A")
    assert client.post("/context/records", json=record).status_code == 200

    client.request("DELETE", "/context/records",
                   params={"user_id": "u1", "chunk_id": "chunk-A"})
    got = client.get("/raw/blobs", params={"ref": blob_ref})
    assert got.status_code == 200 and got.content == data


# --- the ledger boundary -------------------------------------------------------------


def test_retract_then_redeliver_skip_no_resurrection(client, store):
    """The drill the brief names, with the skip reply SIMULATED at the wire boundary
    (the cross-service replay drill is Stage F's): DP's done-ledger still holds the
    green row after a retraction, so a redelivery is L8 case 3 — SKIP, answered from
    the ledger as `200 {ok, record_ids:[rid]}` with NO POST to storage. Storage
    therefore never sees a write, and the retracted record stays retracted."""
    rid = record_id_for("chunk-A", "asr.v1-mock.v1")
    record = _rec(chunk_id="chunk-A")
    assert record["record_id"] == rid
    assert client.post("/context/records", json=record).status_code == 200

    manifest = client.request("DELETE", "/context/records",
                              params={"user_id": "u1", "record_id": rid}).json()
    assert manifest["records"] == 1

    # The redelivery: DP's claim tree reads its OWN ledger (never a storage read —
    # a Stage D decision), finds version-match all-green, and skips. The reply below
    # is the D16 wire shape it answers with; storage receives NOTHING.
    simulated_skip_reply = {"ok": True, "record_ids": [rid]}
    assert simulated_skip_reply["record_ids"] == [rid]   # an id storage no longer holds

    assert client.get(f"/context/records/{rid}").status_code == 404
    with store._connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM context_records").fetchone()["n"]
    assert n == 0                                        # no resurrection, no side rows
