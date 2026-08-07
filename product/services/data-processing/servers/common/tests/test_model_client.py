"""Tests for app/model_client.py: replica pick, identity verification,
per-call timeout, bounded transient retry against other replicas.

The fake fleet here is the real framework (build_app + uvicorn on loopback), so these
tests also exercise the framework over actual HTTP — the same wire the DP stages will
use in
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from dp_servers_common.app import build_app
from dp_servers_common.backend import (
 BackendError,
 ModelBackend,
 TransientBackendError,
)
from dp_servers_common.wire import InferRequest

from app.model_client import (
 IdentityMismatchError,
 ModelCallError,
 ModelClient,
 ModelUnavailableError,
)

IDENTITY = {
 "model_name": "echo-model",
 "weights": {"revision": "deadbeef"},
 "frameworks": {"echo": "1.0"},
 "device": "cpu",
}


class CountingBackend(ModelBackend):
 name = "echo"

 def __init__(self, tag: str, mode: str = "ok", delay_s: float = 0.0,
 load_delay_s: float = 0.0, identity_extra: dict | None = None):
 self.tag = tag
 self.mode = mode
 self.delay_s = delay_s
 self.load_delay_s = load_delay_s
 self.identity_extra = identity_extra or {}
 self.calls = 0

 def load(self) -> None:
 time.sleep(self.load_delay_s)

 def identity(self) -> dict:
 return {**IDENTITY, **self.identity_extra}

 def infer(self, request: InferRequest) -> dict:
 self.calls += 1
 if self.delay_s:
 time.sleep(self.delay_s)
 if self.mode == "transient":
 raise TransientBackendError(f"{self.tag} transient")
 if self.mode == "deterministic":
 raise BackendError(f"{self.tag} deterministic")
 return {"served_by": self.tag}


class LiveServer:
 def __init__(self, backend: ModelBackend, port: int):
 self.backend = backend
 self.port = port
 config = uvicorn.Config(build_app(backend), host="127.0.0.1", port=port,
 log_level="warning")
 self.server = uvicorn.Server(config)
 self.thread = threading.Thread(target=self.server.run, daemon=True)

 def start(self):
 self.thread.start
 deadline = time.monotonic + 15
 while not self.server.started:
 if time.monotonic > deadline:
 raise RuntimeError("uvicorn did not start")
 time.sleep(0.01)
 return self

 def stop(self):
 self.server.should_exit = True
 self.thread.join(timeout=10)

 @property
 def url(self) -> str:
 return f"http://127.0.0.1:{self.port}"


def free_port -> int:
 with socket.socket as s:
 s.bind(("127.0.0.1", 0))
 return s.getsockname[1]


def fleet(*backends: ModelBackend) -> list[LiveServer]:
 return [LiveServer(b, free_port).start for b in backends]


def run(coro):
 return asyncio.run(coro)


def test_round_robin_spreads_calls_across_replicas:
 a, b = CountingBackend("a"), CountingBackend("b")
 servers = fleet(a, b)
 try:
 async def go:
 client = ModelClient("echo", [s.url for s in servers],
 expected_identity={"model_name": "echo-model"})
 try:
 await client.verify_identity
 for _ in range(4):
 await client.infer({"params": {}})
 finally:
 await client.aclose
 run(go)
 assert a.calls == 2 and b.calls == 2
 finally:
 for s in servers:
 s.stop


def test_identity_mismatch_raises_at_connect:
 a = CountingBackend("a", identity_extra={"model_name": "wrong-model"})
 servers = fleet(a)
 try:
 async def go:
 client = ModelClient("echo", [s.url for s in servers],
 expected_identity={"model_name": "echo-model"})
 try:
 with pytest.raises(IdentityMismatchError):
 await client.verify_identity
 finally:
 await client.aclose
 run(go)
 finally:
 for s in servers:
 s.stop


def test_expected_identity_is_subset_match:
 """Manifest pins a subset (model_name + weights.revision); extra reported
 identity fields (frameworks, device) must not trip verification."""
 servers = fleet(CountingBackend("a"))
 try:
 async def go:
 client = ModelClient(
 "echo", [s.url for s in servers],
 expected_identity={"model_name": "echo-model",
 "weights": {"revision": "deadbeef"}})
 try:
 await client.verify_identity
 return await client.infer({"params": {}})
 finally:
 await client.aclose
 assert run(go)["served_by"] == "a"
 finally:
 for s in servers:
 s.stop


def test_transient_failure_retries_on_other_replica:
 bad, good = CountingBackend("bad", mode="transient"), CountingBackend("good")
 servers = fleet(bad, good)
 try:
 async def go:
 client = ModelClient("echo", [s.url for s in servers],
 expected_identity={"model_name": "echo-model"},
 max_transient_retries=1)
 try:
 results = [await client.infer({"params": {}}) for _ in range(2)]
 finally:
 await client.aclose
 return results
 results = run(go)
 assert all(r["served_by"] == "good" for r in results)
 assert bad.calls >= 1
 finally:
 for s in servers:
 s.stop


def test_deterministic_failure_raises_immediately_without_retry:
 a = CountingBackend("a", mode="deterministic")
 b = CountingBackend("b", mode="deterministic")
 servers = fleet(a, b)
 try:
 async def go:
 client = ModelClient("echo", [s.url for s in servers],
 expected_identity={"model_name": "echo-model"},
 max_transient_retries=3)
 try:
 with pytest.raises(ModelCallError):
 await client.infer({"params": {}})
 finally:
 await client.aclose
 run(go)
 assert a.calls + b.calls == 1, "deterministic failure must not fan out"
 finally:
 for s in servers:
 s.stop


def test_all_replicas_transient_exhausts_into_unavailable:
 a = CountingBackend("a", mode="transient")
 b = CountingBackend("b", mode="transient")
 servers = fleet(a, b)
 try:
 async def go:
 client = ModelClient("echo", [s.url for s in servers],
 expected_identity={"model_name": "echo-model"},
 max_transient_retries=2)
 try:
 with pytest.raises(ModelUnavailableError):
 await client.infer({"params": {}})
 finally:
 await client.aclose
 run(go)
 assert a.calls + b.calls == 3 # first attempt + 2 retries, bounded
 finally:
 for s in servers:
 s.stop


def test_per_call_timeout_is_transient_and_bounded:
 slow = CountingBackend("slow", delay_s=1.0)
 servers = fleet(slow)
 try:
 async def go:
 client = ModelClient("echo", [s.url for s in servers],
 expected_identity={"model_name": "echo-model"},
 call_timeout_s=0.2, max_transient_retries=0)
 try:
 t0 = time.monotonic
 with pytest.raises(ModelUnavailableError):
 await client.infer({"params": {}})
 return time.monotonic - t0
 finally:
 await client.aclose
 elapsed = run(go)
 assert elapsed < 0.9, f"timeout not enforced: {elapsed:.2f}s"
 finally:
 for s in servers:
 s.stop


def test_dead_replica_is_skipped:
 good = CountingBackend("good")
 servers = fleet(good)
 dead_url = f"http://127.0.0.1:{free_port}"
 try:
 async def go:
 client = ModelClient("echo", [dead_url, servers[0].url],
 expected_identity={"model_name": "echo-model"},
 max_transient_retries=1)
 try:
 return [await client.infer({"params": {}}) for _ in range(2)]
 finally:
 await client.aclose
 results = run(go)
 assert all(r["served_by"] == "good" for r in results)
 finally:
 for s in servers:
 s.stop


def test_wrong_model_replica_is_never_silently_used:
 """L4 teeth: if a replica reports a different identity, infer must refuse to
 use it — even when it was unreachable at verify time and comes up later."""
 wrong = CountingBackend("wrong", identity_extra={"model_name": "other-model"})
 servers = fleet(wrong)
 try:
 async def go:
 client = ModelClient("echo", [servers[0].url],
 expected_identity={"model_name": "echo-model"})
 try:
 with pytest.raises((IdentityMismatchError, ModelUnavailableError)):
 await client.infer({"params": {}})
 finally:
 await client.aclose
 run(go)
 assert wrong.calls == 0
 finally:
 for s in servers:
 s.stop


# ---------------------------------------------------------------------------
# Cleanup round (2026-08-06): identity re-verification after HTTP-presented
# respawns — a crash+respawn completing between calls presents as a 5xx (or a
# 503-warming first contact), not as a transport error; verified must clear on
# BOTH presentations so whatever serves on that port next re-verifies.
# ---------------------------------------------------------------------------

def _scripted_client(responses):
 """A one-replica ModelClient whose wire is a MockTransport playing a script:
 each /infer POST consumes the next scripted (status, body); /health always
 answers ready with the expected identity, counting calls."""
 client = ModelClient("echo", ["http://replica-1"],
 expected_identity={"model_name": "echo-model"},
 max_transient_retries=2)
 health_calls = {"n": 0}
 script = list(responses)

 def handler(request: httpx.Request) -> httpx.Response:
 if request.url.path == "/health":
 health_calls["n"] += 1
 return httpx.Response(200, json={
 "status": "ready", "server": "echo",
 "identity": {"model_name": "echo-model"},
 })
 status, body = script.pop(0)
 return httpx.Response(status, json=body)

 client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
 return client, health_calls


@pytest.mark.parametrize("status,body", [
 (503, {"error": "not ready: warming", "transient": True}),
 (500, {"error": "RuntimeError: CUDA context lost", "transient": True}),
], ids=["warming-503", "crash-500"])
def test_5xx_presentation_clears_verified_and_reverifies(status, body):
 client, health_calls = _scripted_client([
 (status, body), # attempt 1: the respawn presentation
 (200, {"result": {"served_by": "post-respawn"}}), # attempt 2, post-verify
 ])
 replica = client._replicas[0]
 replica.verified = True # verified against the OLD process
 result = run(client.infer({"input_b64": "", "codec": "x", "params": {}}))
 assert result == {"served_by": "post-respawn"}
 # The 5xx cleared verified, so the retry re-verified /health identity
 # BEFORE the next serve — the L4 "never silently the wrong model" gate.
 assert health_calls["n"] == 1
 assert replica.verified is True
 run(client.aclose)


def test_transport_error_still_clears_verified:
 """The original hardening's presentation, pinned alongside."""
 calls = {"n": 0}

 def handler(request: httpx.Request) -> httpx.Response:
 if request.url.path == "/health":
 return httpx.Response(200, json={
 "status": "ready", "server": "echo",
 "identity": {"model_name": "echo-model"},
 })
 calls["n"] += 1
 if calls["n"] == 1:
 raise httpx.ConnectError("connection refused (respawning)")
 return httpx.Response(200, json={"result": {"served_by": "b"}})

 client = ModelClient("echo", ["http://replica-1"],
 expected_identity={"model_name": "echo-model"},
 max_transient_retries=2)
 client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
 replica = client._replicas[0]
 replica.verified = True
 assert run(client.infer({"input_b64": "", "codec": "x", "params": {}})) \
 == {"served_by": "b"}
 assert replica.verified is True # re-verified on the retry
 run(client.aclose)
