"""DedupStore — the L8 claim tree + the async claim lifecycle.

The five verdicts, decided ONLY from DP's own ledger (the row_lookup callback
over the journal's done-row — never a storage read), under the existing one-lock
discipline: ``fresh`` / ``version_forward`` / ``skip`` / ``heal`` / ``inflight``.
The in-memory ``_done`` map is now a cache of STABLE skip verdicts only (green,
finalized, or budget-exhausted rows); a holey-but-healable chunk is re-judged
from the ledger on every delivery, so heal bookkeeping is never answered from a
stale cache.
"""
from __future__ import annotations

import asyncio

from app.dedup import Claim, DedupStore
from app.journal import HEAL_MAX_ATTEMPTS


def _classify(store, cid, pv):
    """Sync wrapper: classify is async since the review round (threadpool read)."""
    return asyncio.run(store.classify(cid, pv))


def _row(pv="pv-1", statuses=None, attempts=0, final=False, ids=("r1",)):
    return {
        "chunk_id": "c1", "modality": "audio", "record_ids": list(ids),
        "pipeline_version": pv, "processed_at": "t",
        "stage_status": statuses, "heal_attempts": attempts,
        "done_final": final, "cached_slots": None, "superseded_pv": None,
    }


# ---- The decision tree (classify) ---------------------------------------------

def test_no_row_is_fresh():
    d = DedupStore(row_lookup=lambda cid: None)
    assert _classify(d, "c1", "pv-1") == Claim("fresh")


def test_version_mismatch_is_version_forward_even_with_holes_or_final():
    """Tree order: the version check comes FIRST (L8 case 2) — a stale-dialect
 row version-forwards regardless of its hole or budget state."""
    for row in (
        _row(pv="pv-OLD", statuses={"asr": "ok"}),
        _row(pv="pv-OLD", statuses={"asr": "ok", "acoustic": "failed"}),
        _row(pv="pv-OLD", statuses={"asr": "ok", "acoustic": "failed"}, final=True),
    ):
        d = DedupStore(row_lookup=lambda cid, r=row: r)
        claim = _classify(d, "c1", "pv-1")
        assert claim.verdict == "version_forward"
        assert claim.row["pipeline_version"] == "pv-OLD"


def test_all_green_is_skip():
    d = DedupStore(row_lookup=lambda cid: _row(statuses={"asr": "ok", "diarize": "ok"}))
    claim = _classify(d, "c1", "pv-1")
    assert claim.verdict == "skip" and claim.record_ids == ["r1"]


def test_legacy_null_statuses_is_skip():
    """A pre-D row carries no hole evidence — heals need evidence, so it skips."""
    d = DedupStore(row_lookup=lambda cid: _row(statuses=None))
    assert _classify(d, "c1", "pv-1").verdict == "skip"


def test_holes_with_budget_is_heal():
    row = _row(statuses={"asr": "ok", "acoustic": "failed", "align": "cancelled"},
               attempts=HEAL_MAX_ATTEMPTS - 1)
    d = DedupStore(row_lookup=lambda cid: row)
    claim = _classify(d, "c1", "pv-1")
    assert claim.verdict == "heal"
    assert claim.record_ids == ["r1"]           # the id the heal re-POSTs (L3)
    assert claim.row is row


def test_done_final_or_exhausted_budget_is_skip():
    holey = {"asr": "ok", "acoustic": "failed"}
    for row in (_row(statuses=holey, final=True),
                _row(statuses=holey, attempts=HEAL_MAX_ATTEMPTS)):
        d = DedupStore(row_lookup=lambda cid, r=row: r)
        claim = _classify(d, "c1", "pv-1")
        assert claim.verdict == "skip" and claim.record_ids == ["r1"]


def test_unresolvable_current_pv_skips_the_version_check():
    """current_pv None (modality unresolvable) — can't judge, same posture as the
 old pv_for_modality None: the stored row is served."""
    d = DedupStore(row_lookup=lambda cid: _row(pv="pv-OLD", statuses={"a": "ok"}))
    assert _classify(d, "c1", None).verdict == "skip"


def test_stable_skips_are_cached_heals_are_not():
    calls = []

    def lookup_green(cid):
        calls.append(cid)
        return _row(statuses={"asr": "ok"})

    d = DedupStore(row_lookup=lookup_green)
    assert _classify(d, "c1", "pv-1").verdict == "skip"
    assert _classify(d, "c1", "pv-1").verdict == "skip"
    assert calls == ["c1"]                      # second answer came from the cache

    calls2 = []

    def lookup_holey(cid):
        calls2.append(cid)
        return _row(statuses={"asr": "ok", "acoustic": "failed"})

    d2 = DedupStore(row_lookup=lookup_holey)
    assert _classify(d2, "c2", "pv-1").verdict == "heal"
    assert _classify(d2, "c2", "pv-1").verdict == "heal"
    assert calls2 == ["c2", "c2"]               # re-judged from the ledger each time


def test_classify_ledger_read_runs_off_the_event_loop():
    """Close-out round: the review-round fix moved the sqlite read off the loop;
 this pins it (a docstring is not enforcement). The lookup must run on a
 thread that is NOT the event-loop thread and has no running loop."""
    import threading

    seen: dict = {}

    def lookup(cid):
        seen["thread"] = threading.get_ident()
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        return None

    d = DedupStore(row_lookup=lookup)

    async def go():
        seen["loop_thread"] = threading.get_ident()
        return await d.classify("c1", "pv-1")

    assert asyncio.run(go()).verdict == "fresh"
    assert seen["thread"] != seen["loop_thread"], "ledger read ran ON the loop thread"
    assert seen["on_loop"] is False


def test_put_green_caches_holey_ship_does_not():
    d = DedupStore(row_lookup=lambda cid: None)
    d.put("green-chunk", ["rg"])                # default green=True
    assert d.get("green-chunk") == ["rg"]
    d.put("holey-chunk", ["rh"], green=False)   # holey ship: NOT skip-cached
    assert d.get("holey-chunk") is None


# ---- The async claim lifecycle (one-lock discipline, unchanged guarantees) -----

def test_claim_then_inflight_then_done():
    async def go():
        d = DedupStore(row_lookup=lambda cid: None)
        assert (await d.claim_for_async("c1", "pv-1")).verdict == "fresh"
        # Concurrent redelivery sees it in-flight (no second claim).
        assert (await d.claim_for_async("c1", "pv-1")).verdict == "inflight"
        # Completion records ids AND releases the in-flight claim.
        d.put("c1", ["r1"])
        assert d.get("c1") == ["r1"]
        claim = await d.claim_for_async("c1", "pv-1")
        assert claim.verdict == "skip" and claim.record_ids == ["r1"]

    asyncio.run(go())


def test_heal_claim_rides_the_same_inflight_discipline():
    async def go():
        row = _row(statuses={"asr": "ok", "acoustic": "failed"})
        d = DedupStore(row_lookup=lambda cid: row)
        first = await d.claim_for_async("c1", "pv-1")
        assert first.verdict == "heal"
        # A redelivery arriving mid-heal re-ACKs 202 unchanged (L8 case 5).
        assert (await d.claim_for_async("c1", "pv-1")).verdict == "inflight"
        d.release_inflight("c1")                # failed heal: claim released
        assert (await d.claim_for_async("c1", "pv-1")).verdict == "heal"

    asyncio.run(go())


def test_release_inflight_allows_reclaim():
    async def go():
        d = DedupStore(row_lookup=lambda cid: None)
        assert (await d.claim_for_async("c1", "pv-1")).verdict == "fresh"
        d.release_inflight("c1")
        assert (await d.claim_for_async("c1", "pv-1")).verdict == "fresh"
        assert d.get("c1") is None

    asyncio.run(go())


def test_release_inflight_is_idempotent_and_safe_when_absent():
    d = DedupStore()
    d.release_inflight("never-claimed")  # no error
    d.release_inflight("never-claimed")


def test_reset_inflight_clears_all_claims():
    async def go():
        d = DedupStore(row_lookup=lambda cid: None)
        await d.claim_for_async("a", "pv-1")
        await d.claim_for_async("b", "pv-1")
        assert (await d.claim_for_async("a", "pv-1")).verdict == "inflight"
        d.reset_inflight()
        assert (await d.claim_for_async("a", "pv-1")).verdict == "fresh"

    asyncio.run(go())


def test_put_releases_inflight_even_without_prior_claim():
    d = DedupStore()
    # Inline mode never claims; put() must still be safe (discard is a no-op).
    d.put("inline-chunk", ["r"])
    assert d.get("inline-chunk") == ["r"]
