"""Shared fixtures: a TestClient whose downstream httpx calls hit in-process fakes.

We monkeypatch the ``clients.async_client`` seam so both StorageClient and
DataProcessingClient get an ``httpx.AsyncClient`` backed by an ``httpx.MockTransport``
(the fake storage / data-processing). Nothing binds a real port — the integrator owns
live ports; here the real httpx request/response path is exercised against fakes.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest
from fastapi.testclient import TestClient

from app import clients
from tests.fakes import FakeDataProcessing, FakeStorage

STORAGE_URL = "http://storage.mock"
DP_URL = "http://dp.mock"


@dataclass
class Wiring:
    client: TestClient
    storage: FakeStorage
    dp: FakeDataProcessing
    events: list

    def run(self, **overrides) -> dict:
        """POST /capture/run and return the parsed JSON body (asserts 200)."""
        body = {"storage_url": STORAGE_URL, "dp_url": DP_URL, "base_wallclock": "2026-07-09T12:00:00Z"}
        body.update(overrides)
        resp = self.client.post("/capture/run", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()


def _make_wiring(monkeypatch, *, storage_fail_first=False, dp_fail_first=False, dp_fanout=1) -> Wiring:
    monkeypatch.setenv("RECORDING_RETRY_BACKOFF", "0")   # no sleeps between retries
    events: list = []
    storage = FakeStorage(events, fail_first=storage_fail_first)
    dp = FakeDataProcessing(events, fail_first=dp_fail_first, fanout=dp_fanout)

    def fake_async_client(base_url: str, timeout: float) -> httpx.AsyncClient:
        handler = storage if "storage" in base_url else dp
        return httpx.AsyncClient(
            base_url=base_url, timeout=timeout, transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(clients, "async_client", fake_async_client)

    from app.main import app

    return Wiring(client=TestClient(app), storage=storage, dp=dp, events=events)


@pytest.fixture()
def wiring(monkeypatch) -> Wiring:
    """Happy-path wiring: fakes never fail."""
    return _make_wiring(monkeypatch)


@pytest.fixture()
def make_wiring(monkeypatch):
    """Factory for fault-injected wiring (drills)."""
    def _factory(*, storage_fail_first=False, dp_fail_first=False, dp_fanout=1) -> Wiring:
        return _make_wiring(
            monkeypatch,
            storage_fail_first=storage_fail_first,
            dp_fail_first=dp_fail_first,
            dp_fanout=dp_fanout,
        )
    return _factory
