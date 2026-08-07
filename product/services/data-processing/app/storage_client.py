"""Storage-service client: pull raw blobs + write C2 records.

Storage owns both stores; data-processing calls exactly two endpoints:
  * GET  {STORAGE_URL}/raw/blobs?ref=<blob_ref>   -> 200 raw bytes body
  * POST {STORAGE_URL}/context/records            (body = C2 JSON) -> {ok, record_id}

The ``ref`` is passed as a QUERY PARAM (not a path segment) because a blob_ref is
an opaque storage-owned string that may contain '/'.

An optional httpx transport can be injected (tests bind a MockTransport); in
production it is None and httpx uses its default networking.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx


class StorageClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, transport=self._transport)

    async def get_blob(self, blob_ref: str) -> bytes:
        """Pull the raw chunk bytes by opaque ref. Raises httpx.HTTPStatusError on
        a non-2xx (e.g. 404 for a since-deleted blob)."""
        async with self._client() as client:
            resp = await client.get(
                f"{self.base_url}/raw/blobs", params={"ref": blob_ref}
            )
            resp.raise_for_status()
            return resp.content

    async def post_record(self, c2: dict[str, Any]) -> dict[str, Any]:
        """Write a C2 record (idempotent upsert on record_id at storage)."""
        async with self._client() as client:
            resp = await client.post(f"{self.base_url}/context/records", json=c2)
            resp.raise_for_status()
            return resp.json()
