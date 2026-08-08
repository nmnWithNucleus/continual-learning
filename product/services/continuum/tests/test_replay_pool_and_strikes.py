"""Two properties that moving the stores behind HTTP silently lost, each with its proof.

Both share a root cause with the publish tail: the journal's terminal key descends from
the day-log's `content_fingerprint`, so a legitimate re-materialization re-enters code
that assumed it ran once.
"""
from __future__ import annotations

import json

import httpx

from app.clients.window_client import HttpWindowLedger
from app.cycle import _UserState


def _ledger(rows):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=rows)
    return HttpWindowLedger("http://storage.test",
                            transport=httpx.MockTransport(handler))


def _row(window_id, outcome):
    return {"contract": "C10", "version": "1", "window_id": window_id,
            "user_id": "u-r", "t_start": "2026-07-20T04:00:00Z",
            "t_end": "2026-07-21T04:00:00Z", "state": "consolidated",
            "outcome": outcome, "opened_at": "2026-07-20T04:00:00Z",
            "closed_at": "2026-07-21T04:00:00Z"}


# ------------------------------------------------------------------ H2: the replay pool
def test_replay_pool_holds_published_windows_only():
    """Replay is ANTI-FORGETTING, so it can only mean nights the adapter learned.

    Before the fix `prior_windows` filtered on ledger STATE ('consolidated') and never
    on OUTCOME, so gate-failed and crashed windows entered the rehearsal pool. Measured
    live over publish -> gate-fail -> publish: 50% of night 3's replay budget went on
    material already in tonight's fresh corpus, because a failed window does not advance
    the watermark and is therefore re-trained as fresh AND rehearsed at once."""
    ledger = _ledger([_row("w20260721T110000Z", "published"),
                      _row("w20260722T110000Z", "gate_failed"),
                      _row("w20260723T110000Z", "crashed"),
                      _row("w20260724T110000Z", "skipped_no_data"),
                      _row("w20260725T110000Z", "published")])

    got = [w.window_id for w in
           ledger.prior_windows("u-r", "w20260726T110000Z", tz="UTC")]

    assert got == ["w20260721T110000Z", "w20260725T110000Z"], (
        "only published nights may be rehearsed; a night that never entered the "
        "adapter has nothing to be forgotten and displaces material that does")


def test_replay_pool_still_excludes_the_window_being_trained():
    """The `<` guard is unchanged — a window never rehearses itself."""
    ledger = _ledger([_row("w20260721T110000Z", "published"),
                      _row("w20260722T110000Z", "published")])
    got = [w.window_id for w in
           ledger.prior_windows("u-r", "w20260722T110000Z", tz="UTC")]
    assert got == ["w20260721T110000Z"]


# ----------------------------------------------------------------------- M1: strikes
def test_one_window_strikes_at_most_once(var_dir):
    """`cycle.py`'s docstring promises strikes are window-monotonic. They were not.

    With the shipped `consecutive_fail_freeze: 2`, one window that gate-failed and was
    then re-consolidated (an operator correcting `home_tz` re-renders the day-log, which
    sails past the content-derived terminal guard) struck TWICE and froze the user."""
    state = _UserState(var_dir, "u-s")
    state.strike("w20260721T110000Z", 2)
    state.strike("w20260721T110000Z", 2)     # the re-materialized retry
    state.strike("w20260721T110000Z", 2)

    saved = json.loads((var_dir / "state" / "u-s.json").read_text())
    assert saved["consecutive_failures"] == 1, "one window, one strike"
    assert saved["frozen"] is False, "one failed night must not freeze a user"
    assert saved["debt"] == ["w20260721T110000Z"]


def test_distinct_failed_windows_still_accumulate_and_freeze(var_dir):
    """The guard must not disarm the freeze: genuinely consecutive failures still count."""
    state = _UserState(var_dir, "u-s2")
    state.strike("w20260721T110000Z", 2)
    state.strike("w20260722T110000Z", 2)

    saved = json.loads((var_dir / "state" / "u-s2.json").read_text())
    assert saved["consecutive_failures"] == 2
    assert saved["frozen"] is True


def test_a_window_that_fails_then_passes_then_fails_strikes_twice(var_dir):
    """Those are two genuine failures, not one counted twice — `record_pass` clears
    the window from `debt`, so the idempotency guard correctly re-arms."""
    state = _UserState(var_dir, "u-s3")
    state.strike("w20260721T110000Z", 5)
    state.record_pass("w20260721T110000Z")
    state.strike("w20260721T110000Z", 5)

    saved = json.loads((var_dir / "state" / "u-s3.json").read_text())
    assert saved["consecutive_failures"] == 1, (
        "record_pass reset the counter, so the second failure is the first of a new run")
    assert saved["debt"] == ["w20260721T110000Z"]
