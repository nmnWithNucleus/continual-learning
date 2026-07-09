"""Integration-ish tests for /api/turn: it builds a valid C3 and relays the C9
stream from inference byte-for-byte. Inference is stubbed at the httpx-client
seam (`_client_factory`) — no live inference server needed.
"""

import json
from pathlib import Path

import httpx
import jsonschema
import pytest
from fastapi.testclient import TestClient

from app import main

SEP = chr(0x1E)  # U+001E record separator
CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"


def _schema(name: str) -> dict:
    with open(CONTRACTS_DIR / name, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# httpx stubs
# ---------------------------------------------------------------------------
class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c


class FakeClient:
    """Records the C3 it was asked to POST and replays canned C9 bytes."""

    captured = None

    def __init__(self, chunks):
        self._chunks = chunks

    def stream(self, method, url, json=None):
        FakeClient.captured = {"method": method, "url": url, "json": json}
        return _FakeStream(self._chunks)

    async def aclose(self):
        pass


class _FailingStream:
    async def __aenter__(self):
        raise httpx.ConnectError("connection refused")

    async def __aexit__(self, *exc):
        return False


class FailingClient:
    def stream(self, *a, **k):
        return _FailingStream()

    async def aclose(self):
        pass


def _canned_c9(answer="The capital of France is Paris.", turn_hint="turn"):
    end_frame = {
        "contract": "C9",
        "version": "0",
        "turn_id": "will-be-overwritten-by-inference",
        "model_id": "Qwen/Qwen3-VL-32B-Instruct",
        "adapter": "base",
        "usage": {"prompt_tokens": 11, "output_tokens": 7},
        "finished": True,
    }
    # Answer split across chunks + separator + end frame, mimicking a real stream.
    return [
        answer[:12].encode(),
        answer[12:].encode(),
        SEP.encode(),
        json.dumps(end_frame).encode(),
    ]


@pytest.fixture
def client():
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
def test_relays_c9_stream_byte_for_byte(client, monkeypatch):
    chunks = _canned_c9()
    monkeypatch.setattr(main, "_client_factory", lambda: FakeClient(chunks))

    resp = client.post("/api/turn", json={"text": "What is the capital of France?"})
    assert resp.status_code == 200
    # byte-for-byte relay
    assert resp.content == b"".join(chunks)

    # and it parses per the C9 wire format
    body = resp.content.decode()
    assert SEP in body
    answer, frame_str = body.split(SEP, 1)
    assert answer == "The capital of France is Paris."
    frame = json.loads(frame_str)
    jsonschema.validate(instance=frame, schema=_schema("c9_response_stream.v0.json"))


def test_sends_schema_valid_c3_to_inference(client, monkeypatch):
    monkeypatch.setattr(main, "_client_factory", lambda: FakeClient(_canned_c9()))

    text = "explain photosynthesis"
    resp = client.post("/api/turn", json={"text": text})
    assert resp.status_code == 200

    cap = FakeClient.captured
    assert cap["method"] == "POST"
    assert cap["url"].endswith("/infer")
    c3 = cap["json"]
    # the C3 we handed inference is contract-valid
    jsonschema.validate(instance=c3, schema=_schema("c3_userprompt.v0.json"))
    assert c3["messages"][0]["text"] == text
    assert c3["client_capabilities"]["surface"] == "computer"
    # turn_id we minted is echoed in the response header
    assert resp.headers["X-Turn-Id"] == c3["turn_id"]
    assert resp.headers["X-Session-Id"] == c3["session_id"]


def test_mints_session_when_absent(client, monkeypatch):
    monkeypatch.setattr(main, "_client_factory", lambda: FakeClient(_canned_c9()))
    resp = client.post("/api/turn", json={"text": "hi"})
    sid = resp.headers["X-Session-Id"]
    assert sid.startswith("sess-")


def test_session_id_passthrough(client, monkeypatch):
    monkeypatch.setattr(main, "_client_factory", lambda: FakeClient(_canned_c9()))
    resp = client.post("/api/turn", json={"text": "hi", "session_id": "sess-keep-me"})
    assert resp.headers["X-Session-Id"] == "sess-keep-me"
    assert FakeClient.captured["json"]["session_id"] == "sess-keep-me"


def test_empty_text_is_400(client):
    resp = client.post("/api/turn", json={"text": "   "})
    assert resp.status_code == 400
    assert "error" in resp.json()


def test_unreachable_inference_emits_valid_c9_error_frame(client, monkeypatch):
    monkeypatch.setattr(main, "_client_factory", lambda: FailingClient())
    resp = client.post("/api/turn", json={"text": "hi"})
    assert resp.status_code == 200
    body = resp.content.decode()
    assert body.startswith(SEP)  # empty answer, then separator, then end frame
    frame = json.loads(body[len(SEP):])
    jsonschema.validate(instance=frame, schema=_schema("c9_response_stream.v0.json"))
    assert frame["error"]
    assert frame["finished"] is True
    # error frame carries the turn_id we minted
    assert frame["turn_id"] == resp.headers["X-Turn-Id"]


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "input"


def test_index_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Nucleus" in resp.text
    assert "/static/app.js" in resp.text
