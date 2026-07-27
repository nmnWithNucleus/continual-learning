"""D18 — the training-window ledger over the `[last_trained_t, now-delta)` watermark.

Four rules carry the whole design and each has its own test below:

  1. OPEN IS IDEMPOTENT and BOUNDS ARE IMMUTABLE. Continuum's crash-safe journal is
     keyed by window_id, so a retry that recomputed `now` would mint a fresh id, a fresh
     journal, and therefore a full re-train, a second C5 entry and a second reservoir
     admission.
  2. THE WATERMARK ADVANCES IFF THE CYCLE PUBLISHED. gate_failed, frozen,
     skipped_no_data and crashed all leave it exactly where it was, so the next window
     is a strict SUPERSET of the failed one. `skipped_no_data` in particular does NOT
     advance — that was corrected on 2026-07-27.
  3. A WINDOW'S END MUST BE STRICTLY GREATER than every prior window end for that user.
     This is what makes a second-granularity id collision impossible by construction
     rather than by luck.
  4. MEMBERSHIP IS ON THE INGEST AXIS. A never-trained user's first window starts at
     their earliest ingest_time, not at any event time and not at any local midnight.

Time is injected (`now=` / `delta_seconds=`) for everything the wall clock would
otherwise make flaky; the HTTP surface tests use the real clock and backdated records.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import schemas
from app.db import Store, WindowOutcomeConflict, WindowRefused
from app.window_id import mint_window_id, validate_window_id
from tests.conftest import make_c2

UTC = timezone.utc
NON_ADVANCING = ["gate_failed", "frozen", "skipped_no_data", "crashed"]


@pytest.fixture()
def store(client) -> Store:
    """The same DB the `client` fixture just pointed the app at (declares `client` for
    ordering — the env vars only exist because that fixture set them)."""
    return Store(path=os.environ["STORAGE_DB_PATH"],
                 raw_root=os.environ["STORAGE_RAW_DIR"])


def _at(year, month, day, hour=0, minute=0, second=0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ago(**kwargs) -> str:
    """A timestamp relative to the REAL clock, for tests that go through HTTP (where
    `now` is not injectable). Keeps them clock-agnostic instead of pinning a date."""
    return _stamp(datetime.now(UTC) - timedelta(**kwargs))


def _land(store: Store, user_id: str, ingest_time: str, **kwargs) -> dict:
    """Land a C2 record and force its storage-assigned ingest_time.

    ingest_time is minted by storage at write and is deliberately not a C2 field, so a
    test that needs a deterministic watermark floor sets it on the column directly —
    the same 'reach into the columns' idiom tests/test_civil_time.py uses.
    """
    record = make_c2(user_id=user_id, **kwargs)
    store.put_context(record)
    with store._connect() as conn:
        conn.execute(
            "UPDATE context_records SET ingest_time = ? WHERE record_id = ?",
            (ingest_time, record["record_id"]),
        )
        conn.commit()
    return record


# --- rule 4: the window starts on the ingest axis --------------------------------


def test_a_never_trained_users_window_starts_at_their_earliest_ingest_time(store):
    _land(store, "u1", "2026-07-20T04:00:00Z")
    _land(store, "u1", "2026-07-19T09:30:00Z")   # earlier ingest, landed second
    _land(store, "u1", "2026-07-21T23:00:00Z")

    window = store.open_training_window("u1", now=_at(2026, 7, 22, 4, 0, 0))
    assert window["t_start"] == "2026-07-19T09:30:00Z"
    assert store.last_trained_t("u1") is None     # opening trains nothing


def test_the_window_end_is_now_minus_delta(store):
    _land(store, "u1", "2026-07-20T04:00:00Z")
    window = store.open_training_window(
        "u1", now=_at(2026, 7, 22, 4, 0, 0), delta_seconds=60
    )
    assert window["t_end"] == "2026-07-22T03:59:00Z"


def test_delta_is_configurable(client, store, monkeypatch):
    """Watermark lag is service config, not a per-request knob a caller could shrink to
    zero — so it moves via the environment and the route never accepts it."""
    monkeypatch.setenv("STORAGE_WINDOW_DELTA_SECONDS", "3600")
    _land(store, "u1", _ago(days=3))
    window = client.post("/training/windows", json={"user_id": "u1"}).json()
    lag = datetime.now(UTC) - datetime.strptime(window["t_end"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert timedelta(seconds=3600) <= lag < timedelta(seconds=3660)


def test_the_window_id_is_minted_from_its_own_end(store):
    _land(store, "u1", "2026-07-20T04:00:00Z")
    window = store.open_training_window(
        "u1", now=_at(2026, 7, 22, 4, 0, 0), delta_seconds=60
    )
    assert window["window_id"] == "w20260722T035900Z"
    assert window["window_id"] == mint_window_id(_at(2026, 7, 22, 3, 59, 0))
    assert validate_window_id(window["window_id"])


def test_a_naive_now_is_refused_at_the_ledger_entry_point(store):
    """`now` must be timezone-aware, and the ledger — not the minter — is where that is
    enforced.

    `.astimezone()` on a NAIVE datetime does not assume UTC: it reads the value as the
    SERVER'S LOCAL ZONE. So by the time `mint_window_id` (which refuses naive input) sees
    it, the value is already aware, and its guard cannot fire on the very input it names.
    Proved under TZ=Asia/Tokyo, where a naive `now` minted a window nine hours early —
    which then mis-orders every string comparison continuum makes over that id.
    """
    _land(store, "u1", "2026-07-20T04:00:00Z")

    with pytest.raises(ValueError, match="timezone-aware"):
        store.open_training_window("u1", now=datetime(2026, 7, 22, 4, 0, 0),
                                   delta_seconds=60)
    assert store.list_windows("u1") == []          # refused BEFORE anything was written

    # The same instant, said properly, is accepted — and so is any other aware zone, which
    # is NORMALIZED to UTC rather than refused: the rule is about the zone being stated,
    # not about it being UTC. 13:00 in Tokyo is the same instant as 04:00 UTC, and both
    # mint the same id.
    window = store.open_training_window("u1", now=_at(2026, 7, 22, 4, 0, 0),
                                        delta_seconds=60)
    assert window["window_id"] == "w20260722T035900Z"

    _land(store, "u2", "2026-07-20T04:00:00Z")
    tokyo = store.open_training_window(
        "u2",
        now=datetime(2026, 7, 22, 13, 0, 0, tzinfo=timezone(timedelta(hours=9))),
        delta_seconds=60,
    )
    assert tokyo["window_id"] == window["window_id"]


def test_a_user_with_no_records_at_all_cannot_open_a_window(client, store):
    """Refused, not invented. There is no range to name, so the cycle simply does not
    run for this user — visible, not silent."""
    with pytest.raises(WindowRefused):
        store.open_training_window("ghost", now=_at(2026, 7, 22, 4, 0, 0))
    got = client.post("/training/windows", json={"user_id": "ghost"})
    assert got.status_code == 409
    assert got.json()["detail"]["error"] == "no ingest history"


# --- rule 1: idempotent open, immutable bounds -----------------------------------


def test_open_is_idempotent_over_http(client, store):
    _land(store, "u1", _ago(days=3))
    first = client.post("/training/windows", json={"user_id": "u1"})
    second = client.post("/training/windows", json={"user_id": "u1"})
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()

    with store._connect() as conn:
        rows = conn.execute("SELECT COUNT(*) AS n FROM training_windows").fetchone()["n"]
    assert rows == 1


def test_bounds_are_immutable_once_opened(store):
    """The retry arrives a DAY later on a clock that has moved. The window it gets back
    is the one it already had — same id, same t_end — because re-minting is exactly the
    failure that would force a full re-train."""
    _land(store, "u1", "2026-07-20T04:00:00Z")
    first = store.open_training_window("u1", now=_at(2026, 7, 22, 4, 0, 0))
    later = store.open_training_window("u1", now=_at(2026, 7, 23, 4, 0, 0))
    assert later == first


def test_records_landing_after_the_open_do_not_move_the_bounds(store):
    """The window is OPENED, not COMPUTED. New ingest joins the window's range on read;
    it does not re-derive the range."""
    _land(store, "u1", "2026-07-20T04:00:00Z")
    first = store.open_training_window("u1", now=_at(2026, 7, 22, 4, 0, 0))
    _land(store, "u1", "2026-07-19T00:00:00Z")   # an EARLIER ingest lands late
    again = store.open_training_window("u1", now=_at(2026, 7, 22, 5, 0, 0))
    assert again == first


def test_the_db_refuses_a_second_open_window_for_one_user(store):
    """Belt and braces under the read-then-write in open_training_window: a partial
    unique index makes two open rows per user unrepresentable, not merely unlikely."""
    _land(store, "u1", "2026-07-20T04:00:00Z")
    store.open_training_window("u1", now=_at(2026, 7, 22, 4, 0, 0))
    with pytest.raises(sqlite3.IntegrityError):
        with store._connect() as conn:
            conn.execute(
                "INSERT INTO training_windows "
                "(window_id, user_id, t_start, t_end, state, outcome, opened_at, closed_at) "
                "VALUES ('w20260723T040000Z','u1','x','y','open',NULL,'z',NULL)"
            )
            conn.commit()


def test_windows_are_per_user(store):
    _land(store, "u-a", "2026-07-20T04:00:00Z")
    _land(store, "u-b", "2026-07-21T04:00:00Z")
    a = store.open_training_window("u-a", now=_at(2026, 7, 22, 4, 0, 0))
    b = store.open_training_window("u-b", now=_at(2026, 7, 22, 4, 0, 1))
    assert a["window_id"] != b["window_id"]
    assert a["t_start"] == "2026-07-20T04:00:00Z"
    assert b["t_start"] == "2026-07-21T04:00:00Z"
    assert [w["window_id"] for w in store.list_windows("u-a")] == [a["window_id"]]


# --- the window_id is a PER-USER token -------------------------------------------


def test_two_users_can_open_in_the_same_second(store):
    """The id is derived from an instant, so a nightly FLEET run legitimately mints the
    same string for two users within one second. That is normal, not a collision — the
    ledger is keyed (user_id, window_id), not window_id alone."""
    _land(store, "u-a", "2026-07-20T04:00:00Z")
    _land(store, "u-b", "2026-07-20T04:00:00Z")
    same_moment = _at(2026, 7, 22, 4, 0, 0)
    a = store.open_training_window("u-a", now=same_moment)
    b = store.open_training_window("u-b", now=same_moment)

    assert a["window_id"] == b["window_id"]      # one token, two users
    assert a["user_id"] == "u-a" and b["user_id"] == "u-b"
    assert store.get_window("u-a", a["window_id"])["user_id"] == "u-a"
    assert store.get_window("u-b", a["window_id"])["user_id"] == "u-b"


def test_closing_one_users_window_does_not_touch_the_others(store):
    _land(store, "u-a", "2026-07-20T04:00:00Z")
    _land(store, "u-b", "2026-07-20T04:00:00Z")
    same_moment = _at(2026, 7, 22, 4, 0, 0)
    a = store.open_training_window("u-a", now=same_moment)
    store.open_training_window("u-b", now=same_moment)

    store.close_training_window("u-a", a["window_id"], "published")
    assert store.last_trained_t("u-a") == a["t_end"]
    assert store.last_trained_t("u-b") is None
    assert store.get_window("u-b", a["window_id"])["state"] == "open"


def test_a_cross_user_close_fails_closed(client, store):
    """Per-user isolation is a property of the layer. Guessing another user's window_id
    (which is guessable — it is a second) must 404, not close their window and not
    reveal that it exists."""
    _land(store, "u-a", _ago(days=3))
    window = client.post("/training/windows", json={"user_id": "u-a"}).json()
    got = client.post(f"/training/windows/{window['window_id']}/close",
                      json={"user_id": "u-b", "outcome": "published"})
    assert got.status_code == 404
    assert store.get_window("u-a", window["window_id"])["state"] == "open"
    assert store.last_trained_t("u-a") is None


def test_the_close_body_requires_a_user_id(client, store):
    _land(store, "u1", _ago(days=3))
    window = client.post("/training/windows", json={"user_id": "u1"}).json()
    got = client.post(f"/training/windows/{window['window_id']}/close",
                      json={"outcome": "published"})
    assert got.status_code == 422


# --- rule 2: the watermark advances IFF published --------------------------------


def test_publishing_advances_the_watermark(store):
    _land(store, "u1", "2026-07-20T04:00:00Z")
    first = store.open_training_window("u1", now=_at(2026, 7, 22, 4, 0, 0))
    store.close_training_window("u1", first["window_id"], "published")
    assert store.last_trained_t("u1") == first["t_end"]

    second = store.open_training_window("u1", now=_at(2026, 7, 23, 4, 0, 0))
    assert second["t_start"] == first["t_end"]      # exactly where the last one stopped
    assert second["window_id"] != first["window_id"]


@pytest.mark.parametrize("outcome", NON_ADVANCING)
def test_every_other_outcome_leaves_the_watermark_alone(store, outcome):
    _land(store, "u1", "2026-07-20T04:00:00Z")
    first = store.open_training_window("u1", now=_at(2026, 7, 22, 4, 0, 0))
    store.close_training_window("u1", first["window_id"], outcome)
    assert store.last_trained_t("u1") is None


@pytest.mark.parametrize("outcome", NON_ADVANCING)
def test_the_next_window_is_a_strict_superset_of_a_failed_one(store, outcome):
    """The design-of-record's failed-day merge, obtained structurally rather than by
    continuum's `_UserState.debt` bookkeeping: same start, later end."""
    _land(store, "u1", "2026-07-20T04:00:00Z")
    failed = store.open_training_window("u1", now=_at(2026, 7, 22, 4, 0, 0))
    store.close_training_window("u1", failed["window_id"], outcome)
    nxt = store.open_training_window("u1", now=_at(2026, 7, 23, 4, 0, 0))

    assert nxt["t_start"] == failed["t_start"]
    assert nxt["t_end"] > failed["t_end"]
    assert nxt["window_id"] > failed["window_id"]   # order is the string order


def test_skipped_no_data_does_not_advance_the_watermark(store):
    """Called out on its own because D18's FIRST draft advanced on it; refined
    2026-07-27. A below-floor night just doesn't advance, so material accumulates until
    a run is worth it — which is what makes the min-data floor nearly free."""
    _land(store, "u1", "2026-07-20T04:00:00Z")
    first = store.open_training_window("u1", now=_at(2026, 7, 22, 4, 0, 0))
    store.close_training_window("u1", first["window_id"], "skipped_no_data")

    assert store.last_trained_t("u1") is None
    second = store.open_training_window("u1", now=_at(2026, 7, 23, 4, 0, 0))
    assert second["t_start"] == first["t_start"]


def test_a_run_of_failures_then_a_publish_trains_the_whole_backlog(store):
    _land(store, "u1", "2026-07-20T04:00:00Z")
    for day, outcome in [(22, "gate_failed"), (23, "gate_failed"), (24, "skipped_no_data")]:
        win = store.open_training_window("u1", now=_at(2026, 7, day, 4, 0, 0))
        assert win["t_start"] == "2026-07-20T04:00:00Z"   # never moved
        store.close_training_window("u1", win["window_id"], outcome)

    final = store.open_training_window("u1", now=_at(2026, 7, 25, 4, 0, 0))
    assert final["t_start"] == "2026-07-20T04:00:00Z"
    store.close_training_window("u1", final["window_id"], "published")
    assert store.last_trained_t("u1") == final["t_end"]

    after = store.open_training_window("u1", now=_at(2026, 7, 26, 4, 0, 0))
    assert after["t_start"] == final["t_end"]


def test_closing_over_http_advances_the_watermark(client, store):
    _land(store, "u1", _ago(days=3))
    window = client.post("/training/windows", json={"user_id": "u1"}).json()
    closed = client.post(f"/training/windows/{window['window_id']}/close",
                         json={"user_id": "u1", "outcome": "published"})
    assert closed.status_code == 200
    body = closed.json()
    assert body["state"] == "consolidated"
    assert body["outcome"] == "published"
    assert body["closed_at"] is not None
    assert body["t_start"] == window["t_start"] and body["t_end"] == window["t_end"]
    assert store.last_trained_t("u1") == window["t_end"]


# --- rule 3: ends are strictly increasing, so ids cannot collide ------------------


def test_a_window_ending_where_the_last_one_ended_is_refused(store):
    """The collision guard. Two windows in the same second would share a window_id, and
    an id collision corrupts the journal, the reservoir and C5 lineage at once."""
    _land(store, "u1", "2026-07-20T04:00:00Z")
    first = store.open_training_window("u1", now=_at(2026, 7, 22, 4, 0, 0))
    store.close_training_window("u1", first["window_id"], "published")
    with pytest.raises(WindowRefused) as exc:
        store.open_training_window("u1", now=_at(2026, 7, 22, 4, 0, 0))
    assert exc.value.detail["t_end"] == first["t_end"]


def test_the_guard_also_covers_a_failed_window(store):
    """A gate_failed window's id occupies the id namespace too, so guarding only
    against `last_trained_t` (which a failure deliberately does not move) would let an
    immediate re-drive mint a colliding id. This is why the implemented guard is the
    strictly stronger 'greater than every prior end'."""
    _land(store, "u1", "2026-07-20T04:00:00Z")
    first = store.open_training_window("u1", now=_at(2026, 7, 22, 4, 0, 0))
    store.close_training_window("u1", first["window_id"], "gate_failed")
    assert store.last_trained_t("u1") is None      # the weaker guard would allow this
    with pytest.raises(WindowRefused):
        store.open_training_window("u1", now=_at(2026, 7, 22, 4, 0, 0))

    # One second later is fine, and mints a distinct id.
    nxt = store.open_training_window("u1", now=_at(2026, 7, 22, 4, 0, 1))
    assert nxt["window_id"] != first["window_id"]


def test_a_window_that_would_be_empty_is_refused(store):
    """t_end == t_start is not 'a window with no records', it is not a window."""
    _land(store, "u1", "2026-07-20T04:00:00Z")
    with pytest.raises(WindowRefused):
        store.open_training_window("u1", now=_at(2026, 7, 20, 4, 0, 0), delta_seconds=0)
    with pytest.raises(WindowRefused):   # and t_end < t_start
        store.open_training_window("u1", now=_at(2026, 7, 20, 4, 0, 30), delta_seconds=60)


def test_a_refused_open_writes_nothing(client, store):
    _land(store, "u1", "2026-07-20T04:00:00Z")
    with pytest.raises(WindowRefused):
        store.open_training_window("u1", now=_at(2026, 7, 20, 4, 0, 0), delta_seconds=0)
    assert store.list_windows("u1") == []


def test_every_window_id_in_a_users_ledger_is_unique_and_ordered(store):
    _land(store, "u1", "2026-07-20T04:00:00Z")
    ids = []
    for day in range(21, 27):
        win = store.open_training_window("u1", now=_at(2026, 7, day, 4, 0, 0))
        ids.append(win["window_id"])
        store.close_training_window("u1", win["window_id"], "published")
    assert len(set(ids)) == len(ids)
    assert ids == sorted(ids)


# --- close: 404 / 422 / conflict / retry ------------------------------------------


def test_closing_an_unknown_window_is_a_404(client):
    got = client.post("/training/windows/w20260722T040000Z/close",
                      json={"user_id": "u1", "outcome": "published"})
    assert got.status_code == 404


def test_closing_a_malformed_window_id_is_a_422(client):
    """The one validator, wired into the request path. `w2026-07-21` is the PRE-D18
    format — it can never name a row, so it is rejected on shape rather than 404-ing."""
    got = client.post("/training/windows/w2026-07-21/close", json={"user_id": "u1", "outcome": "published"})
    assert got.status_code == 422
    assert got.json()["detail"]["error"] == "malformed window_id"


def test_an_unknown_outcome_is_a_422(client, store):
    """Never a silent non-advance, and never a silent advance."""
    _land(store, "u1", _ago(days=3))
    window = client.post("/training/windows", json={"user_id": "u1"}).json()
    got = client.post(f"/training/windows/{window['window_id']}/close",
                      json={"user_id": "u1", "outcome": "succeeded"})
    assert got.status_code == 422
    assert store.get_window("u1", window["window_id"])["state"] == "open"


def test_re_closing_with_the_same_outcome_is_a_retry(client, store):
    _land(store, "u1", _ago(days=3))
    window = client.post("/training/windows", json={"user_id": "u1"}).json()
    first = client.post(f"/training/windows/{window['window_id']}/close",
                        json={"user_id": "u1", "outcome": "gate_failed"})
    second = client.post(f"/training/windows/{window['window_id']}/close",
                         json={"user_id": "u1", "outcome": "gate_failed"})
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


def test_re_closing_with_a_different_outcome_is_a_409(client, store):
    """Rewriting a recorded outcome would silently advance a watermark that a failed
    night deliberately left alone."""
    _land(store, "u1", _ago(days=3))
    window = client.post("/training/windows", json={"user_id": "u1"}).json()
    client.post(f"/training/windows/{window['window_id']}/close",
                json={"user_id": "u1", "outcome": "gate_failed"})
    got = client.post(f"/training/windows/{window['window_id']}/close",
                      json={"user_id": "u1", "outcome": "published"})
    assert got.status_code == 409
    assert store.last_trained_t("u1") is None
    assert store.get_window("u1", window["window_id"])["outcome"] == "gate_failed"


def test_close_raises_on_an_outcome_the_enum_does_not_know(store):
    _land(store, "u1", "2026-07-20T04:00:00Z")
    window = store.open_training_window("u1", now=_at(2026, 7, 22, 4, 0, 0))
    with pytest.raises(ValueError):
        store.close_training_window("u1", window["window_id"], "definitely_not_an_outcome")


def test_a_conflicting_close_raises_at_the_store_level_too(store):
    _land(store, "u1", "2026-07-20T04:00:00Z")
    window = store.open_training_window("u1", now=_at(2026, 7, 22, 4, 0, 0))
    store.close_training_window("u1", window["window_id"], "frozen")
    with pytest.raises(WindowOutcomeConflict):
        store.close_training_window("u1", window["window_id"], "published")


# --- enumeration -------------------------------------------------------------------


def test_enumeration_is_ordered_and_filterable(client, store):
    for day, outcome in [(21, "published"), (22, "gate_failed"), (23, "published")]:
        _land(store, "u1", "2026-07-20T04:00:00Z")
        win = store.open_training_window("u1", now=_at(2026, 7, day, 4, 0, 0))
        store.close_training_window("u1", win["window_id"], outcome)
    open_win = store.open_training_window("u1", now=_at(2026, 7, 24, 4, 0, 0))

    all_windows = client.get("/training/windows", params={"user_id": "u1"})
    assert all_windows.status_code == 200
    ids = [w["window_id"] for w in all_windows.json()]
    assert len(ids) == 4
    assert ids == sorted(ids)          # window_id order IS chronological order
    assert ids[-1] == open_win["window_id"]

    still_open = client.get("/training/windows",
                            params={"user_id": "u1", "state": "open"}).json()
    assert [w["window_id"] for w in still_open] == [open_win["window_id"]]

    consolidated = client.get("/training/windows",
                              params={"user_id": "u1", "state": "consolidated"}).json()
    assert len(consolidated) == 3
    assert {w["outcome"] for w in consolidated} == {"published", "gate_failed"}


def test_enumeration_is_per_user(client, store):
    _land(store, "u-a", "2026-07-20T04:00:00Z")
    _land(store, "u-b", "2026-07-20T04:00:00Z")
    store.open_training_window("u-a", now=_at(2026, 7, 22, 4, 0, 0))
    store.open_training_window("u-b", now=_at(2026, 7, 22, 4, 0, 1))

    for user in ("u-a", "u-b"):
        rows = client.get("/training/windows", params={"user_id": user}).json()
        assert [w["user_id"] for w in rows] == [user]


def test_enumeration_of_an_unknown_user_is_an_empty_list(client):
    got = client.get("/training/windows", params={"user_id": "ghost"})
    assert got.status_code == 200 and got.json() == []


def test_an_unknown_state_filter_is_a_422(client):
    got = client.get("/training/windows", params={"user_id": "u1", "state": "closed"})
    assert got.status_code == 422


def test_the_ledger_row_shape_is_stable(client, store):
    _land(store, "u1", _ago(days=3))
    body = client.post("/training/windows", json={"user_id": "u1"}).json()
    assert set(body) == {"window_id", "user_id", "t_start", "t_end", "state",
                         "outcome", "opened_at", "closed_at"}
    assert body["state"] == "open"
    assert body["outcome"] is None and body["closed_at"] is None


def test_the_open_body_rejects_caller_supplied_bounds(client, store):
    """The bounds are facts about STORAGE's ingest clock — that is the whole reason the
    ledger lives here rather than in continuum."""
    _land(store, "u1", _ago(days=3))
    got = client.post("/training/windows",
                      json={"user_id": "u1", "t_start": "2020-01-01T00:00:00Z"})
    assert got.status_code == 422


# --- the frozen contract gates these bodies, not just the pydantic mirror ------------
#
# `contracts/c10_training_window.v1.json` is the AUTHORITY; `models.TrainingWindow` is a
# second, independent check — the same order `POST /context/records` applies to C2 (schema
# first, mirror second). The schema is a SIBLING of `c10_daylog.v1.json` rather than an
# extension of it because C10 is a family of OPERATIONS: the day-log body and the ledger
# row are different bodies on different endpoints, and one file could only carry both
# behind a `oneOf` that hides exactly the distinction the contract is about.
#
# The tests below are split in two on purpose. The first group proves the real responses
# validate; the second proves the gate is NOT VACUOUS, by mutating a real body into each
# shape the contract forbids and asserting the schema rejects it. A schema that only ever
# sees valid input is indistinguishable from no schema at all.


def test_every_window_response_validates_against_the_frozen_c10_schema(client, store):
    """All three surfaces that serve a ledger row: open, enumerate, close."""
    _land(store, "u1", _ago(days=3))

    opened = client.post("/training/windows", json={"user_id": "u1"})
    assert opened.status_code == 200
    assert schemas.validate_c10_window(opened.json()) == []

    listed = client.get("/training/windows", params={"user_id": "u1"})
    assert listed.status_code == 200
    assert listed.json()  # not vacuous: there is a row to check
    for row in listed.json():
        assert schemas.validate_c10_window(row) == []

    closed = client.post(
        f"/training/windows/{opened.json()['window_id']}/close",
        json={"user_id": "u1", "outcome": "published"},
    )
    assert closed.status_code == 200
    assert schemas.validate_c10_window(closed.json()) == []


@pytest.mark.parametrize("outcome", ["published"] + NON_ADVANCING)
def test_every_outcome_the_ledger_accepts_is_in_the_frozen_enum(client, store, outcome):
    """The enum in the contract and the enum in `db.WINDOW_OUTCOMES` must not drift: a
    close the ledger accepts but the schema rejects would be a 500 on a legal night."""
    _land(store, "u1", _ago(days=3))
    window_id = client.post("/training/windows",
                            json={"user_id": "u1"}).json()["window_id"]
    closed = client.post(f"/training/windows/{window_id}/close",
                         json={"user_id": "u1", "outcome": outcome})
    assert closed.status_code == 200
    assert schemas.validate_c10_window(closed.json()) == []


def test_the_enumeration_is_a_bare_array_of_contract_rows(client, store):
    """No envelope: the contract is over the ROW, so consumers validate element-wise.
    Pinned because adding a wrapper later would silently break every such consumer."""
    _land(store, "u1", _ago(days=3))
    client.post("/training/windows", json={"user_id": "u1"})
    assert isinstance(client.get("/training/windows",
                                 params={"user_id": "u1"}).json(), list)


def _a_real_open_row(client, store) -> dict:
    _land(store, "u1", _ago(days=3))
    return client.post("/training/windows", json={"user_id": "u1"}).json()


@pytest.mark.parametrize(
    "field, value",
    [
        # The id is minted in exactly one place and is compared AS A STRING by four
        # continuum call sites; a shape that is not fixed-width breaks that ordering.
        ("window_id", "w2026-07-22"),
        ("window_id", "2026-07-22T03:59:00Z"),   # a raw instant: colons are not path-safe
        ("window_id", "w20260722T035900Z\n"),    # trailing newline is a different path
        ("state", "closed"),                     # not one of open|consolidated
        ("user_id", ""),                         # a window_id does not name a row alone
    ],
)
def test_the_schema_rejects_a_malformed_field(client, store, field, value):
    assert schemas.validate_c10_window({**_a_real_open_row(client, store), field: value})


def test_the_schema_rejects_an_open_window_that_names_an_outcome(client, store):
    """An open window has reported nothing. If it could carry `published`, the derived
    watermark would already have advanced past a boundary the cycle had not finished."""
    row = _a_real_open_row(client, store)
    assert schemas.validate_c10_window({**row, "outcome": "published"})
    assert schemas.validate_c10_window({**row, "closed_at": "2026-07-22T04:00:00Z"})


def test_the_schema_rejects_a_consolidated_window_with_no_outcome(client, store):
    """THE INVARIANT THE PYDANTIC MIRROR CANNOT EXPRESS, which is why this schema exists
    rather than being redundant with the model.

    `last_trained_t` is derived by selecting rows whose outcome is `published`, so a
    consolidated row with a null outcome is a night whose training status is
    unanswerable — and a query that can only see the column would silently treat it as
    non-advancing. `TrainingWindow` types `state` and `outcome` independently and cannot
    say that the two agree; the schema's if/then pair can, and does.
    """
    row = _a_real_open_row(client, store)
    consolidated = {**row, "state": "consolidated", "closed_at": "2026-07-22T04:00:00Z"}
    assert schemas.validate_c10_window({**consolidated, "outcome": None})
    assert schemas.validate_c10_window({**consolidated, "outcome": "published",
                                        "closed_at": None})
    # ... and the well-formed version of the same row passes, so the assertions above
    # are failing on the invariant and not on some unrelated defect in the fixture.
    assert schemas.validate_c10_window({**consolidated, "outcome": "published"}) == []


def test_the_schema_rejects_an_unknown_outcome(client, store):
    """A CLOSED enum: an unrecognised outcome must fail loudly, never become a silent
    non-advance or — far worse — a silent advance."""
    row = _a_real_open_row(client, store)
    assert schemas.validate_c10_window(
        {**row, "state": "consolidated", "outcome": "succeeded",
         "closed_at": "2026-07-22T04:00:00Z"}
    )


def test_the_schema_rejects_a_watermark_column(client, store):
    """`last_trained_t` is DERIVED (MAX(t_end) over this user's published rows) and is
    deliberately not a field. `additionalProperties: false` is what keeps it that way —
    a stored copy is a second source of truth that can disagree with the ledger."""
    assert schemas.validate_c10_window(
        {**_a_real_open_row(client, store), "last_trained_t": "2026-07-20T00:00:00Z"}
    )


def test_no_contract_accepts_a_window_id_with_a_trailing_newline():
    """One id, three schemas, one spec — pinned together so the three cannot drift.

    JSON Schema's `pattern` is unanchored-search semantics, and in the Python validator
    these contracts are enforced by, `$` ALSO MATCHES JUST BEFORE A TRAILING NEWLINE. So
    `^w\\d{8}T\\d{6}Z$` alone admits "w20260722T035900Z\\n" — a string that is a different
    filesystem path from the id it looks like, and the reservoir stores corpora at paths
    built from it. `app.window_id.validate_window_id` closes the hole with `fullmatch`;
    the schemas close it with an exact width. This test is why the width bounds must not
    be tidied away as redundant with the pattern.
    """
    forged = "w20260722T035900Z\n"
    assert not validate_window_id(forged)                       # the service's own gate

    assert schemas.validate_c10_window(
        {"window_id": forged, "user_id": "u1", "t_start": "2026-07-20T00:00:00Z",
         "t_end": "2026-07-22T03:59:00Z", "state": "open", "outcome": None,
         "opened_at": "2026-07-22T04:00:00Z", "closed_at": None}
    )
    assert schemas.validate_c10(
        {"contract": "C10", "version": "1", "user_id": "u1", "window_id": forged,
         "t_start": "2026-07-20T00:00:00Z", "t_end": "2026-07-22T03:59:00Z",
         "daylog_format_version": "daylog-v1", "recipe_id": "consolidation-v1.0",
         "home_tz": "UTC", "segments": [], "blocks": [], "content_fingerprint": "f"}
    )
    assert schemas.validate_c14(
        {"contract": "C14", "version": "0", "user_id": "u1", "before_window": None,
         "entries": [{"user_id": "u1", "window_id": forged,
                      "recipe_id": "consolidation-v1.0", "sha": "0" * 64, "chars": 0,
                      "admitted_at": "2026-07-22T04:00:00Z"}]}
    )


@pytest.mark.parametrize(
    "missing",
    ["window_id", "user_id", "t_start", "t_end", "state", "outcome",
     "opened_at", "closed_at"],
)
def test_every_field_is_required_including_the_nullable_ones(client, store, missing):
    """`outcome` and `closed_at` are REQUIRED-NULLABLE: the key is always present and may
    be null, so a reader never has to tell 'absent' from 'not yet'. Same convention as
    C2's segment `speaker` and C4's empty trace arrays."""
    row = _a_real_open_row(client, store)
    assert schemas.validate_c10_window({k: v for k, v in row.items() if k != missing})


# --- migration ---------------------------------------------------------------------


def test_migration_adds_the_ledger_and_the_ingest_index_to_a_preexisting_db(tmp_path):
    """A live DB predates D18 and has no ingest_time index — membership on the ingest
    axis needs one, and a new index (unlike a new column) is handled by _SCHEMA alone."""
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
        conn.execute("CREATE INDEX idx_context_user_tstart ON context_records (user_id, t_start)")
        conn.execute(
            "INSERT INTO context_records VALUES"
            " ('r-old','u1','2026-07-01T00:00:00Z',NULL,'c1','v1',"
            "  '2026-07-01T00:00:01Z',NULL,NULL,'{}')"
        )

    store = Store(path=str(db), raw_root=str(tmp_path / "raw"))
    with store._connect() as conn:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        indexes = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "training_windows" in tables
    assert "idx_context_user_ingest" in indexes

    # The legacy row is visible on the ingest axis — no backfill was needed, the column
    # was always there; what D18 added is the index over it.
    assert store.earliest_ingest_time("u1") == "2026-07-01T00:00:01Z"
    window = store.open_training_window("u1", now=_at(2026, 7, 2, 0, 0, 0))
    assert window["t_start"] == "2026-07-01T00:00:01Z"


def test_the_ingest_index_is_on_user_and_ingest_time(store):
    with store._connect() as conn:
        cols = [r["name"] for r in conn.execute(
            "PRAGMA index_info(idx_context_user_ingest)")]
    assert cols == ["user_id", "ingest_time"]
