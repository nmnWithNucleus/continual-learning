"""servers/whisper tests — real model, in-process TestClient, no ports bound.

Run:  cd servers/whisper && CUDA_VISIBLE_DEVICES=4 ./.venv/bin/python -m pytest tests/ -q

The session fixture starts ONE app (one model load, cold ~30-90 s) and records
the very first /health response so the framework-conformance warming check
(503 while warming) rides the same startup instead of loading twice.
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

WHISPER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WHISPER_DIR))  # make `import server` work under pytest

from server import WhisperBackend  # noqa: E402
from dp_servers_common.app import build_app  # noqa: E402

MANIFEST_PATH = WHISPER_DIR.parent / "manifest.json"  # read-only
FIXTURES = WHISPER_DIR / "tests" / "fixtures"
GOLDEN_PARAMS = {"task": "transcribe", "beam_size": 1, "language": "en", "vad": True}
WARM_TIMEOUT_S = 300


@pytest.fixture(scope="session")
def warm_client():
    """Yields (client, first_health_response) with the model warm."""
    app = build_app(WhisperBackend())
    with TestClient(app) as client:
        first = client.get("/health")
        deadline = time.time() + WARM_TIMEOUT_S
        while True:
            resp = client.get("/health")
            if resp.status_code == 200:
                break
            if resp.status_code == 500:
                pytest.fail(f"model load failed: {resp.json()}")
            if time.time() > deadline:
                pytest.fail(f"model not warm after {WARM_TIMEOUT_S}s")
            time.sleep(1.0)
        yield client, first


def _assert_subset(expected: dict, actual: dict, path: str = "identity") -> None:
    for key, want in expected.items():
        assert key in actual, f"{path}.{key} missing"
        if isinstance(want, dict):
            assert isinstance(actual[key], dict), f"{path}.{key} is not a dict"
            _assert_subset(want, actual[key], f"{path}.{key}")
        else:
            assert actual[key] == want, f"{path}.{key}: {actual[key]!r} != {want!r}"


def _infer(client: TestClient, payload_bytes: bytes, params: dict, codec="audio/webm"):
    return client.post("/infer", json={
        "input_b64": base64.b64encode(payload_bytes).decode("ascii"),
        "codec": codec,
        "params": params,
    })


def test_health_warming_503_then_ready_200(warm_client):
    client, first = warm_client
    # Framework conformance: while the model warms, /health must be 503 not-ok.
    assert first.status_code == 503, f"first /health was {first.status_code}"
    body = first.json()
    assert body["ok"] is False
    assert body["status"] == "warming"
    # Warm: 200 with ok:true.
    ready = client.get("/health")
    assert ready.status_code == 200
    ready_body = ready.json()
    assert ready_body["ok"] is True
    assert ready_body["status"] == "ready"
    assert ready_body["server"] == "whisper"


def test_identity_matches_manifest_and_shape(warm_client):
    client, _ = warm_client
    identity = client.get("/health").json()["identity"]
    manifest = json.loads(MANIFEST_PATH.read_text())
    expected = manifest["servers"]["whisper"]["expected_identity"]
    _assert_subset(expected, identity)
    # Full identity shape (the L4 hook): everything output-affecting, stated.
    assert identity["model_name"] == "Systran/faster-whisper-large-v3"
    assert identity["weights"] == {"revision": "edaa852ec7e145841d8ffdb056a99866b5f0a478"}
    assert identity["device"] == "cuda"
    assert identity["compute_type"] == "float16"
    for pkg in ("faster_whisper", "ctranslate2", "av"):
        assert isinstance(identity["frameworks"][pkg], str) and identity["frameworks"][pkg]


def test_golden_transcribe_exact(warm_client):
    client, _ = warm_client
    audio = (FIXTURES / "speech_two_speakers.webm").read_bytes()
    resp = _infer(client, audio, GOLDEN_PARAMS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    golden = json.loads((FIXTURES / "golden_transcribe.json").read_text())
    # Exact compare: measured bit-stable across 4 fresh-process runs, GPUs 4 and 5
    # (see fixtures/PROVENANCE.md). Any drift here is an identity change — fail loud.
    assert body["result"] == golden


def test_translate_task_serves_from_same_model(warm_client):
    client, _ = warm_client
    audio = (FIXTURES / "speech_two_speakers.webm").read_bytes()
    resp = _infer(client, audio, {"task": "translate", "beam_size": 1, "vad": True})
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["language"] == "en"  # translate output is always English
    assert result["text"]  # English source -> non-empty English text
    assert all(set(s) == {"start_s", "end_s", "text"} for s in result["segments"])


def test_garbage_bytes_deterministic_422(warm_client):
    client, _ = warm_client
    resp = _infer(client, b"this is definitely not an audio container" * 8, {})
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["transient"] is False  # deterministic: every replica fails it


def test_unknown_param_deterministic_422(warm_client):
    client, _ = warm_client
    audio = (FIXTURES / "speech_two_speakers.webm").read_bytes()
    resp = _infer(client, audio, {"task": "transcribe", "beam": 5})  # typo'd param
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["transient"] is False
    assert "beam" in body["error"]
