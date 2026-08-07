"""servers/ast tests — in-process via fastapi TestClient (no ports bound).

Run: cd servers/ast && CUDA_VISIBLE_DEVICES=6 ./.venv/bin/python -m pytest tests/ -q

The golden smoke compares against tests/fixtures/golden_tags.json under the tolerance
policy recorded in tests/fixtures/PROVENANCE.md (measured across fresh processes on
GPUs 6 and 7).
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dp_servers_common.app import build_app
from server import AstBackend

SERVER_DIR = Path(__file__).resolve().parents[1]
FIXTURES = SERVER_DIR / "tests" / "fixtures"
MANIFEST_PATH = SERVER_DIR.parent / "manifest.json"

INPUT_FIXTURE = FIXTURES / "speech_two_speakers.webm"
INPUT_SHA256 = "a2e29465ffc9c4df72a7571e7270fb4d304910637ef56e1770c01ba95ea0756d"
GOLDEN_PATH = FIXTURES / "golden_tags.json"
GOLDEN_PARAMS = {"top_k": 20}

# Tolerance policy (measured; see PROVENANCE.md): 4 fresh-process runs (3x GPU 6,
# 1x GPU 7) produced BYTE-IDENTICAL canonical JSON — no score jitter, no tie flips —
# so the golden compare is exact. If a future environment change introduces jitter,
# re-measure and derive a per-label score atol from the observed deltas (compare as
# a label->score mapping if near-tie order flips); do not loosen this blindly.

WARM_TIMEOUT_S = 300  # matches manifest startup_timeout_s


@pytest.fixture(scope="session")
def client():
    app = build_app(AstBackend())
    with TestClient(app) as c:
        deadline = time.monotonic() + WARM_TIMEOUT_S
        while True:
            r = c.get("/health")
            if r.status_code == 200:
                break
            body = r.json()
            assert body.get("status") != "load_failed", f"model load failed: {body}"
            assert time.monotonic() < deadline, f"warmup timed out: {body}"
            time.sleep(0.5)
        yield c


def _assert_subset(expected: dict, actual: dict, path: str = "identity") -> None:
    for key, want in expected.items():
        assert key in actual, f"{path}.{key} missing"
        if isinstance(want, dict):
            assert isinstance(actual[key], dict), f"{path}.{key} is not a dict"
            _assert_subset(want, actual[key], f"{path}.{key}")
        else:
            assert actual[key] == want, f"{path}.{key}: {actual[key]!r} != {want!r}"


def test_health_identity_matches_manifest(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "ready"
    assert body["server"] == "ast"

    identity = body["identity"]
    manifest = json.loads(MANIFEST_PATH.read_text())
    _assert_subset(manifest["servers"]["ast"]["expected_identity"], identity)

    # Shape: the full L4 identity contract.
    assert identity["device"] == "cuda"
    frameworks = identity["frameworks"]
    for key in ("transformers", "torch", "ffmpeg"):
        assert isinstance(frameworks.get(key), str) and frameworks[key], key


def test_golden_smoke_speech_clip(client: TestClient) -> None:
    audio = INPUT_FIXTURE.read_bytes()
    assert hashlib.sha256(audio).hexdigest() == INPUT_SHA256, "input fixture drifted"

    r = client.post("/infer", json={
        "input_b64": base64.b64encode(audio).decode("ascii"),
        "codec": "audio/webm",
        "params": GOLDEN_PARAMS,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    tags = body["result"]["tags"]

    assert len(tags) == GOLDEN_PARAMS["top_k"]
    scores = [t["score"] for t in tags]
    assert scores == sorted(scores, reverse=True), "tags not in descending score order"

    # It is a speech clip — pinned as MEASURED: the top-4 labels are exactly the
    # Speech family ("Speech synthesizer" is correct: the fixture is piper-TTS
    # synthesized speech), with "Speech" first at ~0.663.
    assert tags[0]["label"] == "Speech"
    assert tags[0]["score"] > 0.5
    assert {t["label"] for t in tags[:4]} == {
        "Speech",
        "Speech synthesizer",
        "Narration, monologue",
        "Male speech, man speaking",
    }

    # Exact golden compare (byte-identical across 4 fresh processes on GPUs 6 and 7).
    golden = json.loads(GOLDEN_PATH.read_text())["tags"]
    assert tags == golden


def test_golden_smoke_real_dialog(client: TestClient) -> None:
    """Second golden (cleanup round 2026-08-06): real multi-speaker speech
    (LibriVox dramatic reading, see INPUT_PROVENANCE.md). Bit-stable across 4
    fresh-process runs on GPUs 6 and 7 — exact compare, zero tolerance."""
    audio = (FIXTURES / "speech_real_dialog.webm").read_bytes()
    r = client.post("/infer", json={
        "input_b64": base64.b64encode(audio).decode("ascii"),
        "codec": "audio/webm",
        "params": GOLDEN_PARAMS,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    golden = json.loads((FIXTURES / "golden_tags_real.json").read_text())["tags"]
    assert body["result"]["tags"] == golden


def test_garbage_bytes_deterministic_422(client: TestClient) -> None:
    garbage = base64.b64encode(b"\x00\x01\x02 definitely not an audio container " * 64)
    r = client.post("/infer", json={
        "input_b64": garbage.decode("ascii"),
        "codec": "audio/webm",
        "params": {},
    })
    assert r.status_code == 422
    body = r.json()
    assert body["ok"] is False
    assert body["transient"] is False


def test_invalid_base64_deterministic_422(client: TestClient) -> None:
    r = client.post("/infer", json={"input_b64": "not!!base64@@", "params": {}})
    assert r.status_code == 422
    assert r.json()["transient"] is False


def test_unknown_param_deterministic_422(client: TestClient) -> None:
    audio = INPUT_FIXTURE.read_bytes()
    r = client.post("/infer", json={
        "input_b64": base64.b64encode(audio).decode("ascii"),
        "params": {"top_k": 20, "threshold": 0.5},
    })
    assert r.status_code == 422
    body = r.json()
    assert body["ok"] is False
    assert body["transient"] is False
    assert "threshold" in body["error"]
