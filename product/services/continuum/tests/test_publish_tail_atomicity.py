"""The publish tail's three atomicity properties, each proved by a defect that was real.

`run_cycle`'s gate-passed tail is a sequence of side effects — `directory.publish()`,
`state.record_pass()` / `state.strike()`, `reservoir.admit()` — and only THEN the
journal's terminal record. Anything failing in that gap keeps every side effect and
loses the record, so the retry re-enters the tail. Putting the reservoir behind HTTP
makes that likelier rather than rarer: `reservoir.admit` is a POST to another service
where a local write would have been atomic.

The guard that was supposed to make re-entry safe is the journal's terminal key — but it
descends from the day-log's `content_fingerprint`, so ANY legitimate re-materialization
sails past it. The realistic trigger is an operator correcting `home_tz`, which storage's
own rules re-render the day-log for. So re-entry is not an exotic crash case; it is a
documented operator action.

Each test below fails without its fix.
"""
from __future__ import annotations

import httpx
import pytest

from app.clients.reservoir_client import HttpReservoirClient
from app.publish import ModelDirectory, _active_stack


def _publish(directory, user, window, version, *, retention=14):
    return directory.publish(
        user_id=user, adapter_version=version, adapter_dir=f"/tmp/{version}",
        base_model_hash="qwen3-vl-32b-instruct", training_window=window,
        recipe_id="consolidation-v1.1", eval_report={}, snapshot_retention=retention)


# ----------------------------------------------------------------- H1a: idempotence
def test_republishing_one_night_does_not_duplicate_its_activation(var_dir):
    """The tail re-entered after a mid-tail failure must not append a second row.

    Before the fix this produced TWO `active` rows for one window and pushed
    `_active_stack` two deep."""
    d = ModelDirectory(var_dir)
    _publish(d, "u-idem", "w20260721T110000Z", "a-first")
    _publish(d, "u-idem", "w20260721T110000Z", "a-first")   # the retry

    entries = d.entries("u-idem")
    active = [e for e in entries if e["status"] == "active"]
    assert len(active) == 1, f"one activation per window, got {len(active)}"
    assert len(_active_stack(entries)) == 1


def test_rollback_still_returns_to_base_after_a_retried_publish(var_dir):
    """THE reason H1 mattered: a duplicated activation silently disabled rollback.

    With two rows on the stack, one `rollback()` popped to the *same* adapter
    version — so the operator's one safety net for a bad adapter did nothing, with
    no error. CHARTER M2's exit criterion is that one command restores the prior
    version."""
    d = ModelDirectory(var_dir)
    _publish(d, "u-rb", "w20260721T110000Z", "a-only")
    _publish(d, "u-rb", "w20260721T110000Z", "a-only")   # the retry

    d.rollback("u-rb")
    assert d.active("u-rb") is None, (
        "one rollback after a retried publish must return to base, not to the "
        "same adapter version")


def test_a_rematerialized_night_supersedes_rather_than_stacking(var_dir):
    """A re-rendered day-log yields a DIFFERENT adapter for the SAME window.

    At most one live activation per window: the prior one is rolled back first, so
    the stack stays one deep and rollback keeps its meaning."""
    d = ModelDirectory(var_dir)
    _publish(d, "u-sup", "w20260721T110000Z", "a-before-tz-fix")
    _publish(d, "u-sup", "w20260721T110000Z", "a-after-tz-fix")

    stack = _active_stack(d.entries("u-sup"))
    assert [e["adapter_version"] for e in stack] == ["a-after-tz-fix"]
    assert d.active("u-sup")["adapter_version"] == "a-after-tz-fix"
    # The superseded row survives for lineage — audit is never silently dropped.
    assert any(e["adapter_version"] == "a-before-tz-fix" and e["status"] == "rolled_back"
               for e in d.entries("u-sup"))


def test_the_alias_never_moves_backward_on_a_republish(var_dir):
    """Re-asserting an OLD window's alias must not steal it from a newer window."""
    d = ModelDirectory(var_dir)
    _publish(d, "u-back", "w20260721T110000Z", "a-old")
    _publish(d, "u-back", "w20260722T110000Z", "a-new")
    _publish(d, "u-back", "w20260721T110000Z", "a-old")   # a retry of the OLD night

    assert d.active("u-back")["adapter_version"] == "a-new"


# ------------------------------------------------------- H1b: a conflict must not strand
def test_a_reservoir_conflict_lets_the_night_finish(monkeypatch):
    """C14 is append-only, so re-admitting a changed corpus is a permanent 409.

    Fatal here, that stranded the night short of its terminal journal record — and
    since the conflict never clears, every retry died at the same point while
    appending another C5 row. Provenance must never block consolidation."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            calls["n"] += 1
            return httpx.Response(409, json={"detail": "corpus conflict"})
        return httpx.Response(200, json={
            "contract": "C14", "version": "0", "user_id": "u-c",
            "entries": [{"user_id": "u-c", "window_id": "w20260721T110000Z",
                         "recipe_id": "consolidation-v1.1", "sha": "0" * 64,
                         "chars": 10, "admitted_at": "2026-07-27T00:00:00Z"}]})

    client = HttpReservoirClient("http://storage.test",
                                 transport=httpx.MockTransport(handler))
    entry = client.admit("u-c", "w20260721T110000Z", "consolidation-v1.1", "new text")

    assert calls["n"] == 1
    assert entry.window_id == "w20260721T110000Z", (
        "the existing admission stands and is returned, so the tail continues")


def test_a_conflict_with_no_existing_entry_still_raises(monkeypatch):
    """Only a conflict we can EXPLAIN is swallowed. A 409 with nothing in the ledger
    is not the append-only case and must not be silently absorbed."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(409, json={"detail": "corpus conflict"})
        return httpx.Response(200, json={"contract": "C14", "version": "0",
                                         "user_id": "u-c", "entries": []})

    client = HttpReservoirClient("http://storage.test",
                                 transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        client.admit("u-c", "w20260721T110000Z", "consolidation-v1.1", "text")
