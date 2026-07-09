"""/context store (C2): write->read round-trip + schema-validate, idempotent upsert,
time-range read ordering, per-user isolation, 404s, invalid-C2 -> 422."""
from __future__ import annotations

from app import schemas
from tests.conftest import make_c2, record_id_for


def test_write_read_roundtrip(client):
    record = make_c2()
    # Fixture must itself be a valid C2 (guards the test, not just the service).
    assert schemas.validate_c2(record) == []

    ack = client.post("/context/records", json=record)
    assert ack.status_code == 200
    assert ack.json() == {"ok": True, "record_id": record["record_id"]}

    got = client.get(f"/context/records/{record['record_id']}")
    assert got.status_code == 200
    stored = got.json()
    # Exact round-trip: what we stored is exactly what we read back.
    assert stored == record
    # And it still schema-validates as a C2 on the way out.
    assert schemas.validate_c2(stored) == []


def test_get_missing_record_404(client):
    resp = client.get("/context/records/does-not-exist")
    assert resp.status_code == 404


def test_idempotent_upsert_on_record_id(client):
    # record_id is deterministic on (chunk_id, pipeline_version): re-processing the same
    # chunk under the same pipeline_version is an idempotent upsert, not a duplicate.
    rid = record_id_for("chunk-X", "asr-mock-v0")
    first = make_c2(record_id=rid, chunk_id="chunk-X", text="first pass transcript")
    second = make_c2(record_id=rid, chunk_id="chunk-X", text="reprocessed transcript")

    assert client.post("/context/records", json=first).status_code == 200
    assert client.post("/context/records", json=second).status_code == 200

    stored = client.get(f"/context/records/{rid}").json()
    assert stored["content"]["text"] == "reprocessed transcript"  # upsert took effect

    # No duplicate row: exactly one record for this record_id.
    store = client.app.state.store
    with store._connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) c FROM context_records WHERE record_id = ?", (rid,)
        ).fetchone()["c"]
    assert n == 1


def test_time_range_ordering_and_bounds(client):
    # Three records for one user across the morning, written out of order.
    r_0900 = make_c2(user_id="u-time", chunk_id="c-0900", t_start="2026-07-09T09:00:00Z")
    r_1000 = make_c2(user_id="u-time", chunk_id="c-1000", t_start="2026-07-09T10:00:00Z")
    r_1100 = make_c2(user_id="u-time", chunk_id="c-1100", t_start="2026-07-09T11:00:00Z")
    for rec in (r_1100, r_0900, r_1000):
        assert client.post("/context/records", json=rec).status_code == 200

    # Full-range read: ordered by t_start ascending.
    resp = client.get("/context/records", params={"user_id": "u-time"})
    assert resp.status_code == 200
    got = resp.json()
    assert [r["source"]["chunk_id"] for r in got] == ["c-0900", "c-1000", "c-1100"]
    for r in got:
        assert schemas.validate_c2(r) == []

    # Half-open window [from, to): 09:00 inclusive, 11:00 exclusive -> 0900 + 1000 only.
    windowed = client.get(
        "/context/records",
        params={"user_id": "u-time", "from": "2026-07-09T09:00:00Z",
                "to": "2026-07-09T11:00:00Z"},
    ).json()
    assert [r["source"]["chunk_id"] for r in windowed] == ["c-0900", "c-1000"]

    # Open-ended lower bound only.
    from_only = client.get(
        "/context/records",
        params={"user_id": "u-time", "from": "2026-07-09T10:00:00Z"},
    ).json()
    assert [r["source"]["chunk_id"] for r in from_only] == ["c-1000", "c-1100"]


def test_per_user_isolation(client):
    a = make_c2(user_id="alice", chunk_id="a-1", t_start="2026-07-09T09:00:00Z")
    b = make_c2(user_id="bob", chunk_id="b-1", t_start="2026-07-09T09:00:00Z")
    assert client.post("/context/records", json=a).status_code == 200
    assert client.post("/context/records", json=b).status_code == 200

    alice = client.get("/context/records", params={"user_id": "alice"}).json()
    assert [r["source"]["chunk_id"] for r in alice] == ["a-1"]
    # Bob's record does not leak into alice's timeline.
    assert all(r["user_id"] == "alice" for r in alice)


def test_list_unknown_user_empty(client):
    resp = client.get("/context/records", params={"user_id": "nobody"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_requires_user_id(client):
    assert client.get("/context/records").status_code == 422


def test_reject_invalid_c2(client):
    # Missing required field.
    bad = make_c2()
    del bad["t_start"]
    assert client.post("/context/records", json=bad).status_code == 422

    # Extra/unknown field (additionalProperties: false).
    bad2 = make_c2()
    bad2["surprise"] = "nope"
    assert client.post("/context/records", json=bad2).status_code == 422

    # Wrong const.
    bad3 = make_c2()
    bad3["contract"] = "C4"
    assert client.post("/context/records", json=bad3).status_code == 422

    # Bad modality enum in nested source ("speech" is C3's token, not C2's "audio").
    bad4 = make_c2()
    bad4["source"]["modality"] = "speech"
    assert client.post("/context/records", json=bad4).status_code == 422

    # Segment speaker key must be PRESENT (required-nullable) — dropping it is invalid.
    bad5 = make_c2()
    del bad5["content"]["segments"][0]["speaker"]
    assert client.post("/context/records", json=bad5).status_code == 422
