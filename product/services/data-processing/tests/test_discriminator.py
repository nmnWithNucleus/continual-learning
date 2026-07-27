"""The within-chunk discriminator is now READABLE on C2 (2026-07-27, D18 follow-through).

The value has fed ``record_id`` since v0 but lived only inside the hash. C10's
day-log materialization keeps exactly one dialect per record by grouping on
``(chunk_id, content.kind, discriminator)`` — it cannot do that against a hash
input it cannot see. Surfacing it adds no new guarantee (the stage-graph executor
already rejects duplicate discriminators within a chunk); it only exposes an
invariant that was already enforced.

Two things must hold, and both are pinned here:

  * ABSENCE, not ``""``, is the 1:1 shape — same rule D17 used for the civil-time
    passthrough, so a 1:1 record is byte-identical to its pre-seam self;
  * ``record_id`` DOES NOT MOVE. The discriminator was always an id input and
    ``compute_record_id`` already skipped an empty one, so neither a 1:1 record nor
    a fan-out record re-keys. The golden hex literals below are the whole point of
    this module: they were computed from the v0 formula and must never change.
"""
from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from app import schemas
from app.models import C2Record
from app.pipeline import build_c2, compute_record_id
from app.processing.base import ProcessedContent, ProcessedUnit
from app.processing.processors import video as video_proc
from tests import fixtures
from .conftest import make_c1

# A synthetic (chunk_id, pipeline_version) pair used ONLY for the golden ids, so
# the pins survive any fixture or plugin-version churn.
_CHUNK = "chunk-disc-golden"
_PV = "pv-disc-golden"

# sha256(b"chunk-disc-golden\x00pv-disc-golden") — the v0 TWO-component id, i.e.
# what a 1:1 record hashed to before the discriminator was ever emitted.
_GOLDEN_1TO1 = "bf01f68c16ecd8bbf59a3328095a3ea7e8824c5b1d3f14c1cd30a1e9d00d3bd3"
# sha256(b"chunk-disc-golden\x00pv-disc-golden\x0007") — the three-component id a
# fan-out record already had, back when "07" was invisible.
_GOLDEN_FANOUT = "03e83048e6e4841c145c8e605f5b7ee46d5dff9600797be5ea72b219fcffad8b"


def _c1(**kw):
    """A bare C1 envelope for the pure build_c2 tests (no blob registration)."""
    class _NoStorage:
        def add_blob(self, *a, **k):  # pragma: no cover - never called
            raise AssertionError("pure test must not touch storage")

    return make_c1(_NoStorage(), register_blob=False, **kw)


def _unit(discriminator: str = "", kind: str = "caption") -> ProcessedUnit:
    return ProcessedUnit(
        content=ProcessedContent(kind=kind, text="x"), discriminator=discriminator
    )


# ---- THE INVARIANT: record_id must not move ----------------------------------

def test_golden_record_ids_are_unchanged_by_surfacing_the_field():
    """Byte-identical before and after, for BOTH cases. A change here means every
    already-persisted record forks on upgrade."""
    assert compute_record_id(_CHUNK, _PV) == _GOLDEN_1TO1
    assert compute_record_id(_CHUNK, _PV, "") == _GOLDEN_1TO1
    assert compute_record_id(_CHUNK, _PV, "07") == _GOLDEN_FANOUT


def test_built_record_carries_the_golden_id_for_a_1to1_record():
    c2 = build_c2(_c1(chunk_id=_CHUNK), _unit(""), _PV, "2026-07-27T10:00:00Z")
    assert c2["record_id"] == _GOLDEN_1TO1


def test_built_record_carries_the_golden_id_for_a_fanout_record():
    """The id a fan-out record ALREADY had — emitting the value must not fork it."""
    c2 = build_c2(_c1(chunk_id=_CHUNK), _unit("07"), _PV, "2026-07-27T10:00:00Z")
    assert c2["record_id"] == _GOLDEN_FANOUT
    assert c2["discriminator"] == "07"


def test_the_emitted_field_is_never_an_id_input_twice():
    """Independent restatement of the id formula: the emitted value and the hashed
    value are the SAME value, and the empty one is folded in nowhere."""
    for disc in ("", "0", "3:ocr", "translation", "injcap-w0_60"):
        digest = hashlib.sha256()
        digest.update(_CHUNK.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(_PV.encode("utf-8"))
        if disc:
            digest.update(b"\x00")
            digest.update(disc.encode("utf-8"))
        c2 = build_c2(_c1(chunk_id=_CHUNK), _unit(disc), _PV, "2026-07-27T10:00:00Z")
        assert c2["record_id"] == digest.hexdigest()
        assert c2.get("discriminator") == (disc or None)


# ---- Absence, not empty string -----------------------------------------------

def test_empty_discriminator_omits_the_key_entirely():
    """The 1:1 contract is ABSENCE. An emitted "" would be a new byte on every
    existing record and would make ``"discriminator" in rec`` a useless test."""
    c2 = build_c2(_c1(), _unit(""), _PV, "2026-07-27T10:00:00Z")
    assert "discriminator" not in c2


def test_1to1_ingest_emits_no_discriminator_key(client):
    """End to end through /ingest: audio/image/text are all 1:1."""
    for modality in ("audio", "image", "text"):
        client.fake_storage.record_posts.clear()
        resp = fixtures.register_and_post(client, modality)
        assert resp.status_code == 200, resp.text
        for c2 in client.fake_storage.record_posts:
            assert "discriminator" not in c2, modality


def test_key_order_places_discriminator_before_processed_at():
    """Cosmetic but deliberate: the emitted key order mirrors the frozen contract's
    property order, so a serialized C2 reads the way the schema does."""
    c2 = build_c2(_c1(), _unit("0"), _PV, "2026-07-27T10:00:00Z")
    keys = list(c2)
    assert keys[-3:] == ["pipeline_version", "discriminator", "processed_at"]


# ---- Fan-out: distinct AND readable ------------------------------------------

def test_video_fanout_emits_distinct_readable_discriminators(client):
    """The headline case. Three keyframe records off ONE chunk: before this seam a
    reader saw three ids and three captions and had to guess which was which."""
    resp = fixtures.register_and_post(client, "video")
    assert resp.status_code == 200, resp.text
    posts = client.fake_storage.record_posts
    assert len(posts) == fixtures.EXPECTED_RECORDS["video"] == 3

    discriminators = [c2["discriminator"] for c2 in posts]
    assert discriminators == ["0", "1", "2"]          # readable, in emit order
    assert len(set(discriminators)) == len(posts)      # and distinct

    c1 = fixtures.load_c1("video")
    pv = video_proc.PIPELINE_VERSION
    for c2 in posts:
        # Same chunk, same kind — the discriminator is the ONLY thing separating
        # them, which is exactly why C10 needs to read it.
        assert c2["source"]["chunk_id"] == c1["chunk_id"]
        assert c2["content"]["kind"] == "caption"
        # ...and it is the id input, still, unchanged.
        assert c2["record_id"] == compute_record_id(
            c1["chunk_id"], pv, c2["discriminator"]
        )
    assert len({c2["record_id"] for c2 in posts}) == len(posts)


def test_fanout_records_stay_schema_valid(client):
    resp = fixtures.register_and_post(client, "video")
    assert resp.status_code == 200
    for c2 in client.fake_storage.record_posts:
        assert schemas.validate_c2(c2) == []


def test_video_reingest_is_still_idempotent(client):
    """Surfacing an id input must not disturb the upsert key."""
    r1 = fixtures.register_and_post(client, "video")
    r2 = fixtures.register_and_post(client, "video")
    assert r1.json()["record_ids"] == r2.json()["record_ids"]


# ---- The frozen schema accepts both shapes -----------------------------------

def _valid_c2(**kw):
    return build_c2(_c1(), _unit(**kw), _PV, "2026-07-27T10:00:00Z")


def test_schema_validates_with_and_without_the_field():
    without = _valid_c2(discriminator="")
    with_it = _valid_c2(discriminator="3:ocr")
    assert "discriminator" not in without
    assert with_it["discriminator"] == "3:ocr"
    assert schemas.validate_c2(without) == []
    assert schemas.validate_c2(with_it) == []


def test_schema_still_accepts_an_explicit_empty_string():
    """The contract says 'absent or ""' — DP never emits "", but a producer that
    does must not be rejected by the gate."""
    rec = _valid_c2()
    rec["discriminator"] = ""
    assert schemas.validate_c2(rec) == []


def test_schema_enforces_the_128_char_ceiling():
    rec = _valid_c2()
    rec["discriminator"] = "d" * 128
    assert schemas.validate_c2(rec) == []
    rec["discriminator"] = "d" * 129
    assert schemas.validate_c2(rec) != []


def test_schema_rejects_a_non_string_discriminator():
    rec = _valid_c2()
    rec["discriminator"] = 7
    assert schemas.validate_c2(rec) != []


# ---- The pydantic mirror moves with the schema -------------------------------

def test_mirror_round_trips_the_field():
    """The D17 trap: schema gate passes, extra='forbid' mirror rejects, and the
    only thing standing between that and a 500 is this test."""
    rec = _valid_c2(discriminator="translation")
    model = C2Record.model_validate(rec)
    assert model.discriminator == "translation"
    assert model.model_dump(exclude_none=True)["discriminator"] == "translation"


def test_mirror_accepts_an_absent_discriminator_as_none():
    model = C2Record.model_validate(_valid_c2(discriminator=""))
    assert model.discriminator is None
    assert "discriminator" not in model.model_dump(exclude_none=True)


def test_mirror_enforces_the_same_ceiling_as_the_schema():
    rec = _valid_c2()
    rec["discriminator"] = "d" * 129
    with pytest.raises(ValidationError):
        C2Record.model_validate(rec)


def test_mirror_still_forbids_unknown_fields():
    """We widened C2 by exactly one field; we did not open the record up."""
    rec = _valid_c2()
    rec["discriminant"] = "typo"  # not the contract's name
    with pytest.raises(ValidationError):
        C2Record.model_validate(rec)
