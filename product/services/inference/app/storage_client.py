"""Storage-service client: C6 model resolution + C4 turn persistence.

Storage owns both stores; inference only calls two endpoints:
  * GET  {STORAGE_URL}/model-directory/resolve?user_id=...  -> C6 resolve
  * POST {STORAGE_URL}/sessions/turns   (body = C4 turn record)
"""
from __future__ import annotations

import logging

import httpx

from .config import Settings

logger = logging.getLogger("inference.storage")


def _base_fallback(settings: Settings) -> dict:
    """The trivial v0 resolution used when the directory is unreachable.

    Matches C6 v0 semantics: base model, no adapter. Storage's own charter names
    'fallback to base model if directory unreachable' as the required behavior,
    so a resolve outage degrades gracefully instead of failing the turn.
    """
    return {"model_id": settings.model_id, "adapter": "base", "adapter_path": None}


async def resolve_model(settings: Settings, user_id: str) -> dict:
    """C6: resolve the model/adapter for a user. Falls back to base on error."""
    url = f"{settings.storage_url}/model-directory/resolve"
    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
            resp = await client.get(url, params={"user_id": user_id})
            resp.raise_for_status()
            data = resp.json()
        # Defensive: fill any missing v0 fields rather than trust blindly.
        return {
            "model_id": data.get("model_id") or settings.model_id,
            "adapter": data.get("adapter") or "base",
            "adapter_path": data.get("adapter_path"),
        }
    except Exception as exc:  # noqa: BLE001 - degrade to base on any failure
        logger.warning("C6 resolve failed (%s); falling back to base model", exc)
        return _base_fallback(settings)


async def write_turn(settings: Settings, turn_record: dict) -> None:
    """C4: persist the turn record to storage /sessions/turns.

    Raises on failure so the caller can log it; the answer has already been
    streamed to the user by the time this runs, so a failure here loses the
    persisted turn but not the user's answer.
    """
    url = f"{settings.storage_url}/sessions/turns"
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.post(url, json=turn_record)
        resp.raise_for_status()
