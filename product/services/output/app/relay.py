"""C9 relay core — proxy a C9 stream from an upstream URL to the caller unchanged.

Kept separate from the FastAPI wiring so it can be unit-tested with an injected
httpx client (e.g. ``httpx.MockTransport``) without a live network. The relay
copies upstream bytes through verbatim so the C9 wire format is preserved
byte-for-byte; a delivery ack rides in the response headers (see ``main.py``).
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, Optional

import httpx

# U+001E RECORD SEPARATOR — kept local so an error frame we synthesise is valid C9.
RECORD_SEPARATOR = "\u001e"


def build_ack(delivery_id: str, turn_id: Optional[str], upstream_url: str) -> Dict[str, str]:
    """Response headers acknowledging that the relay accepted + is relaying the turn."""
    return {
        "X-Delivery-Id": delivery_id,
        "X-Delivery-Turn-Id": turn_id or "",
        "X-Delivery-Upstream": upstream_url,
        "X-Delivery-Ack": "accepted",
        "Cache-Control": "no-store",
    }


def _error_frame(turn_id: Optional[str], message: str) -> bytes:
    """A minimal, schema-valid C9 error end frame, prefixed with the separator.

    Emitted only if the upstream connection fails mid-relay, so the caller still
    receives a well-formed C9 terminus (partial answer text + error end frame).
    """
    frame = {
        "contract": "C9",
        "version": "0",
        "turn_id": turn_id or "",
        "model_id": "",
        "adapter": "base",
        "finished": False,
        "error": message,
    }
    return (RECORD_SEPARATOR + json.dumps(frame)).encode("utf-8")


async def relay_c9(
    client: httpx.AsyncClient,
    upstream_url: str,
    *,
    method: str = "POST",
    payload: Optional[Dict[str, Any]] = None,
    turn_id: Optional[str] = None,
) -> AsyncIterator[bytes]:
    """Open a streaming request to ``upstream_url`` and yield its bytes unchanged.

    If the upstream errors before or during streaming, a synthesized C9 error end
    frame is appended so the caller always sees a valid terminus.
    """
    try:
        kwargs: Dict[str, Any] = {}
        if payload is not None:
            kwargs["json"] = payload
        async with client.stream(method, upstream_url, **kwargs) as upstream:
            if upstream.status_code >= 400:
                # Drain so the connection can be reused, then report as an error frame.
                body = (await upstream.aread()).decode("utf-8", "replace")[:500]
                yield _error_frame(turn_id, f"upstream HTTP {upstream.status_code}: {body}")
                return
            async for chunk in upstream.aiter_bytes():
                if chunk:
                    yield chunk
    except Exception as exc:  # noqa: BLE001 - any transport failure -> error frame
        yield _error_frame(turn_id, f"relay upstream error: {exc}")
