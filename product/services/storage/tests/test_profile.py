"""D18 / C12 — the per-user profile, and the one rule it exists to enforce.

`home_tz` is POLICY ("when is this user's night?"), not the per-record FACT C2 carries in
`source.device_tz`. THERE IS NO SERVER-SIDE DEFAULT TIMEZONE ANYWHERE IN THE SYSTEM (D17),
so an absent profile is a 404 and never a fallback to UTC; and `home_tz` is DECLARED, NOT
INFERRED (corrected 2026-07-27), so storage never writes it on its own initiative — not
from a device-reported zone, not from anything.

The schema at ../../contracts/c12_user_profile.v0.json is the authority for shape; tzdata
is the authority for whether a zone exists. Both gates are pinned here.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from app import schemas
from app.db import Store
from tests.conftest import make_c2


@pytest.fixture()
def store(client) -> Store:
    """The same DB the `client` fixture just pointed the app at (declares `client` for
    ordering — the env vars only exist because that fixture set them)."""
    return Store(path=os.environ["STORAGE_DB_PATH"],
                 raw_root=os.environ["STORAGE_RAW_DIR"])


# --- absence is a 404, never a default -----------------------------------------


def test_a_missing_profile_is_a_404(client):
    got = client.get("/users/nobody/profile")
    assert got.status_code == 404


def test_a_missing_profile_serves_no_timezone_at_all(client):
    """The failure mode this contract exists to prevent: a 200 with a plausible-looking
    default. A user with no home_tz is not schedulable — an operational alert, never a
    silent skip."""
    body = client.get("/users/nobody/profile").json()
    assert "home_tz" not in body            # a 404 detail, not a half-filled profile
    assert schemas.validate_c12(body) != []  # and not a C12 by any reading


def test_profiles_are_per_user(client):
    client.put("/users/u-a/profile", json={"home_tz": "Asia/Tokyo"})
    assert client.get("/users/u-a/profile").status_code == 200
    assert client.get("/users/u-b/profile").status_code == 404


# --- the write path -------------------------------------------------------------


def test_write_then_read_round_trips_a_valid_c12(client):
    written = client.put("/users/u1/profile", json={"home_tz": "America/Los_Angeles"})
    assert written.status_code == 200
    body = written.json()
    assert schemas.validate_c12(body) == []
    assert body["contract"] == "C12" and body["version"] == "0"
    assert body["user_id"] == "u1"
    assert body["home_tz"] == "America/Los_Angeles"

    got = client.get("/users/u1/profile")
    assert got.status_code == 200
    assert got.json() == body
    assert schemas.validate_c12(got.json()) == []


def test_the_first_write_mints_version_1(client):
    body = client.put("/users/u1/profile", json={"home_tz": "Europe/Berlin"}).json()
    assert body["profile_version"] == 1


def test_every_write_bumps_the_version_even_an_unchanged_one(client):
    """The contract calls profile_version 'a monotone counter, bumped on every write'.
    A reader that cached a fire time needs to see that policy was TOUCHED, not merely
    that it changed value."""
    first = client.put("/users/u1/profile", json={"home_tz": "Europe/Berlin"}).json()
    same = client.put("/users/u1/profile", json={"home_tz": "Europe/Berlin"}).json()
    changed = client.put("/users/u1/profile", json={"home_tz": "Asia/Tokyo"}).json()
    assert [first["profile_version"], same["profile_version"], changed["profile_version"]] == [1, 2, 3]
    assert changed["home_tz"] == "Asia/Tokyo"


def test_a_write_sets_updated_at(client, store):
    body = client.put("/users/u1/profile", json={"home_tz": "UTC"}).json()
    assert body["updated_at"].endswith("Z")
    with store._connect() as conn:
        row = conn.execute(
            "SELECT updated_at FROM user_profiles WHERE user_id = 'u1'"
        ).fetchone()
    assert row["updated_at"] == body["updated_at"]


# --- the two gates --------------------------------------------------------------


@pytest.mark.parametrize("abbreviation", ["PST", "MST", "EST", "CET", "GMT"])
def test_an_abbreviation_is_a_400(client, abbreviation):
    """PST/MST are ambiguous and DST-sensitive (D17). The contract's pattern requires a
    region/city form, which structurally excludes them."""
    got = client.put("/users/u1/profile", json={"home_tz": abbreviation})
    assert got.status_code == 400


def test_a_pattern_legal_but_nonexistent_zone_is_a_400(client):
    """THE point of the second gate. 'Not/AZone' passes the contract's pattern — the
    schema description says so itself ('THE PATTERN IS A CHEAP SHAPE GATE, NOT THE
    AUTHORITY') — and only tzdata resolution can reject it."""
    probe = {"contract": "C12", "version": "0", "user_id": "u1",
             "home_tz": "Not/AZone", "profile_version": 1,
             "updated_at": "2026-07-27T00:00:00Z"}
    assert schemas.validate_c12(probe) == []          # the shape gate lets it through
    got = client.put("/users/u1/profile", json={"home_tz": "Not/AZone"})
    assert got.status_code == 400                      # tzdata does not
    assert got.json()["detail"]["error"] == "unknown IANA timezone"


def test_bare_utc_is_admitted(client):
    """UTC is a real, unambiguous, DST-free zone id. It is also the shape a lazy default
    takes — but the system has no default, so a profile that says UTC is one somebody
    chose."""
    got = client.put("/users/u1/profile", json={"home_tz": "UTC"})
    assert got.status_code == 200
    assert got.json()["home_tz"] == "UTC"


def test_a_rejected_zone_leaves_the_stored_profile_untouched(client):
    client.put("/users/u1/profile", json={"home_tz": "Asia/Tokyo"})
    assert client.put("/users/u1/profile", json={"home_tz": "PST"}).status_code == 400
    body = client.get("/users/u1/profile").json()
    assert body["home_tz"] == "Asia/Tokyo"
    assert body["profile_version"] == 1  # the refused write never got a version


@pytest.mark.parametrize("body", [
    {},                                          # no home_tz
    {"home_tz": ""},                             # empty
    {"tz": "Asia/Tokyo"},                        # wrong field name
    {"home_tz": "Asia/Tokyo", "profile_version": 99},   # storage-minted, not settable
    {"home_tz": "Asia/Tokyo", "updated_at": "2020-01-01T00:00:00Z"},
])
def test_a_malformed_write_body_is_a_422(client, body):
    """422 = the request was malformed; 400 = the timezone was wrong. Keeping those
    apart is what lets a caller tell 'I sent nonsense' from 'that zone does not exist'.
    profile_version and updated_at are storage-minted: a counter a client could set is
    not monotone."""
    assert client.put("/users/u1/profile", json=body).status_code == 422


# --- declared, not inferred ------------------------------------------------------


def test_storage_never_seeds_home_tz_from_a_device_reported_zone(client):
    """The correction of 2026-07-27, and the whole reason this test module exists.
    D18's first draft had storage auto-seed home_tz from the first device-reported
    device_tz. It does not: a guess is never stored as though it were an answer."""
    record = make_c2(user_id="u-traveller")
    record["source"]["device_tz"] = "Asia/Tokyo"
    record["source"]["device_utc_offset_minutes"] = 540
    assert client.post("/context/records", json=record).status_code == 200

    assert client.get("/users/u-traveller/profile").status_code == 404


def test_home_tz_does_not_move_when_the_user_travels(client, store):
    """A week in Tokyo changes every record's device_tz and changes NOTHING here, so the
    consolidation boundary stays put instead of jumping 9 h and producing a 15 h night
    followed by a 33 h one. That is the FACT/POLICY split working as designed."""
    client.put("/users/u1/profile", json={"home_tz": "America/Los_Angeles"})
    for zone, offset in [("Asia/Tokyo", 540), ("Asia/Tokyo", 540), ("Europe/Berlin", 120)]:
        record = make_c2(user_id="u1")
        record["source"]["device_tz"] = zone
        record["source"]["device_utc_offset_minutes"] = offset
        client.post("/context/records", json=record)

    body = client.get("/users/u1/profile").json()
    assert body["home_tz"] == "America/Los_Angeles"
    assert body["profile_version"] == 1  # untouched: no write happened


def test_the_profile_write_is_the_only_writer_of_home_tz(client, store):
    """Structural check: nothing but the profile write ever inserts into user_profiles.
    Drive the learn-loop write path — the one place a zone enters the service — and
    assert the table is still empty."""
    for zone in ("Europe/Berlin", "Asia/Tokyo", "America/Los_Angeles"):
        record = make_c2(user_id="u1")
        record["source"]["device_tz"] = zone
        assert client.post("/context/records", json=record).status_code == 200

    with store._connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM user_profiles").fetchone()["n"]
    assert count == 0


# --- migration -------------------------------------------------------------------


def test_migration_adds_the_profile_table_to_a_preexisting_db(tmp_path):
    """A live DB predates D18. New TABLES are handled by _SCHEMA alone (CREATE TABLE IF
    NOT EXISTS runs on a live DB); the additive-column list is only for columns on tables
    that already exist. Pin that the table actually appears."""
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:  # the post-D17 / pre-D18 shape
        conn.execute(
            "CREATE TABLE context_records ("
            " record_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,"
            " t_start TEXT NOT NULL, t_end TEXT, chunk_id TEXT,"
            " pipeline_version TEXT, ingest_time TEXT NOT NULL,"
            " device_tz TEXT, device_utc_offset_minutes INTEGER,"
            " record_json TEXT NOT NULL)"
        )

    store = Store(path=str(db), raw_root=str(tmp_path / "raw"))
    with store._connect() as conn:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "user_profiles" in tables

    body = store.put_profile("u1", "Asia/Tokyo")
    assert body["home_tz"] == "Asia/Tokyo" and body["profile_version"] == 1
