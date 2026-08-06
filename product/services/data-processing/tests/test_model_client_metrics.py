"""WP-D3 — the server-call observability family in ModelClient (Stage C cleanup
§C assigned it here): one counter per call outcome, a transient-retry counter, an
identity-failure counter, and a per-server latency histogram. Declared in
main._setup_metrics; ModelClient records against whatever registry it is handed
(duck-typed — the servers/common suite keeps constructing it metrics-less).

Transport injection rides the same idiom the storage client uses
(``client._client = AsyncClient(transport=MockTransport(...))``) — no API seam.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.metrics import Metrics
from app.model_client import (
    IdentityMismatchError,
    ModelCallError,
    ModelClient,
    ModelUnavailableError,
)

IDENTITY = {"model": "mock-model"}


def _declare(metrics: Metrics) -> None:
    """Mirror main._setup_metrics' server-call declarations."""
    metrics.declare_counter(
        "dp_server_calls_total", "Model-server calls by outcome.",
        ["server", "outcome"],
    )
    metrics.declare_counter(
        "dp_server_call_transient_retries_total",
        "Transient replica failures retried on another replica.", ["server"],
    )
    metrics.declare_counter(
        "dp_server_identity_failures_total",
        "Replica identity verifications that failed (wrong model).", ["server"],
    )
    metrics.declare_histogram(
        "dp_server_call_seconds",
        "Per-attempt /infer latency (HTTP-answered attempts only).", ["server"],
    )


def _client(handler, metrics, endpoints=("http://replica-a",)) -> ModelClient:
    c = ModelClient("mock", list(endpoints), {"model": "mock-model"},
                    metrics=metrics)
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


def _health(request: httpx.Request) -> httpx.Response | None:
    if request.url.path == "/health":
        return httpx.Response(200, json={"identity": IDENTITY})
    return None


def test_ok_call_counts_and_observes_latency():
    metrics = Metrics()
    _declare(metrics)

    def handler(request):
        return _health(request) or httpx.Response(200, json={"result": {"x": 1}})

    c = _client(handler, metrics)
    assert asyncio.run(c.infer({"input_b64": ""})) == {"x": 1}
    rendered = metrics.render()
    assert 'dp_server_calls_total{server="mock",outcome="ok"} 1' in rendered
    assert 'dp_server_call_seconds_count{server="mock"} 1' in rendered


def test_deterministic_error_outcome():
    metrics = Metrics()
    _declare(metrics)

    def handler(request):
        return _health(request) or httpx.Response(
            422, json={"error": "bad input", "transient": False})

    c = _client(handler, metrics)
    with pytest.raises(ModelCallError):
        asyncio.run(c.infer({"input_b64": ""}))
    rendered = metrics.render()
    assert ('dp_server_calls_total{server="mock",outcome="deterministic_error"} 1'
            in rendered)


def test_transient_retries_then_unavailable():
    metrics = Metrics()
    _declare(metrics)

    def handler(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"identity": IDENTITY})
        return httpx.Response(503, json={"error": "warming", "transient": True})

    c = _client(handler, metrics, endpoints=("http://a", "http://b"))
    with pytest.raises(ModelUnavailableError):
        asyncio.run(c.infer({"input_b64": ""}))
    rendered = metrics.render()
    # Default budget: 1 + 2 retries = 3 attempts, each a transient presentation.
    assert 'dp_server_call_transient_retries_total{server="mock"} 3' in rendered
    assert 'dp_server_calls_total{server="mock",outcome="unavailable"} 1' in rendered
    # 5xx-answered attempts still land in the latency histogram (HTTP-answered).
    assert 'dp_server_call_seconds_count{server="mock"} 3' in rendered


def test_identity_mismatch_counts():
    metrics = Metrics()
    _declare(metrics)

    def handler(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"identity": {"model": "WRONG"}})
        return httpx.Response(200, json={"result": {}})

    c = _client(handler, metrics)
    with pytest.raises(IdentityMismatchError):
        asyncio.run(c.infer({"input_b64": ""}))
    rendered = metrics.render()
    assert 'dp_server_identity_failures_total{server="mock"} 1' in rendered
    assert ('dp_server_calls_total{server="mock",outcome="identity_mismatch"} 1'
            in rendered)


def test_metrics_less_construction_stays_silent():
    """servers/common's suite constructs ModelClient without metrics — the
    parameter is optional and nothing records."""
    def handler(request):
        return _health(request) or httpx.Response(200, json={"result": {}})

    c = ModelClient("mock", ["http://a"], {"model": "mock-model"})
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert asyncio.run(c.infer({"input_b64": ""})) == {}
