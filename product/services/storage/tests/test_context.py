"""/context store (C2 v1): write->read round-trip + schema-validate, idempotent upsert,
time-range read ordering, per-user isolation, 404s, invalid-C2 -> 422.

The write path speaks C2 **v1** exclusively (founder ruling R1, 2026-08-06): the branch
validates against `c2_processed_record.v1.json` and its pydantic mirror. The v0 shape —
`content.kind`/`content.text`, `enrichments`, `discriminator`, `source.modality`,
`processed_at` — is rejected wholesale; the live worktree service keeps serving v0
until the Stage F cutover, and the OD-2 wipe means no stored v0 record survives into
this code's world.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app import schemas
from app.models import ProcessedRecord
from tests.conftest import make_c2, record_id_for, slot_acoustic, slot_asr, slot_ocr


def test_write_read_roundtrip(client):
    record = make_c2()
    # Fixture must itself be a valid C2 v1 (guards the test, not just the service).
    assert schemas.validate_c2(record) == []

    ack = client.post("/context/records", json=record)
    assert ack.status_code == 200, ack.text
    assert ack.json() == {"ok": True, "record_id": record["record_id"]}

    got = client.get(f"/context/records/{record['record_id']}")
    assert got.status_code == 200
    stored = got.json()
    # Exact round-trip: what we stored is exactly what we read back.
    assert stored == record
    # And it still schema-validates as a C2 v1 on the way out.
    assert schemas.validate_c2(stored) == []


def test_get_missing_record_404(client):
    resp = client.get("/context/records/does-not-exist")
    assert resp.status_code == 404


def test_idempotent_upsert_on_record_id(client):
    # record_id is deterministic on (chunk_id, pipeline_version): re-processing the same
    # chunk under the same pipeline_version is an idempotent upsert, not a duplicate.
    rid = record_id_for("chunk-X", "asr.v1-mock.v1")
    first = make_c2(record_id=rid, chunk_id="chunk-X", text="first pass transcript")
    second = make_c2(record_id=rid, chunk_id="chunk-X", text="reprocessed transcript")

    assert client.post("/context/records", json=first).status_code == 200
    assert client.post("/context/records", json=second).status_code == 200

    stored = client.get(f"/context/records/{rid}").json()
    assert stored["content"]["slots"]["asr"]["value"] == "reprocessed transcript"

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


def test_an_empty_slots_map_is_legal(client):
    """All-optional-failure under L7 ships a record whose dialect states what was
    attempted; forbidding {} would force a fabricated slot (Stage A ruling, endorsed)."""
    record = make_c2(slots={})
    assert schemas.validate_c2(record) == []
    assert client.post("/context/records", json=record).status_code == 200
    assert client.get(f"/context/records/{record['record_id']}").json() == record


def test_empty_string_slot_values_are_honest_claims_not_rejections(client):
    """L11: `asr.value: ""` (VAD silence) and `ocr.value: ""` (screen had no text) are
    ran-and-empty claims and must land verbatim."""
    record = make_c2(
        modality="video",
        slots={"ocr": slot_ocr(""), "acoustic": slot_acoustic([])},
    )
    assert client.post("/context/records", json=record).status_code == 200
    stored = client.get(f"/context/records/{record['record_id']}").json()
    assert stored["content"]["slots"]["ocr"]["value"] == ""
    assert stored["content"]["slots"]["acoustic"]["values"] == []


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

    # Bad root modality enum ("speech" is C3's token, not C2's "audio").
    bad4 = make_c2()
    bad4["modality"] = "speech"
    assert client.post("/context/records", json=bad4).status_code == 422

    # Transcript split `speaker` is required-nullable — dropping the key is invalid.
    bad5 = make_c2(slots={"transcript": {
        "version": "speaker_align.v1-mock.v1",
        "splits": [{"t_start": "2026-07-09T09:00:00Z", "t_end": "2026-07-09T09:00:01Z",
                    "value": "hi"}],
    }})
    assert client.post("/context/records", json=bad5).status_code == 422


def test_the_v0_shape_is_rejected_wholesale(client):
    """R1: no dual-version ingest. A well-formed v0 record — version "0", content.kind/
    text, enrichments, source.modality, processed_at — is a 422 on every axis it differs."""
    t = "2026-07-09T09:00:00Z"
    v0 = {
        "contract": "C2", "version": "0", "record_id": "rec-v0", "user_id": "u1",
        "source": {"device_id": "d", "stream_id": "s", "chunk_id": "c",
                   "blob_ref": "ab/cd/ef", "modality": "audio"},
        "t_start": t, "t_end": t,
        "content": {"kind": "transcript", "text": "hello", "language": "en",
                    "segments": [{"t_start": t, "t_end": t, "text": "hello",
                                  "speaker": None}]},
        "enrichments": {"speakers": [], "faces": [], "places": [], "objects": []},
        "pipeline_version": "asr-mock-v0",
        "processed_at": t,
    }
    res = client.post("/context/records", json=v0)
    assert res.status_code == 422
    assert res.json()["detail"]["error"] == "C2 schema validation failed"

    # And piecemeal: each v0-only concept fails closed on an otherwise-valid v1 record.
    for field, value in (
        ("discriminator", "3:ocr"),
        ("enrichments", {"speakers": [], "faces": [], "places": [], "objects": []}),
        ("processed_at", t),
    ):
        bad = make_c2()
        bad[field] = value
        assert client.post("/context/records", json=bad).status_code == 422, field
    bad = make_c2()
    bad["content"]["kind"] = "transcript"
    assert client.post("/context/records", json=bad).status_code == 422
    bad = make_c2()
    bad["source"]["modality"] = "audio"  # modality moved to the root in v1
    assert client.post("/context/records", json=bad).status_code == 422


def test_unknown_slot_names_fail_closed(client):
    """Adding a slot type is an additive contract edit made when its first producer
    ships; until then an unknown slot name is a 422, never silently stored."""
    record = make_c2(slots={"telepathy": {"version": "telepathy.v1-mock.v1",
                                          "value": "..."}})
    assert schemas.validate_c2(record) != []
    assert client.post("/context/records", json=record).status_code == 422


def test_record_id_shape_is_pinned(client):
    """64 lowercase hex, exactly — the length bounds close the trailing-newline trap the
    same way the c10 window_id bounds do."""
    for bad_rid in ("short", "R" * 64, record_id_for("c", "asr.v1-mock.v1") + "\n"):
        record = make_c2()
        record["record_id"] = bad_rid
        assert client.post("/context/records", json=record).status_code == 422, bad_rid


def test_pipeline_version_grammar_is_pinned(client):
    """The L4 grammar — and the Python-`$` trap: `pattern` alone admits a trailing
    newline, so storage's validate_c2 additionally fullmatches the contract's own
    regexes (the contract says enforcing implementations must)."""
    good = make_c2(pipeline_version="acoustic.v1-ast.v1+asr.v2-fw.v3.exp-b7")
    assert client.post("/context/records", json=good).status_code == 200

    for bad_pv in ("asr-mock-v0", "Asr.v1-mock.v1", "asr.v1-mock.v1\n"):
        record = make_c2()
        record["pipeline_version"] = bad_pv
        res = client.post("/context/records", json=record)
        assert res.status_code == 422, (bad_pv, res.text)


def test_slot_version_grammar_is_pinned(client):
    """Each slot's `version` carries the producing stage's dialect segment; same grammar,
    same fullmatch closure."""
    record = make_c2(slots={"asr": slot_asr("hi", version="asr.v1-mock.v1\n")})
    assert client.post("/context/records", json=record).status_code == 422


def test_a_non_finite_confidence_is_a_422_not_a_500(client):
    """Another Python-validator trap, closed in validate_c2 (review round): the json
    parser admits the non-standard NaN/Infinity literals, jsonschema's minimum/maximum
    are vacuously true for NaN — and the pydantic mirror then rejects what the schema
    gate passed, which is the two-gates-in-series 500 this service exists to avoid.
    Sent as raw wire bytes: `json.dumps` happily EMITS the NaN literal by default, so
    any stdlib-json client can produce this body."""
    import json as jsonlib
    for bad in (float("nan"), float("inf"), float("-inf")):
        record = make_c2(slots={"acoustic": slot_acoustic(["keyboard typing"],
                                                          confidence=bad)})
        res = client.post("/context/records", content=jsonlib.dumps(record),
                          headers={"Content-Type": "application/json"})
        assert res.status_code == 422, (bad, res.status_code)
        assert res.json()["detail"]["error"] == "C2 schema validation failed"


# ---- The mirror itself, alongside the frozen schema --------------------------


def test_mirror_and_schema_agree_on_the_valid_shapes():
    full = make_c2()
    empty = make_c2(slots={})
    for record in (full, empty):
        assert schemas.validate_c2(record) == []
        ProcessedRecord.model_validate(record)


def test_mirror_and_schema_agree_on_rejections():
    bad = make_c2()
    bad["content"]["kind"] = "transcript"
    assert schemas.validate_c2(bad) != []
    with pytest.raises(ValidationError):
        ProcessedRecord.model_validate(bad)

    bad2 = make_c2(slots={"telepathy": {"version": "t.v1-m.v1", "value": "x"}})
    assert schemas.validate_c2(bad2) != []
    with pytest.raises(ValidationError):
        ProcessedRecord.model_validate(bad2)


def test_the_d17_trio_rides_source_verbatim(client):
    """device_tz + device_utc_offset_minutes + device_clock (the v0 schema omitted the
    third; v1 closes the gap — Stage A ruling `device_clock` stays)."""
    record = make_c2()
    record["source"]["device_tz"] = "Asia/Tokyo"
    record["source"]["device_utc_offset_minutes"] = 540
    record["source"]["device_clock"] = "synced"
    assert client.post("/context/records", json=record).status_code == 200, \
        client.post("/context/records", json=record).text
    assert client.get(f"/context/records/{record['record_id']}").json() == record
