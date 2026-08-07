"""servers/ocr contract tests -- in-process (fastapi TestClient), no ports.

Run: cd servers/ocr && CUDA_VISIBLE_DEVICES=./.venv/bin/python -m pytest tests/ -q

Covers:
 * /health identity: subset-match against servers/manifest.json expected_identity
 plus shape (det/rec sha256 present, 64 hex chars; pinned engine settings).
 * golden smoke: fixtures/screen_planning_notes.jpg vs fixtures/golden_regions.json
 (exact compare -- CPU determinism verified across fresh processes, see
 fixtures/PROVENANCE.md) + a majority sanity check on the known rendered lines.
 * bad input: invalid base64 / garbage bytes / stray params / wrong codec ->
 deterministic 422; a blank white JPEG -> 200 with regions: [] (ran-and-empty
 honesty, distinct from failure).
"""
from __future__ import annotations

import base64
import io
import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dp_servers_common.app import build_app
from server import OcrBackend

SERVER_DIR = Path(__file__).resolve.parents[1] # servers/ocr
FIXTURES = SERVER_DIR / "tests" / "fixtures"
MANIFEST = SERVER_DIR.parent / "manifest.json" # read-only

# The text lines rendered into the fixture (tests/fixtures/INPUT_PROVENANCE.md).
# The golden pins what the engine MEASURABLY returns, verbatim; this list is a
# looser sanity net: a majority must appear as substrings of the recognized text.
# Measured caveat: PP-OCRv4 rec drops many inter-word spaces (e.g. the title
# comes back "QuarterlyPlanningNotes"), so the sanity match strips whitespace
# from both sides -- characters stay verbatim, spacing does not gate.
KNOWN_LINES = (
 "Quarterly Planning Notes",
 "Action items for the data pipeline",
 "Review the ingest backlog before Friday",
 "Ship the model server framework",
 "$ python -m pytest tests/",
 "42 passed in 3.14s",
 "Saved 2 minutes ago",
)


@pytest.fixture(scope="session")
def client:
 c = TestClient(build_app(OcrBackend))
 c.__enter__ # lifespan -> warmup thread -> backend.load
 deadline = time.monotonic + 120
 while time.monotonic < deadline:
 if c.get("/health").status_code in (200, 500):
 break
 time.sleep(0.1)
 health = c.get("/health")
 assert health.status_code == 200, f"model never went warm: {health.json}"
 yield c
 c.__exit__(None, None, None)


def _infer(client: TestClient, **body):
 return client.post("/infer", json=body)


# ---- /health identity -------------------------------------------------------

def test_health_identity_matches_manifest_and_shape(client):
 body = client.get("/health").json
 assert body["ok"] is True
 assert body["status"] == "ready"
 assert body["server"] == "ocr"
 identity = body["identity"]

 # Subset-match: every key the supervisor manifest pins must hold verbatim.
 expected = json.loads(MANIFEST.read_text)["servers"]["ocr"]["expected_identity"]
 for key, want in expected.items:
 assert identity[key] == want, f"identity[{key!r}] != manifest: {identity[key]!r}"

 # Shape: real weight hashes -- 64 lowercase hex chars each.
 weights = identity["weights"]
 for field in ("det_sha256", "rec_sha256"):
 assert re.fullmatch(r"[0-9a-f]{64}", weights[field]), (field, weights.get(field))
 assert weights["det_sha256"] != weights["rec_sha256"]

 # Pinned engine settings surface truthfully (L4 hook).
 assert identity["frameworks"]["rapidocr_onnxruntime"] == "1.4.4"
 assert identity["frameworks"]["onnxruntime"] == "1.27.0"
 assert identity["device"] == "cpu"
 assert identity["ep"] == "CPUExecutionProvider"
 assert identity["threads"] == 4


# ---- golden smoke -----------------------------------------------------------

def test_golden_smoke_screen_planning_notes(client):
 jpeg = (FIXTURES / "screen_planning_notes.jpg").read_bytes
 resp = _infer(
 client,
 input_b64=base64.b64encode(jpeg).decode("ascii"),
 codec="image/jpeg",
 )
 assert resp.status_code == 200, resp.json
 body = resp.json
 assert body["ok"] is True
 result = body["result"]

 # Exact compare: ONNX CPU with pinned threads measured bit-stable across
 # fresh processes (fixtures/PROVENANCE.md). If this ever drifts, that is a
 # real stack change -- investigate, don't loosen.
 golden = json.loads((FIXTURES / "golden_regions.json").read_text)
 assert result == golden

 # Sanity net independent of the golden: a majority of the rendered lines
 # must be recognized (whitespace-insensitive; see KNOWN_LINES note).
 squash = lambda s: re.sub(r"\s+", "", s) # noqa: E731
 joined = squash("\n".join(r["text"] for r in result["regions"]))
 hits = [line for line in KNOWN_LINES if squash(line) in joined]
 assert len(hits) > len(KNOWN_LINES) / 2, f"only recognized {hits} in {joined!r}"

 # Shape: pixel bboxes inside the 1280x800 fixture, origin top-left.
 for r in result["regions"]:
 x0, y0, x1, y1 = r["bbox"]
 assert 0 <= x0 < x1 <= 1280 and 0 <= y0 < y1 <= 800, r
 assert 0.0 <= r["conf"] <= 1.0


# ---- bad input: deterministic 422 ------------------------------------------

def _assert_deterministic_422(resp):
 assert resp.status_code == 422, resp.json
 body = resp.json
 assert body["ok"] is False
 assert body["transient"] is False


def test_invalid_base64_is_422(client):
 _assert_deterministic_422(_infer(client, input_b64="!!!not-base64!!!"))


def test_garbage_bytes_are_422(client):
 not_a_jpeg = base64.b64encode(b"\x00\x01\x02 definitely not an image").decode
 _assert_deterministic_422(_infer(client, input_b64=not_a_jpeg))


def test_missing_input_is_422(client):
 _assert_deterministic_422(_infer(client))


def test_params_are_rejected_422(client):
 jpeg = (FIXTURES / "screen_planning_notes.jpg").read_bytes
 resp = _infer(
 client,
 input_b64=base64.b64encode(jpeg).decode("ascii"),
 params={"conf_threshold": 0.5},
 )
 _assert_deterministic_422(resp)
 assert "params" in resp.json["error"]


def test_wrong_codec_is_422(client):
 jpeg = (FIXTURES / "screen_planning_notes.jpg").read_bytes
 resp = _infer(
 client,
 input_b64=base64.b64encode(jpeg).decode("ascii"),
 codec="image/png",
 )
 _assert_deterministic_422(resp)


# ---- ran-and-empty honesty --------------------------------------------------

def test_blank_white_jpeg_is_200_with_empty_regions(client):
 from PIL import Image

 buf = io.BytesIO
 Image.new("RGB", (320, 200), (255, 255, 255)).save(buf, format="JPEG", quality=90)
 resp = _infer(
 client,
 input_b64=base64.b64encode(buf.getvalue).decode("ascii"),
 codec="image/jpeg",
 )
 assert resp.status_code == 200, resp.json
 body = resp.json
 assert body["ok"] is True
 assert body["result"] == {"regions": []}
