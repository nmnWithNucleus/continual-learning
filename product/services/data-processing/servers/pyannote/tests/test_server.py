"""In-process tests for the pyannote diarization server (no ports bound).

Run:  cd servers/pyannote && CUDA_VISIBLE_DEVICES=2 ./.venv/bin/python -m pytest tests/ -q

The session fixture builds the app around a real PyannoteBackend and waits for
warm — the first-ever run also downloads the wespeaker embedding model into the
HF cache, so the deadline is generous. All /infer calls ride the same warmed
client. The golden comparison policy is recorded in tests/fixtures/PROVENANCE.md
(byte-identical across 4 fresh processes on GPUs 2 and 3 -> exact compare).
"""
from __future__ import annotations

import base64
import json
import re
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

import server  # noqa: E402
from dp_servers_common.app import build_app  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MANIFEST_PATH = SERVER_DIR.parent / "manifest.json"  # read-only reference
WARM_DEADLINE_S = 1800.0  # cold first load downloads the wespeaker embedding model

GOLDEN_PARAMS = {"span_seconds": 6.496}
_HEX40 = re.compile(r"^[0-9a-f]{40}$")


@pytest.fixture(scope="session")
def client():
    app = build_app(server.PyannoteBackend())
    with TestClient(app) as c:
        deadline = time.monotonic() + WARM_DEADLINE_S
        while True:
            health = c.get("/health")
            if health.status_code == 200:
                break
            assert health.status_code != 500, f"model load failed: {health.text}"
            assert time.monotonic() < deadline, "timed out waiting for the model to warm"
            time.sleep(2.0)
        yield c


def _assert_subset(expected, actual, path="identity"):
    """Every key in `expected` must exist in `actual` with an equal value
    (dicts recurse) — the supervisor's manifest check, in miniature."""
    assert isinstance(expected, dict) and isinstance(actual, dict), path
    for key, want in expected.items():
        assert key in actual, f"{path}.{key} missing"
        if isinstance(want, dict):
            _assert_subset(want, actual[key], f"{path}.{key}")
        else:
            assert actual[key] == want, f"{path}.{key}: {actual[key]!r} != {want!r}"


def test_health_identity_matches_manifest(client):
    health = client.get("/health")
    assert health.status_code == 200
    body = health.json()
    assert body["ok"] is True
    assert body["status"] == "ready"
    assert body["server"] == "pyannote"

    identity = body["identity"]

    # Subset-match against the supervisor manifest's expected_identity (read-only).
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    expected = manifest["servers"]["pyannote"]["expected_identity"]
    _assert_subset(expected, identity)

    # Full identity shape: everything output-affecting, pinned or resolved.
    assert set(identity) == {"model_name", "weights", "frameworks", "device"}
    assert identity["model_name"] == server.MODEL_ID
    assert identity["device"] == "cuda"

    weights = identity["weights"]
    assert set(weights) == {"pipeline_revision", "segmentation_revision", "embedding_revision"}
    assert weights["pipeline_revision"] == server.REVISION
    for key, value in weights.items():
        assert _HEX40.fullmatch(value), f"weights.{key} is not a 40-hex commit: {value!r}"

    frameworks = identity["frameworks"]
    assert set(frameworks) == {"pyannote.audio", "torch", "torchaudio", "ffmpeg"}
    assert frameworks["pyannote.audio"] == "3.3.2"
    assert frameworks["torch"].startswith("2.8.0")
    assert frameworks["torchaudio"].startswith("2.8.0")
    assert frameworks["ffmpeg"]  # version token, e.g. "7.1"


def test_golden_smoke_two_speakers(client):
    audio = (FIXTURES / "speech_two_speakers.webm").read_bytes()
    resp = client.post("/infer", json={
        "input_b64": base64.b64encode(audio).decode("ascii"),
        "codec": "audio/webm",
        "params": dict(GOLDEN_PARAMS),
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    result = body["result"]

    golden = json.loads((FIXTURES / "golden_diarize.json").read_text(encoding="utf-8"))
    # Determinism study (PROVENANCE.md): byte-identical across 4 fresh processes
    # on GPU 2 and GPU 3 -> exact compare, no tolerance.
    assert result == golden


def test_bad_input_is_deterministic_422(client):
    garbage = bytes(range(256)) * 4  # not any audio container; ffmpeg decode fails
    resp = client.post("/infer", json={
        "input_b64": base64.b64encode(garbage).decode("ascii"),
        "codec": "audio/webm",
        "params": dict(GOLDEN_PARAMS),
    })
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["transient"] is False
    assert "ffmpeg" in body["error"]


def test_missing_span_seconds_is_422(client):
    audio = (FIXTURES / "speech_two_speakers.webm").read_bytes()
    resp = client.post("/infer", json={
        "input_b64": base64.b64encode(audio).decode("ascii"),
        "codec": "audio/webm",
        "params": {},
    })
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["transient"] is False
    assert "span_seconds" in body["error"]


def test_unknown_param_is_422(client):
    audio = (FIXTURES / "speech_two_speakers.webm").read_bytes()
    resp = client.post("/infer", json={
        "input_b64": base64.b64encode(audio).decode("ascii"),
        "codec": "audio/webm",
        "params": {"span_seconds": 6.496, "num_speakers": 2},
    })
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["transient"] is False
    assert "unknown params" in body["error"]
