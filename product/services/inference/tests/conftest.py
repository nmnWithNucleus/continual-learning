"""Test fixtures: a live in-process storage stub + env wiring for the mock loop.

Keeps the suite hermetic — no external services, no GPU, no network beyond
localhost. The storage stub is a real uvicorn server (so inference's httpx calls
exercise the real HTTP path) but runs in this same process on a background
thread, so RECORDED_TURNS is directly inspectable.
"""
from __future__ import annotations

import contextlib
import socket
import threading
import time

import pytest
import uvicorn

from . import storage_stub


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def storage_server():
    """Start the storage stub on an ephemeral port; yield its base URL."""
    port = _free_port()
    config = uvicorn.Config(
        storage_stub.stub, host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for startup (uvicorn flips .started once the socket is listening).
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("storage stub failed to start")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture()
def mock_env(monkeypatch, storage_server):
    """Point inference at the stub and select the mock backend (no delay)."""
    monkeypatch.setenv("MODEL_BACKEND", "mock")
    monkeypatch.setenv("MOCK_TOKEN_DELAY", "0")
    monkeypatch.setenv("STORAGE_URL", storage_server)
    storage_stub.reset()
    yield
