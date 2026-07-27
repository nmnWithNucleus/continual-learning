"""The nightly entrypoint after D18: no `--tz`, no `--date`, a storage window.

What these pin is the wiring, because the wiring IS the decision:

  * the timezone is READ from the C12 profile, never passed in;
  * a user with no profile is NOT SCHEDULABLE and the night does not run;
  * the window is OPENED by storage, not computed here;
  * the window is CLOSED with the cycle's own outcome, which is what decides
    whether the watermark advances.
"""
from __future__ import annotations

import json

import pytest

from app import nightly
from app.clients.profile_client import UserNotSchedulable
from tests._helpers import StubWindowLedger, make_window


class _StubRegistry:
    def __init__(self, recipe, policy):
        self.recipe, self.policy = recipe, policy

    def fetch_recipe(self, recipe_id):
        return self.recipe

    def fetch_policy(self, policy_id):
        return self.policy


class _StubProfile:
    def __init__(self, tz="America/Los_Angeles", missing=False):
        self.tz, self.missing, self.asked = tz, missing, []

    def home_tz(self, user_id):
        self.asked.append(user_id)
        if self.missing:
            raise UserNotSchedulable(
                f"user {user_id!r} has NO C12 profile — NOT SCHEDULABLE. "
                "OPERATOR ACTION: set the user's home_tz and re-run.")
        return self.tz


@pytest.fixture
def wired(monkeypatch, var_dir, small_recipe, small_policy):
    """nightly with storage stubbed at the three seams it reaches for."""
    profile = _StubProfile()
    ledger = StubWindowLedger([make_window("u-n", 20, "America/Los_Angeles")])
    monkeypatch.setattr(nightly, "profile_client", lambda settings: profile)
    monkeypatch.setattr(nightly, "window_ledger", lambda settings: ledger)
    monkeypatch.setattr(nightly, "recipe_registry",
                        lambda settings: _StubRegistry(small_recipe, small_policy))
    return profile, ledger


def test_tz_and_date_flags_are_gone(wired):
    """`--tz` is retired (the profile answers that question) and `--date` went with
    `window_for` (there is no local date to name a watermark window)."""
    for argv in (["--user", "u-n", "--tz", "America/Los_Angeles"],
                 ["--user", "u-n", "--date", "2026-07-20"]):
        with pytest.raises(SystemExit) as exc:
            nightly.main(argv)
        assert exc.value.code == 2   # argparse: unrecognized arguments


def test_night_reads_home_tz_and_runs_on_storages_window(wired, capsys):
    profile, ledger = wired
    open_window = ledger.open("u-n", tz=profile.tz)
    rc = nightly.main(["--user", "u-n", "--synthetic"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert profile.asked == ["u-n"]                    # the tz was read, not passed
    assert out["home_tz"] == "America/Los_Angeles"
    assert out["window"] == open_window.window_id      # storage's id, verbatim
    assert out["status"] == "published"


def test_night_closes_the_window_with_the_cycle_outcome(wired, capsys):
    _, ledger = wired
    open_window = ledger.open("u-n", tz="America/Los_Angeles")
    nightly.main(["--user", "u-n", "--synthetic"])
    capsys.readouterr()
    assert ledger.closed == [(open_window.window_id, "published")]


def test_gate_failure_closes_with_gate_failed_so_the_watermark_holds(
        wired, monkeypatch, capsys):
    """Only `published` advances `last_trained_t`. A failed night must report
    itself as failed, so the next window is a strict superset of this one."""
    _, ledger = wired
    monkeypatch.setenv("MOCK_GATE", "fail")
    rc = nightly.main(["--user", "u-n", "--synthetic"])
    capsys.readouterr()
    assert rc == 1
    assert [outcome for _, outcome in ledger.closed] == ["gate_failed"]


def test_a_crash_leaves_the_window_open_and_re_raises(wired, monkeypatch, capsys):
    """REGRESSION (F1). A crash must NOT close the window.

    This test previously asserted the opposite — `ledger.closed == ["crashed"]` — on
    the reasoning that "a crash is an outcome, record it". The reasoning is wrong
    because `close()` is TERMINAL: storage's `POST /training/windows` is a
    get-or-create of the user's **open** window, so closing a crashed window makes
    the retry find none open and mint a fresh id — a fresh journal, a fresh
    `cycles/` dir, a fresh seed, a full re-train, a second C5 entry and a second
    reservoir admission. That is exactly the outcome the idempotent open exists to
    prevent, reached by a different route. The window's outcome is whatever the retry
    reaches; an interrupted attempt is not a verdict.

    Category (b): behaviour we deliberately changed. The assertion is not weakened —
    it is inverted, because the old one pinned the defect.
    """
    _, ledger = wired

    def boom(*a, **k):
        raise RuntimeError("amplifier fell over")

    monkeypatch.setattr(nightly, "run_cycle", boom)
    with pytest.raises(RuntimeError, match="amplifier fell over"):
        nightly.main(["--user", "u-n", "--synthetic"])

    assert ledger.closed == []                       # nothing was closed
    open_now = ledger.open("u-n", tz="America/Los_Angeles")
    assert open_now.state == "open"                  # ...so it is still open
    # And it is LOUD: an operator must be able to see that the window was left open.
    err = capsys.readouterr().err
    assert "CRASHED" in err and "LEFT OPEN" in err


def test_the_retry_after_a_crash_re_opens_the_same_window(wired, monkeypatch, capsys):
    """REGRESSION (F1), the consequence the fix exists for.

    Drive the real entrypoint twice against the stub ledger: crash, then retry. The
    retry must get the SAME `window_id` back, which is what makes the journal resume
    instead of a second window minting a second journal, a second C5 entry and a
    second reservoir admission.
    """
    _, ledger = wired
    before = ledger.open("u-n", tz="America/Los_Angeles")
    real_run_cycle = nightly.run_cycle
    crashing = {"on": True}

    def flaky(*a, **k):
        if crashing["on"]:
            raise RuntimeError("amplifier fell over")
        return real_run_cycle(*a, **k)

    monkeypatch.setattr(nightly, "run_cycle", flaky)
    with pytest.raises(RuntimeError):
        nightly.main(["--user", "u-n", "--synthetic"])
    capsys.readouterr()

    crashing["on"] = False      # the crash is over; the retry runs the real cycle
    rc = nightly.main(["--user", "u-n", "--synthetic"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["window"] == before.window_id     # the SAME window, not a fresh mint
    assert ledger.closed == [(before.window_id, "published")]


def test_crashed_is_still_a_legal_outcome_for_a_deliberate_abandon():
    """`crashed` is not orphaned by F1 — it stays the operator's abandon verb.

    The fix is about who decides, not about deleting a vocabulary word: an `except`
    clause must not abandon a window on the way past, but an operator with a window
    that must never be retried (poison input, a half-migrated store) still needs a
    way to close it. Storage's enum is the contract, so this pins that it survived.
    """
    from app.clients.window_client import WINDOW_OUTCOMES
    assert "crashed" in WINDOW_OUTCOMES


def test_a_dialect_mismatch_exits_2_and_leaves_the_window_open(
        wired, monkeypatch, capsys):
    """REGRESSION (F3), at the entrypoint. A day-log fetched under the wrong recipe
    or format stops the night — and stops it the way a crash does, with the window
    LEFT OPEN, because the retry after the pin is fixed must resume THIS window
    rather than mint a fresh id, a fresh journal and a second C5 entry.

    It is an operator condition and not a crash, so it says what to change and exits
    2 instead of printing a stack trace at somebody whose only fault was re-pinning
    one of the two services.
    """
    from app.clients.daylog_client import DayLogDialectMismatch

    _, ledger = wired
    before = ledger.open("u-n", tz="America/Los_Angeles")

    def mismatched(*a, **k):
        raise DayLogDialectMismatch(
            "day-log was rendered under recipe 'consolidation-v1.0', but this night "
            "trains under 'consolidation-v1.1' (STORAGE_DAYLOG_RECIPE_ID vs "
            "CONTINUUM_RECIPE_ID)")

    monkeypatch.setattr(nightly, "run_cycle", mismatched)
    rc = nightly.main(["--user", "u-n", "--synthetic"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "MISCONFIGURED" in err and "LEFT OPEN" in err
    assert "STORAGE_DAYLOG_RECIPE_ID" in err and "CONTINUUM_RECIPE_ID" in err
    assert ledger.closed == []                                  # terminal close: never
    assert ledger.open("u-n", tz="America/Los_Angeles").window_id == before.window_id


def test_a_user_storage_will_not_open_a_window_for_exits_2_not_a_traceback(
        monkeypatch, var_dir, small_recipe, small_policy, capsys):
    """REGRESSION (F5). `run.sh`'s own printed instructions produced exactly this
    call and exactly this 409 — a user with a profile but no ingest history — and the
    night died inside `raise_for_status` with `Client error '409 Conflict' for url
    http://.../training/windows`. Nothing in that names the missing precondition.
    """
    from app.clients.window_client import WindowNotOpenable

    profile = _StubProfile()

    class _RefusingLedger(StubWindowLedger):
        def open(self, user_id, *, tz):
            raise WindowNotOpenable(
                f"storage will not open a training window for user {user_id!r}: "
                "no ingest history — user has never been trained and has no "
                "/context records, so there is no window start to name",
                {"error": "no ingest history"})

    ledger = _RefusingLedger([make_window("u-n", 20, "UTC")])
    monkeypatch.setattr(nightly, "profile_client", lambda settings: profile)
    monkeypatch.setattr(nightly, "window_ledger", lambda settings: ledger)
    monkeypatch.setattr(nightly, "recipe_registry",
                        lambda settings: _StubRegistry(small_recipe, small_policy))
    rc = nightly.main(["--user", "u-n", "--synthetic"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "NO WINDOW TO CONSOLIDATE" in err
    assert "no ingest history" in err          # the REASON, which is the actionable bit
    assert ledger.closed == []


def test_missing_profile_stops_the_night_before_anything_runs(
        monkeypatch, var_dir, small_recipe, small_policy, capsys):
    """404 on the profile is an operational alert with a human action attached —
    never a silent skip, and never a guessed timezone."""
    profile = _StubProfile(missing=True)
    ledger = StubWindowLedger([make_window("u-n", 20, "UTC")])
    monkeypatch.setattr(nightly, "profile_client", lambda settings: profile)
    monkeypatch.setattr(nightly, "window_ledger", lambda settings: ledger)
    monkeypatch.setattr(nightly, "recipe_registry",
                        lambda settings: _StubRegistry(small_recipe, small_policy))
    rc = nightly.main(["--user", "u-n", "--synthetic"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "NOT SCHEDULABLE" in err and "OPERATOR ACTION" in err
    # No window was opened and none was closed: nothing ran, and nothing pretended to.
    assert ledger.closed == []
