"""In-process, httpx-MockTransport fakes for storage /raw and data-processing /ingest.

Each fake models the FROZEN idempotency contract:
  * storage /raw is idempotent on chunk_id (re-PUT same chunk_id -> same blob_ref, no dup)
  * data-processing /ingest dedups on chunk_id (re-POST same chunk_id -> same record, no dup)

so a retried request has an exactly-once *effect*. They record every call (on a shared
``events`` timeline, tagged by chunk_id) so tests can assert blob-first ordering across
the two services, at-least-once retry behaviour, and the loss/dup drill.

``fail_first`` injects ONE transient failure: the fake STORES the request, then returns
503 (modelling "processed but the ack was lost") — the worst case idempotency defends
against. The client retries the identical call; the fake dedups and returns success.
"""
from __future__ import annotations

import hashlib
import json

import httpx


def deterministic_record_id(envelope: dict) -> str:
    """Stand-in for C2 record_id: deterministic on chunk_id (data-processing owns the real one)."""
    basis = f"{envelope['chunk_id']}::mock-pipeline-v0"
    return hashlib.sha256(basis.encode()).hexdigest()[:32]


class FakeStorage:
    """PUT /raw/blobs -> {blob_ref, bytes, sha256}. Idempotent on chunk_id."""

    def __init__(self, events: list, *, fail_first: bool = False) -> None:
        self.events = events
        self.blobs: dict[str, dict] = {}   # chunk_id -> stored blob
        self.put_count = 0
        self._fail_first = fail_first
        self._failed = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT", request.method
        assert request.url.path == "/raw/blobs", request.url.path
        assert request.headers.get("content-type") == "application/octet-stream"
        params = request.url.params
        chunk_id = params["chunk_id"]
        data = request.content

        self.put_count += 1
        self.events.append(("PUT", chunk_id))

        # Integrity: the query-param sha256/bytes must match the body we received.
        assert params["sha256"] == hashlib.sha256(data).hexdigest()
        assert int(params["bytes"]) == len(data)

        # Idempotent create: opaque, storage-owned ref that may contain '/'.
        if chunk_id not in self.blobs:
            self.blobs[chunk_id] = {
                "blob_ref": f"raw/{params['user_id']}/{params['device_id']}/{chunk_id}.wav",
                "bytes": len(data),
                "sha256": params["sha256"],
            }
        rec = self.blobs[chunk_id]

        if self._fail_first and not self._failed:
            self._failed = True
            return httpx.Response(503, json={"error": "transient (stored, ack lost)"})
        return httpx.Response(200, json=rec)


class FakeDataProcessing:
    """POST /ingest (C1 envelope) -> {ok, record_ids:[...]}. Dedups on chunk_id.

    ``fanout`` mirrors data-processing's one-chunk-many-records case (e.g. video
    keyframes): each chunk yields ``fanout`` deterministic record_ids (default 1).
    """

    def __init__(self, events: list, *, fail_first: bool = False, fanout: int = 1) -> None:
        self.events = events
        self.records: dict[str, list[str]] = {}   # chunk_id -> [record_id, ...]
        self.envelopes: list[dict] = []      # every C1 received (dupes included)
        self.post_count = 0
        self._fail_first = fail_first
        self._failed = False
        self._fanout = fanout

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "POST", request.method
        assert request.url.path == "/ingest", request.url.path
        envelope = json.loads(request.content)
        chunk_id = envelope["chunk_id"]

        self.post_count += 1
        self.events.append(("POST", chunk_id))
        self.envelopes.append(envelope)

        if chunk_id not in self.records:
            base = deterministic_record_id(envelope)
            self.records[chunk_id] = (
                [base] if self._fanout == 1
                else [f"{base}-{i}" for i in range(self._fanout)]
            )
        record_ids = self.records[chunk_id]

        if self._fail_first and not self._failed:
            self._failed = True
            return httpx.Response(503, json={"error": "transient (stored, ack lost)"})
        return httpx.Response(200, json={"ok": True, "record_ids": record_ids})

    def unique_envelopes(self) -> list[dict]:
        """First-seen C1 per chunk_id, in arrival order (drops retry duplicates)."""
        seen: set[str] = set()
        out: list[dict] = []
        for env in self.envelopes:
            if env["chunk_id"] not in seen:
                seen.add(env["chunk_id"])
                out.append(env)
        return out
