"""Unit tests for the DedupStore async claim lifecycle — the atomicity the async
/ingest accept path leans on (no double-enqueue; no orphaned claim)."""
from __future__ import annotations

import asyncio

from app.dedup import DedupStore


def test_claim_then_inflight_then_done():
    async def go():
        d = DedupStore()
        # First claim wins.
        assert await d.claim_for_async("c1") == "claimed"
        # Concurrent redelivery sees it in-flight (no second claim).
        assert await d.claim_for_async("c1") == "inflight"
        # Completion records ids AND releases the in-flight claim.
        d.put("c1", ["r1", "r2"])
        assert d.get("c1") == ["r1", "r2"]
        # Now a redelivery is 'done' (caller returns record_ids, 200).
        assert await d.claim_for_async("c1") == "done"

    asyncio.run(go())


def test_release_inflight_allows_reclaim():
    async def go():
        d = DedupStore()
        assert await d.claim_for_async("c1") == "claimed"
        # Dead-letter / cancel path: release WITHOUT recording a result.
        d.release_inflight("c1")
        # A redelivery re-claims and reprocesses (self-healing at-least-once).
        assert await d.claim_for_async("c1") == "claimed"
        assert d.get("c1") is None

    asyncio.run(go())


def test_release_inflight_is_idempotent_and_safe_when_absent():
    d = DedupStore()
    d.release_inflight("never-claimed")  # no error
    d.release_inflight("never-claimed")


def test_reset_inflight_clears_all_claims():
    async def go():
        d = DedupStore()
        await d.claim_for_async("a")
        await d.claim_for_async("b")
        assert await d.claim_for_async("a") == "inflight"
        d.reset_inflight()
        # After a reset (loop reuse), prior claims no longer block re-claiming.
        assert await d.claim_for_async("a") == "claimed"

    asyncio.run(go())


def test_put_releases_inflight_even_without_prior_claim():
    d = DedupStore()
    # Inline mode never claims; put() must still be safe (discard is a no-op).
    d.put("inline-chunk", ["r"])
    assert d.get("inline-chunk") == ["r"]
