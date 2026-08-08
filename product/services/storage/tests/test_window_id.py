"""D18 — the `window_id` minter + validator.

ONE minter, ONE validator, nothing else may construct an id. These tests pin the three
properties ARCHITECTURE § "C10 evolved" calls load-bearing — path-safety, lexicographic
== chronological order, and second granularity — plus the two shapes the format
replaces, so a regression to either is a red test rather than a corrupted journal.

Note what is deliberately NOT tested, because it deliberately does not exist: parsing an
id back into a date. D18 deleted `Window.local_date` and `ReservoirEntry.local_window_date`
precisely because a watermark window can span 23 h, 25 h or 47 h and has no local date to
name. Consumers may rely on `<` / `>=` and on nothing else.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.window_id import WINDOW_ID_PATTERN, mint_window_id, validate_window_id

UTC = timezone.utc

# Continuum's id gate, restated literally (services/continuum/app/ids.py:8). It lives in
# a different service and a different venv, so this is a COPY on purpose: the whole point
# is that a window_id survives being used as a filesystem path component and an rmtree
# target over there.
CONTINUUM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def test_mints_the_documented_example():
    """The literal example from ARCHITECTURE § 'C10 evolved'."""
    assert mint_window_id(datetime(2026, 7, 21, 11, 0, 0, tzinfo=UTC)) == "w20260721T110000Z"


def test_the_minter_and_the_validator_agree():
    for moment in (
        datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        datetime(2026, 7, 21, 11, 0, 0, tzinfo=UTC),
        datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC),
    ):
        assert validate_window_id(mint_window_id(moment))


def test_the_pattern_is_the_one_in_the_decision():
    assert WINDOW_ID_PATTERN == r"^w\d{8}T\d{6}Z$"


def test_the_old_local_date_format_is_rejected():
    """`w2026-07-21` was the format before the watermark window. It must not validate:
    mixed formats order correctly only by ASCII accident ('-' 0x2D sorts below '0' 0x30),
    so the rejection has to be explicit and tested, never trusted."""
    assert not validate_window_id("w2026-07-21")


def test_legacy_freeform_window_ids_are_rejected():
    """`w-day5` — the shape a pre-D18 continuum smoke script wrote, since retired —
    breaks the total order twice over: 'w-day10' < 'w-day5', and every 'w-day*' sorts
    below every real id. It is a mess, not a precedent, and the validator is what stops
    it recurring. The script is gone; this test is why it cannot come back."""
    assert not validate_window_id("w-day5")


def test_a_raw_rfc3339_instant_is_rejected():
    """A raw instant fails continuum's path gate because of its colons — which is the
    reason the id is a token rather than a timestamp."""
    assert not validate_window_id("2026-07-21T11:00:00Z")
    assert not CONTINUUM_ID_RE.match("2026-07-21T11:00:00Z")


def test_minted_ids_are_path_safe_for_continuum():
    """Property (1): the id is a filesystem path component and an rmtree target over in
    continuum (journal/, cycles/, reservoir/, adapters/)."""
    window_id = mint_window_id(datetime(2026, 7, 21, 11, 0, 0, tzinfo=UTC))
    assert CONTINUUM_ID_RE.match(window_id)
    assert "/" not in window_id and ":" not in window_id and window_id == window_id.strip()


def test_lexicographic_order_is_chronological_order():
    """Property (2): four continuum call sites compare these ids AS STRINGS and rely on
    nothing else — publish's active_before + alias-monotonicity guard, cycle's journal
    debt + latest_window, reservoir's before_window replay filter."""
    instants = [
        datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        datetime(2026, 7, 9, 4, 0, 0, tzinfo=UTC),
        datetime(2026, 7, 21, 11, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 3, 9, 5, 1, tzinfo=UTC),
        datetime(2027, 3, 1, 0, 0, 1, tzinfo=UTC),
    ]
    ids = [mint_window_id(t) for t in instants]
    assert ids == sorted(ids)
    # And shuffling the input cannot change that: string sort == time sort.
    assert sorted(mint_window_id(t) for t in reversed(instants)) == ids


def test_granularity_is_seconds_not_minutes():
    """Property (3): a truncating id can silently collide two distinct windows, and an
    id collision corrupts the journal, the reservoir and C5 lineage at once."""
    base = datetime(2026, 7, 21, 11, 0, 0, tzinfo=UTC)
    assert mint_window_id(base) != mint_window_id(base + timedelta(seconds=1))


def test_subsecond_precision_is_truncated():
    """The format carries seconds, so the ledger must persist the SAME truncated instant
    as t_end or the two would disagree — Store.open_training_window truncates first."""
    base = datetime(2026, 7, 21, 11, 0, 0, tzinfo=UTC)
    assert mint_window_id(base.replace(microsecond=999_999)) == mint_window_id(base)


def test_a_naive_datetime_is_refused():
    """Rejected rather than assumed UTC or assumed local: a silently wrong zone here
    mis-orders every downstream string comparison."""
    with pytest.raises(ValueError):
        mint_window_id(datetime(2026, 7, 21, 11, 0, 0))


def test_a_non_utc_zone_is_normalized_to_utc():
    local = datetime(2026, 7, 21, 4, 0, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert mint_window_id(local) == mint_window_id(local.astimezone(UTC))
    assert mint_window_id(local) == "w20260721T110000Z"  # PDT is UTC-7


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        "w20260721T110000",          # no Z
        "20260721T110000Z",          # no w
        "W20260721T110000Z",         # wrong case
        "w2026072T110000Z",          # 7-digit date
        "w20260721T11000Z",          # 5-digit time
        "w20260721t110000Z",         # lowercase separator
        "w20260721T110000Zx",        # trailing junk
        " w20260721T110000Z",        # leading space
        "w20260721T110000Z\n",       # trailing newline (a regex '$' trap)
        "journal/w20260721T110000Z",  # a path, not an id
    ],
)
def test_near_misses_are_rejected(candidate):
    assert not validate_window_id(candidate)


def test_validate_tolerates_a_non_string():
    assert not validate_window_id(None)  # type: ignore[arg-type]
    assert not validate_window_id(20260721)  # type: ignore[arg-type]
