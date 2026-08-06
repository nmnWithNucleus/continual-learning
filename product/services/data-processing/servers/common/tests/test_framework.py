"""Wire-contract tests for the servers/common framework (WP-B1).

The contract every model server speaks (plan §3, L9):

  GET  /health   -> 200 {"ok": true,  "status": "ready", "server", "identity"} once warm
                 -> 503 {"ok": false, "status": "warming", "server"} while loading
                 -> 500 {"ok": false, "status": "load_failed", "server", "error"} if load raised
  POST /infer    -> 200 {"ok": true,  "result": {...}}
                 -> 422 {"ok": false, "error", "transient": false}  (bad input / deterministic)
                 -> 503 {"ok": false, "error", "transient": true}   (not warm yet / transient)
                 -> 500 {"ok": false, "error", "transient": true}   (unexpected backend crash)

Identity (the L4 hook): /health.identity must carry model_name, weights, frameworks,
device — a client must be able to check what the server actually runs.
"""
from __future__ import annotations

import base64
import threading
import time

from fastapi.testclient import TestClient

from dp_servers_common.app import build_app
from dp_servers_common.backend import BackendError, ModelBackend, TransientBackendError
from dp_servers_common.wire import InferRequest


class EchoBackend(ModelBackend):
    """Minimal real backend: echoes input length + params after a controllable load."""

    name = "echo"

    def __init__(self, load_delay_s: float = 0.0, load_error: Exception | None = None):
        self.load_delay_s = load_delay_s
        self.load_error = load_error
        self.loaded = threading.Event()

    def load(self) -> None:
        if self.load_delay_s:
            time.sleep(self.load_delay_s)
        if self.load_error is not None:
            raise self.load_error
        self.loaded.set()

    def identity(self) -> dict:
        return {
            "model_name": "echo-model",
            "weights": {"revision": "deadbeef"},
            "frameworks": {"echo": "1.0"},
            "device": "cpu",
        }

    def infer(self, request: InferRequest) -> dict:
        if request.params.get("explode") == "deterministic":
            raise BackendError("bad input")
        if request.params.get("explode") == "transient":
            raise TransientBackendError("replica hiccup")
        if request.params.get("explode") == "unexpected":
            raise RuntimeError("surprise")
        payload = base64.b64decode(request.input_b64 or "")
        return {"n_bytes": len(payload), "codec": request.codec, "params": request.params}


def ready_client(backend: ModelBackend | None = None) -> tuple[TestClient, ModelBackend]:
    backend = backend or EchoBackend()
    client = TestClient(build_app(backend))
    client.__enter__()  # run lifespan -> warmup thread
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if client.get("/health").status_code in (200, 500):
            break
        time.sleep(0.02)
    return client, backend


def test_health_ready_reports_full_identity():
    client, _ = ready_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "ready"
    assert body["server"] == "echo"
    identity = body["identity"]
    assert identity["model_name"] == "echo-model"
    assert identity["weights"] == {"revision": "deadbeef"}
    assert identity["frameworks"] == {"echo": "1.0"}
    assert identity["device"] == "cpu"


def test_health_warming_is_503_and_infer_transient():
    backend = EchoBackend(load_delay_s=30.0)
    client = TestClient(build_app(backend))
    client.__enter__()
    health = client.get("/health")
    assert health.status_code == 503
    assert health.json() == {"ok": False, "status": "warming", "server": "echo"}
    infer = client.post("/infer", json={"params": {}})
    assert infer.status_code == 503
    assert infer.json()["transient"] is True


def test_health_load_failure_is_500_load_failed():
    client, _ = ready_client(EchoBackend(load_error=RuntimeError("weights missing")))
    resp = client.get("/health")
    assert resp.status_code == 500
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "load_failed"
    assert "weights missing" in body["error"]


def test_infer_roundtrip():
    client, _ = ready_client()
    payload = base64.b64encode(b"hello bytes").decode()
    resp = client.post(
        "/infer",
        json={"input_b64": payload, "codec": "audio/webm", "params": {"k": 1}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["result"] == {"n_bytes": 11, "codec": "audio/webm", "params": {"k": 1}}


def test_infer_deterministic_error_is_422_not_transient():
    client, _ = ready_client()
    resp = client.post("/infer", json={"params": {"explode": "deterministic"}})
    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert body["transient"] is False
    assert "bad input" in body["error"]


def test_infer_transient_error_is_503_transient():
    client, _ = ready_client()
    resp = client.post("/infer", json={"params": {"explode": "transient"}})
    assert resp.status_code == 503
    assert resp.json()["transient"] is True


def test_infer_unexpected_error_is_500_transient():
    client, _ = ready_client()
    resp = client.post("/infer", json={"params": {"explode": "unexpected"}})
    assert resp.status_code == 500
    body = resp.json()
    assert body["ok"] is False
    assert body["transient"] is True


def test_malformed_infer_body_is_422():
    client, _ = ready_client()
    resp = client.post("/infer", json={"input_b64": 12345})
    assert resp.status_code == 422


def test_infer_calls_are_serialized_one_at_a_time():
    """Models are not assumed thread-safe: concurrent /infer must not overlap.

    Two slow calls fired together must take ~2x one call's duration."""

    class SlowBackend(EchoBackend):
        def infer(self, request: InferRequest) -> dict:
            time.sleep(0.3)
            return {"done": True}

    client, _ = ready_client(SlowBackend())
    t0 = time.monotonic()
    results = []

    def call():
        results.append(client.post("/infer", json={"params": {}}).status_code)

    threads = [threading.Thread(target=call) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - t0
    assert results == [200, 200]
    assert elapsed >= 0.55, f"calls overlapped: {elapsed:.2f}s"
