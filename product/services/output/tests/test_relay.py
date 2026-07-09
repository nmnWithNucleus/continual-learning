"""Relay service tests: /deliver proxies a C9 stream from an upstream URL to the
caller byte-for-byte unchanged, with a delivery ack; /health responds.

Upstream is faked with httpx.MockTransport so no network or GPU is needed. The
relayed body is fed back through the C9 parser to prove it survived intact and
still yields a schema-valid end frame."""

from __future__ import annotations

import json

import httpx
import jsonschema
import pytest
from fastapi.testclient import TestClient

from app.c9_parse import parse_c9_bytes, build_c9_stream
from app.main import create_app

VALID_END_FRAME = {
    "contract": "C9",
    "version": "0",
    "turn_id": "turn-relay-1",
    "model_id": "Qwen/Qwen3-VL-32B-Instruct",
    "adapter": "base",
    "usage": {"prompt_tokens": 5, "output_tokens": 9},
    "finished": True,
}

UPSTREAM_URL = "http://inference.local:8010/infer"


def make_client(handler) -> TestClient:
    """A TestClient over the app, with the app's httpx client mounted on a
    MockTransport handler so upstream calls are intercepted in-process."""
    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(client=mock)
    return TestClient(app)


def test_health():
    client = make_client(lambda req: httpx.Response(200))
    with client:
        r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "output"
    assert body["status"] == "ok"
    assert "C9" in body["consumes"]


def test_index():
    client = make_client(lambda req: httpx.Response(200))
    with client:
        r = client.get("/")
    assert r.status_code == 200
    assert r.json()["service"] == "output"


def test_deliver_relays_c9_unchanged(c9_schema):
    upstream_body = build_c9_stream(
        "# Answer\n\nStreamed **verbatim** through the relay.", VALID_END_FRAME
    )

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["json"] = json.loads(request.content) if request.content else None
        return httpx.Response(200, content=upstream_body,
                              headers={"Content-Type": "text/plain; charset=utf-8"})

    client = make_client(handler)
    payload = {"contract": "C3", "version": "0", "text_probe": "hello"}
    with client:
        r = client.post("/deliver", json={
            "upstream_url": UPSTREAM_URL,
            "payload": payload,
            "turn_id": "turn-relay-1",
        })

    assert r.status_code == 200
    # Body relayed byte-for-byte.
    assert r.content == upstream_body
    # Delivery ack rides in headers; the C9 body is left pristine.
    assert r.headers["x-delivery-ack"] == "accepted"
    assert r.headers["x-delivery-turn-id"] == "turn-relay-1"
    assert r.headers["x-delivery-upstream"] == UPSTREAM_URL
    assert r.headers["x-delivery-id"]
    # Upstream received our payload + method + url.
    assert captured["url"] == UPSTREAM_URL
    assert captured["method"] == "POST"
    assert captured["json"] == payload
    # The relayed bytes still parse into the correct answer + schema-valid frame.
    answer, end_frame = parse_c9_bytes(r.content)
    assert answer == "# Answer\n\nStreamed **verbatim** through the relay."
    assert end_frame == VALID_END_FRAME
    jsonschema.validate(instance=end_frame, schema=c9_schema)


def test_deliver_relays_large_stream_intact(c9_schema):
    """A large upstream body is relayed (re-chunked by httpx) with bytes intact."""
    answer = "chunked answer " * 200
    upstream_body = build_c9_stream(answer.strip(), VALID_END_FRAME)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=upstream_body,
                              headers={"Content-Type": "text/plain; charset=utf-8"})

    client = make_client(handler)
    with client:
        r = client.post("/deliver", json={"upstream_url": UPSTREAM_URL, "turn_id": "turn-relay-1"})

    assert r.status_code == 200
    assert r.content == upstream_body
    parsed_answer, end_frame = parse_c9_bytes(r.content)
    assert parsed_answer == answer.strip()
    jsonschema.validate(instance=end_frame, schema=c9_schema)


def test_deliver_upstream_error_yields_c9_error_frame(c9_schema):
    """If upstream raises, the caller still gets a valid C9 error end frame."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = make_client(handler)
    with client:
        r = client.post("/deliver", json={"upstream_url": UPSTREAM_URL, "turn_id": "turn-x"})

    assert r.status_code == 200  # headers already sent before the stream body
    answer, end_frame = parse_c9_bytes(r.content)
    assert answer == ""  # no answer bytes were produced
    assert "error" in end_frame
    assert end_frame["turn_id"] == "turn-x"
    jsonschema.validate(instance=end_frame, schema=c9_schema)


def test_deliver_upstream_http_error_status_yields_error_frame(c9_schema):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"service unavailable")

    client = make_client(handler)
    with client:
        r = client.post("/deliver", json={"upstream_url": UPSTREAM_URL, "turn_id": "turn-503"})

    assert r.status_code == 200
    _, end_frame = parse_c9_bytes(r.content)
    assert "error" in end_frame
    assert "503" in end_frame["error"]
    jsonschema.validate(instance=end_frame, schema=c9_schema)
