"""C2's within-chunk ``discriminator`` survives the /context write path intact.

Surfaced 2026-07-27 (D18 follow-through). Storage does nothing clever with the
field — it persists C2 verbatim — but the write path has TWO gates in series
(``schemas.validate_c2`` then the ``_Strict`` ``ProcessedRecord`` mirror), and an
additive contract field that lands in only the first one passes validation and is
then rejected by pydantic as an UNHANDLED exception: a 500, not a 422 (main.py's
step 2 is a bare ``model_validate``). That is precisely the trap D17 fell into, so
these tests exercise the real HTTP surface, not the model in isolation.

C10's day-log materialization is the reader: it keeps exactly one dialect per
record by grouping on ``(chunk_id, content.kind, discriminator)``, so the value
has to come back out of ``/context`` byte-for-byte.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app import schemas
from app.models import ProcessedRecord
from tests.conftest import make_c2


def test_record_with_a_discriminator_round_trips_verbatim(client):
    record = make_c2(user_id="disc-user", chunk_id="chunk-fanout")
    record["discriminator"] = "3:ocr"
    assert schemas.validate_c2(record) == []

    res = client.post("/context/records", json=record)
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "record_id": record["record_id"]}

    got = client.get(f"/context/records/{record['record_id']}")
    assert got.status_code == 200
    assert got.json() == record                      # verbatim, whole record
    assert got.json()["discriminator"] == "3:ocr"
    assert schemas.validate_c2(got.json()) == []


def test_record_without_a_discriminator_is_unchanged(client):
    """The 1:1 case is ABSENCE. Storage must not materialize the key on read."""
    record = make_c2(user_id="disc-user", chunk_id="chunk-1to1")
    assert "discriminator" not in record

    assert client.post("/context/records", json=record).status_code == 200
    got = client.get(f"/context/records/{record['record_id']}").json()
    assert got == record
    assert "discriminator" not in got


def test_discriminator_is_not_stripped_or_500d_on_write(client):
    """The regression this module exists for: before the mirror was widened, this
    exact POST returned 500 (schema gate passed, ProcessedRecord rejected it)."""
    record = make_c2(user_id="disc-user", chunk_id="chunk-500-guard")
    record["discriminator"] = "translation"
    res = client.post("/context/records", json=record)
    assert res.status_code == 200, res.text


def test_range_read_returns_the_discriminator(client):
    """The list surface C10 reads through, not just the by-id fetch."""
    t = "2026-07-27T08:00:00Z"
    kept = []
    for disc in ("0", "1", "2"):
        rec = make_c2(
            user_id="disc-range", chunk_id="chunk-kf", record_id=f"rec-{disc}",
            t_start=t, t_end=t,
        )
        rec["discriminator"] = disc
        assert client.post("/context/records", json=rec).status_code == 200
        kept.append(rec)

    res = client.get(
        "/context/records",
        params={"user_id": "disc-range", "from": "2026-07-27T00:00:00Z",
                "to": "2026-07-28T00:00:00Z"},
    )
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == len(kept)
    # Equal t_start across all three -> the rowid tiebreak preserves insert order.
    assert [r["discriminator"] for r in rows] == ["0", "1", "2"]
    # Same chunk, same content.kind — the discriminator is the ONLY separator, which
    # is the whole reason C10 groups on it.
    assert len({r["source"]["chunk_id"] for r in rows}) == 1
    assert len({r["content"]["kind"] for r in rows}) == 1


def test_stored_json_keeps_the_field(client):
    """record_json is the verbatim column C10's materializer will read from."""
    record = make_c2(user_id="disc-user", chunk_id="chunk-column")
    record["discriminator"] = "injcap-w0_60"
    assert client.post("/context/records", json=record).status_code == 200

    store = client.app.state.store
    with store._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM context_records WHERE record_id = ?",
            (record["record_id"],),
        ).fetchone()
    assert json.loads(row["record_json"])["discriminator"] == "injcap-w0_60"


def test_reprocess_upsert_can_move_the_discriminator(client):
    """Storage stays dumb: it is DP that owns the id<->discriminator relationship.
    A same-record_id re-post overwrites verbatim, like every other C2 field."""
    record = make_c2(user_id="disc-user", chunk_id="chunk-upsert", record_id="rec-fixed")
    record["discriminator"] = "0"
    assert client.post("/context/records", json=record).status_code == 200

    record["discriminator"] = "0:ocr"
    assert client.post("/context/records", json=record).status_code == 200
    got = client.get("/context/records/rec-fixed").json()
    assert got["discriminator"] == "0:ocr"


# ---- The mirror itself, alongside the frozen schema --------------------------

def test_mirror_and_schema_agree_on_both_shapes():
    without = make_c2()
    with_it = make_c2()
    with_it["discriminator"] = "3:ocr"

    assert schemas.validate_c2(without) == []
    assert schemas.validate_c2(with_it) == []
    assert ProcessedRecord.model_validate(without).discriminator is None
    assert ProcessedRecord.model_validate(with_it).discriminator == "3:ocr"


def test_mirror_and_schema_agree_on_the_128_char_ceiling():
    rec = make_c2()
    rec["discriminator"] = "d" * 128
    assert schemas.validate_c2(rec) == []
    assert ProcessedRecord.model_validate(rec).discriminator == "d" * 128

    rec["discriminator"] = "d" * 129
    assert schemas.validate_c2(rec) != []
    with pytest.raises(ValidationError):
        ProcessedRecord.model_validate(rec)


def test_an_oversized_discriminator_is_a_422_not_a_500(client):
    """Both gates reject it, and the schema gate — which returns a structured 422 —
    is the one that runs first, so the mirror never gets to raise."""
    record = make_c2(user_id="disc-user", chunk_id="chunk-toolong")
    record["discriminator"] = "d" * 129
    res = client.post("/context/records", json=record)
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["error"] == "C2 schema validation failed"


def test_unknown_neighbouring_fields_are_still_rejected(client):
    """We widened C2 by exactly one field; extra='forbid' still holds."""
    record = make_c2(user_id="disc-user", chunk_id="chunk-typo")
    record["discriminant"] = "typo"  # not the contract's name
    assert client.post("/context/records", json=record).status_code == 422
    with pytest.raises(ValidationError):
        ProcessedRecord.model_validate(record)


def test_discriminator_does_not_belong_to_source(client):
    """It is a TOP-LEVEL C2 field. Nesting it under source would pass neither gate,
    and would put it on the wrong side of the provenance/identity line."""
    record = make_c2(user_id="disc-user", chunk_id="chunk-misplaced")
    record["source"]["discriminator"] = "0"
    assert schemas.validate_c2(record) != []
    assert client.post("/context/records", json=record).status_code == 422
