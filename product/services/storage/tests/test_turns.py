"""/sessions turn store: C4 write→read round-trip, list-by-session, validation."""
from __future__ import annotations

from app import schemas
from tests.conftest import make_c4


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_write_read_roundtrip(client):
    record = make_c4()
    # Fixture must itself be a valid C4 (guards the test, not just the service).
    assert schemas.validate_c4(record) == []

    ack = client.post("/sessions/turns", json=record)
    assert ack.status_code == 200
    assert ack.json() == {"ok": True, "turn_id": record["turn_id"]}

    got = client.get(f"/sessions/turns/{record['turn_id']}")
    assert got.status_code == 200
    stored = got.json()
    # Exact round-trip: what we stored is exactly what we read back.
    assert stored == record
    # And it still schema-validates as a C4 on the way out.
    assert schemas.validate_c4(stored) == []


def test_get_missing_turn_404(client):
    resp = client.get("/sessions/turns/does-not-exist")
    assert resp.status_code == 404


def test_list_by_session_ordered(client):
    # Two turns in sess-A (out-of-order created_at), one in sess-B.
    t_late = make_c4(session_id="sess-A", turn_id="A-late",
                     created_at="2026-07-09T12:05:00Z")
    t_early = make_c4(session_id="sess-A", turn_id="A-early",
                      created_at="2026-07-09T12:00:00Z")
    t_other = make_c4(session_id="sess-B", turn_id="B-1",
                      created_at="2026-07-09T12:01:00Z")
    for rec in (t_late, t_early, t_other):
        assert client.post("/sessions/turns", json=rec).status_code == 200

    resp = client.get("/sessions/sess-A/turns")
    assert resp.status_code == 200
    turns = resp.json()
    assert [t["turn_id"] for t in turns] == ["A-early", "A-late"]  # ordered by created_at
    for t in turns:
        assert schemas.validate_c4(t) == []

    # Session isolation: sess-B's turn does not leak into sess-A.
    other = client.get("/sessions/sess-B/turns").json()
    assert [t["turn_id"] for t in other] == ["B-1"]


def test_list_unknown_session_empty(client):
    resp = client.get("/sessions/nope/turns")
    assert resp.status_code == 200
    assert resp.json() == []


def test_write_is_idempotent_on_turn_id(client):
    rec = make_c4(turn_id="dup-1", response_text="first")
    assert client.post("/sessions/turns", json=rec).status_code == 200
    rec2 = dict(rec, response_text="second (updated)")
    assert client.post("/sessions/turns", json=rec2).status_code == 200

    stored = client.get("/sessions/turns/dup-1").json()
    assert stored["response_text"] == "second (updated)"
    # No duplicate row created.
    listed = client.get(f"/sessions/{rec['session_id']}/turns").json()
    assert [t["turn_id"] for t in listed] == ["dup-1"]


def test_reject_invalid_c4(client):
    # Missing required field.
    bad = make_c4()
    del bad["response_text"]
    r = client.post("/sessions/turns", json=bad)
    assert r.status_code == 422

    # Extra/unknown field (additionalProperties: false).
    bad2 = make_c4()
    bad2["surprise"] = "nope"
    assert client.post("/sessions/turns", json=bad2).status_code == 422

    # Wrong const.
    bad3 = make_c4()
    bad3["contract"] = "C9"
    assert client.post("/sessions/turns", json=bad3).status_code == 422

    # Nested C3 broken (bad role enum) — exercises the $ref resolution.
    bad4 = make_c4()
    bad4["user_prompt"]["messages"][0]["role"] = "assistant"
    r4 = client.post("/sessions/turns", json=bad4)
    assert r4.status_code == 422
