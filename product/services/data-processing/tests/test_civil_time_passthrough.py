"""D17 — civil-time context is carried C1 -> C2 verbatim, and DP adds nothing.

The contract is deliberately narrow: a timezone is a fact about where the user
physically was, knowable only at the capturing device at capture time. DP copies
it (like device_id/blob_ref) and derives, validates, normalizes and infers
NOTHING. These tests pin both halves — that it arrives, and that we don't invent
it when it doesn't.
"""
from __future__ import annotations

from .conftest import make_c1


def _records(client, envelope):
    res = client.post("/ingest", json=envelope)
    assert res.status_code == 200, res.text
    ids = res.json()["record_ids"]
    return [client.fake_storage.records[rid] for rid in ids]


def test_device_tz_and_offset_reach_c2_source_verbatim(client):
    c1 = make_c1(client.fake_storage)
    c1["device_tz"] = "America/Los_Angeles"
    c1["device_utc_offset_minutes"] = -420
    for rec in _records(client, c1):
        assert rec["source"]["device_tz"] == "America/Los_Angeles"
        assert rec["source"]["device_utc_offset_minutes"] == -420


def test_absent_civil_time_stays_absent_not_null(client):
    """DP must not invent a zone (the server's, UTC, or a geo guess). An ABSENT
    key is what makes downstream fall back to the user's profile home_tz; a null
    or a fabricated 'UTC' would be the silent-wrong-timezone bug D17 killed."""
    for rec in _records(client, make_c1(client.fake_storage)):
        assert "device_tz" not in rec["source"]
        assert "device_utc_offset_minutes" not in rec["source"]


def test_device_location_is_no_longer_dropped_on_the_floor(client):
    """C1 has always collected device_location; C2 used to discard it."""
    c1 = make_c1(client.fake_storage)
    c1["device_location"] = {"lat": 37.77, "lon": -122.42, "accuracy_m": 12.5}
    for rec in _records(client, c1):
        assert rec["source"]["device_location"]["lat"] == 37.77


def test_civil_time_does_not_change_record_id(client):
    """record_id is deterministic on (chunk_id, pipeline_version). Civil-time
    context is provenance, not a dialect input — adding it must NOT fork the
    record, or every existing record would re-key on upgrade."""
    plain = make_c1(client.fake_storage, chunk_id="chunk-same")
    res_a = client.post("/ingest", json=plain)
    assert res_a.status_code == 200, res_a.text

    stamped = make_c1(client.fake_storage, chunk_id="chunk-same")
    stamped["device_tz"] = "Asia/Tokyo"
    res_b = client.post("/ingest", json=stamped)
    assert res_b.status_code == 200, res_b.text

    assert res_a.json()["record_ids"] == res_b.json()["record_ids"]


def test_device_clock_reaches_c2_source_verbatim(client):
    """The D17 trio's third member. v0's C2 schema omitted device_clock (a
    standing prose/schema gap); v1 closes it — the flag that keeps clock skew
    detectable rides source{} verbatim like its siblings."""
    c1 = make_c1(client.fake_storage)
    c1["device_clock"] = "unsynced"
    for rec in _records(client, c1):
        assert rec["source"]["device_clock"] == "unsynced"


def test_absent_device_clock_stays_absent(client):
    for rec in _records(client, make_c1(client.fake_storage)):
        assert "device_clock" not in rec["source"]


def test_unknown_envelope_fields_are_still_rejected(client):
    """extra='forbid' still holds — we widened C1 by exactly two fields, we did
    not open the envelope up."""
    c1 = make_c1(client.fake_storage)
    c1["device_timezone"] = "America/Los_Angeles"  # not the contract's name
    assert client.post("/ingest", json=c1).status_code == 422
