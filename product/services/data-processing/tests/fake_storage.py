"""An in-process fake storage service, wired via an httpx MockTransport.

Serves the two endpoints data-processing calls:
  * GET  /raw/blobs?ref=<blob_ref>   -> the registered bytes (404 if unknown)
  * POST /context/records            -> records the C2 body (idempotent on record_id)

Because it is a MockTransport (no real socket), the test can inspect exactly what
was fetched and written — and count how many times /context was POSTed, which is
how the dedup guarantee ('storage POSTed at most once') is proven.
"""
from __future__ import annotations

import json
from typing import Any

import httpx


class FakeStorage:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}          # blob_ref -> bytes
        self.records: dict[str, dict[str, Any]] = {}  # record_id -> C2 (upsert)
        self.blob_gets: list[str] = []             # every ref fetched, in order
        self.record_posts: list[dict[str, Any]] = []  # every C2 posted, in order

    def add_blob(self, blob_ref: str, data: bytes) -> None:
        self.blobs[blob_ref] = data

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/raw/blobs":
            ref = request.url.params.get("ref")
            self.blob_gets.append(ref)
            data = self.blobs.get(ref)
            if data is None:
                return httpx.Response(404, json={"error": "no such ref", "ref": ref})
            return httpx.Response(
                200, content=data, headers={"content-type": "application/octet-stream"}
            )
        if request.method == "POST" and path == "/context/records":
            body = json.loads(request.content)
            self.record_posts.append(body)
            record_id = body["record_id"]
            self.records[record_id] = body  # idempotent upsert
            return httpx.Response(200, json={"ok": True, "record_id": record_id})
        return httpx.Response(
            404, json={"error": f"unhandled {request.method} {path}"}
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)
