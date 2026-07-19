"""HTTP clients for the two downstream services, with at-least-once retry.

Blob leg  -> storage  PUT /raw/blobs   (bytes land in /raw, idempotent on chunk_id)
Envelope  -> data-processing POST /ingest  (the C1 push receiver, dedup on chunk_id)

DELIVERY SEMANTICS (frozen): push, at-least-once. Both consumers are idempotent on
``chunk_id`` (the dedup key), so a retry that duplicates a request downstream still has
an exactly-once *effect* (no dup blob, no dup record). A retry MUST reuse the same
chunk_id and MUST NOT advance sequence — both are properties of the request being
retried, so re-issuing the identical call preserves them for free.

Only transient failures are retried: transport errors and 5xx responses. A 4xx is a
permanent (malformed) request — surfaced immediately, not retried.

``async_client`` is a module-level seam so unit tests can mount an httpx MockTransport
(a fake, idempotent storage / data-processing) without a live port.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from .metrics import record_retry

logger = logging.getLogger("recording.clients")


def _service_for(url: str) -> str:
    """Tag a downstream by its path so the retry counter is per-target (bounded)."""
    if url.startswith("/raw"):
        return "storage"
    if url.startswith("/ingest"):
        return "data-processing"
    return "other"


def async_client(base_url: str, timeout: float) -> httpx.AsyncClient:
    """Construct the AsyncClient for a downstream service. Test seam (monkeypatched)."""
    return httpx.AsyncClient(base_url=base_url, timeout=timeout)


async def _request_with_retry(
    ac: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    attempts: int,
    backoff: float,
    **kwargs,
) -> httpx.Response:
    """Issue one request, retrying transient failures. Re-issues the IDENTICAL call,
    so chunk_id/sequence (carried in params/body) are preserved across retries."""
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = await ac.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            last_exc = exc
            logger.warning("%s %s transport error (attempt %d/%d): %s",
                           method, url, attempt, attempts, exc)
        else:
            if resp.status_code < 500:
                resp.raise_for_status()   # 4xx -> permanent, raise now
                return resp
            last_exc = httpx.HTTPStatusError(
                f"server error {resp.status_code}", request=resp.request, response=resp
            )
            logger.warning("%s %s -> %d (attempt %d/%d)",
                           method, url, resp.status_code, attempt, attempts)
        if attempt >= attempts:
            break
        record_retry(_service_for(url))  # about to reissue -> count the retry (D9)
        if backoff:
            await asyncio.sleep(backoff * attempt)
    assert last_exc is not None
    raise last_exc


class StorageClient:
    """storage /raw blob leg. PUT bytes -> {blob_ref, bytes, sha256} (idempotent on chunk_id)."""

    def __init__(self, base_url: str, *, timeout: float, attempts: int, backoff: float) -> None:
        self._ac = async_client(base_url, timeout)
        self._attempts = attempts
        self._backoff = backoff

    async def aclose(self) -> None:
        await self._ac.aclose()

    async def put_blob(
        self,
        *,
        user_id: str,
        device_id: str,
        chunk_id: str,
        codec: str,
        sha256: str,
        nbytes: int,
        data: bytes,
    ) -> dict:
        params = {
            "user_id": user_id,
            "device_id": device_id,
            "chunk_id": chunk_id,
            "codec": codec,
            "sha256": sha256,
            "bytes": nbytes,
        }
        resp = await _request_with_retry(
            self._ac,
            "PUT",
            "/raw/blobs",
            attempts=self._attempts,
            backoff=self._backoff,
            params=params,
            content=data,
            headers={"content-type": "application/octet-stream"},
        )
        return resp.json()


class DataProcessingClient:
    """data-processing /ingest — the C1 push receiver. POST C1 -> {ok, record_ids:[...]}."""

    def __init__(self, base_url: str, *, timeout: float, attempts: int, backoff: float) -> None:
        self._ac = async_client(base_url, timeout)
        self._attempts = attempts
        self._backoff = backoff

    async def aclose(self) -> None:
        await self._ac.aclose()

    async def ingest(self, envelope: dict) -> dict:
        resp = await _request_with_retry(
            self._ac,
            "POST",
            "/ingest",
            attempts=self._attempts,
            backoff=self._backoff,
            json=envelope,
        )
        return resp.json()
