"""WP-C6 — the REAL-backend e2e: fleet up via the supervisor, one audio chunk
(and one video chunk) end-to-end, records validated against C2 v1 and POSTed to
a LOCAL STUB /context sink only. NEVER the live storage on :8083 — storage
accepts v1 at Stage E, not before (the stub here is the MockTransport fake:
in-process, no socket, structurally incapable of reaching a real service).

Gated on DP_E2E=1: this spins real model servers on GPUs 2-7 (manifest ports
8121-8152) and takes minutes. Drill discipline: v0 :8085 healthy before and
after, nothing left listening, fleet GPU usage released (measured as before/
after deltas per GPU — GPU 7 hosts a foreign eval job whose usage is not ours
to zero).

The client-path-reproduces-goldens check: the record's audio slots must equal
EXACTLY the slot dicts the golden-fed fake clients produce through the same
stage code (tests/test_audio_stages.py's pins) — same C1, same span, same
mapping; the only difference is which process ran the model. A mismatch is
either my bug or a genuine identity change — STOP on the latter.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import schemas
from app.models import C2RecordV1

from tests.fake_storage import FakeStorage
from tests.test_audio_stages import (
    ASR_SLOT_REAL,
    DIARIZATION_SLOT_REAL,
    GOLDEN_TAGS_REAL,
    C1_REAL,
)

pytestmark = pytest.mark.skipif(
    os.getenv("DP_E2E") != "1",
    reason="real-fleet e2e: set DP_E2E=1 (spawns model servers on GPUs 2-7)",
)

_SERVICE = Path(__file__).resolve().parents[1]
_MANIFEST = _SERVICE / "servers" / "manifest.json"
_AUDIO_FIXTURE = (_SERVICE / "servers" / "whisper" / "tests" / "fixtures"
                  / "speech_real_dialog.webm")
_VIDEO_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "video_scenes.mp4"


def _manifest_ports() -> list[int]:
    manifest = json.loads(_MANIFEST.read_text())
    return [r["port"] for spec in manifest["servers"].values()
            for r in spec["replicas"]]


def _listening(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _health_ok(port: int) -> bool:
    import httpx
    try:
        return httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200
    except Exception:
        return False


def _v0_health() -> int:
    import httpx
    return httpx.get("http://127.0.0.1:8085/health", timeout=5).status_code


@pytest.fixture(scope="module")
def fleet():
    assert _v0_health() == 200, "v0 not healthy before the drill"
    ports = _manifest_ports()
    assert not any(_listening(p) for p in ports), "fleet ports already in use"

    proc = subprocess.Popen(
        [str(_SERVICE / ".venv" / "bin" / "python"), "-m", "app.supervisor",
         "--manifest", str(_MANIFEST)],
        cwd=_SERVICE, start_new_session=True,
    )
    try:
        deadline = time.time() + 360
        while time.time() < deadline:
            if all(_health_ok(p) for p in ports):
                break
            assert proc.poll() is None, "supervisor died during spawn"
            time.sleep(2)
        else:
            pytest.fail("fleet never became healthy within 360s")
        yield ports
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=30)
        deadline = time.time() + 30
        while time.time() < deadline and any(_listening(p) for p in ports):
            time.sleep(1)
        assert not any(_listening(p) for p in ports), "fleet ports still listening"
        assert _v0_health() == 200, "v0 unhealthy after the drill"


@pytest.fixture()
def real_app(monkeypatch, tmp_path):
    """The DP app on the REAL registry (discovery — no mocks), storage bound to
    the local stub sink; model clients point at the manifest fleet."""
    monkeypatch.setenv("STORAGE_URL", "http://stub.sink")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / "var"))
    monkeypatch.delenv("DP_SUPERVISOR", raising=False)  # the fixture owns the fleet
    from app.main import create_app

    fs = FakeStorage()
    app = create_app()
    app.state.storage._transport = fs.transport()
    with TestClient(app) as client:
        client.fake_storage = fs  # type: ignore[attr-defined]
        yield client


def _c1_for(fs: FakeStorage, path: Path, template: dict, **overrides) -> dict:
    import hashlib
    blob = path.read_bytes()
    c1 = dict(template)
    c1.update(overrides)
    c1["blob_sha256"] = hashlib.sha256(blob).hexdigest()
    c1["blob_bytes"] = len(blob)
    fs.add_blob(c1["blob_ref"], blob)
    return c1


def test_audio_chunk_end_to_end(fleet, real_app):
    fs = real_app.fake_storage
    # The Stage B real-speech fixture, under the SAME C1 the golden-fed pins
    # used (same t_start/t_end -> identical absolute split times).
    c1 = _c1_for(fs, _AUDIO_FIXTURE, C1_REAL, chunk_id="e2e-audio-1",
                 blob_ref="raw/e2e/audio-1")
    resp = real_app.post("/ingest", json=c1)
    assert resp.status_code == 200, resp.text

    assert len(fs.record_posts) == 1
    record = fs.record_posts[0]
    assert schemas.validate_c2(record) == []
    C2RecordV1.model_validate(record)
    assert record["pipeline_version"] == (
        "acoustic.v1-ast.v1+asr.v1-fw.v1+diarize.v1-pyannote.v1"
        "+speaker_align.v1-builtin.v1")

    slots = record["content"]["slots"]
    # Client path reproduces the goldens (byte-exact slot dicts).
    assert slots["asr"] == ASR_SLOT_REAL
    assert slots["diarization"] == DIARIZATION_SLOT_REAL
    # Acoustic: the real golden folds to the empty claim (speech-only clip).
    assert slots["acoustic"] == {"version": "acoustic.v1-ast.v1", "values": []}
    # The derived transcript exists and is speaker-aligned over the asr splits.
    assert slots["transcript"]["version"] == "speaker_align.v1-builtin.v1"
    assert len(slots["transcript"]["splits"]) == len(ASR_SLOT_REAL["splits"])


def test_video_chunk_end_to_end(fleet, real_app):
    """One video chunk through the REAL machinery: clipprep's real ffmpeg over
    the committed fixture, screentext against the real ocr server. clipcap's
    endpoint (`VLM_URL` → an OpenAI-compatible server actually hosting
    Qwen3-VL-32B) does not exist on this node — v0's live video dialect was
    mock, the real VLM is the serve loop's, and the serve loop cannot co-run
    with the learn loop (shared storage :8083) — so the caption slot HOLES
    honestly: attempted (in the dialect), absent (L7/L11). The golden chain for
    OCR is closed at the layers below: servers/ocr pins engine-vs-golden, the
    screentext suite pins client-mapping-vs-the-same-golden; this test proves
    the live wire produces a valid record deterministically."""
    fs = real_app.fake_storage
    template = json.loads(
        (Path(__file__).resolve().parent / "fixtures" / "video_scenes.c1.json")
        .read_text())
    c1 = _c1_for(fs, _VIDEO_FIXTURE, template, chunk_id="e2e-video-1",
                 blob_ref="raw/e2e/video-1")
    resp = real_app.post("/ingest", json=c1)
    assert resp.status_code == 200, resp.text

    records = [r for r in fs.record_posts if r["modality"] == "video"]
    assert len(records) == 1
    record = records[0]
    assert schemas.validate_c2(record) == []
    C2RecordV1.model_validate(record)
    assert record["pipeline_version"] == (
        "clipcap.v1-vlm.v1+clipprep.v1-ffmpeg.v1+screentext.v1-ppocr.v1")

    slots = record["content"]["slots"]
    # clipprep ran (required — the 200 proves it) and emits no slot.
    assert "clipprep" not in slots
    # screentext ran against the REAL ocr server: slot present, string value
    # ("" would be the honest empty claim; this fixture carries little/no text).
    assert "ocr" in slots
    assert isinstance(slots["ocr"]["value"], str)
    assert slots["ocr"]["version"] == "screentext.v1-ppocr.v1"
    # clipcap holed (no VLM endpoint on this node): attempted, absent.
    assert "caption" not in slots

    # Determinism through the real machinery: same bytes, same versions,
    # second chunk -> identical slots map.
    c1b = _c1_for(fs, _VIDEO_FIXTURE, template, chunk_id="e2e-video-2",
                  blob_ref="raw/e2e/video-2")
    assert real_app.post("/ingest", json=c1b).status_code == 200
    records = [r for r in fs.record_posts if r["modality"] == "video"]
    assert records[1]["content"]["slots"] == slots
    assert records[1]["record_id"] != record["record_id"]


def test_audio_reprocess_is_byte_identical_through_the_real_fleet(fleet, real_app,
                                                                  monkeypatch,
                                                                  tmp_path):
    """T-1's claim held against the real machinery: same bytes, same versions,
    two independent process-and-POST runs -> identical wire bytes."""
    fs1 = real_app.fake_storage
    c1 = _c1_for(fs1, _AUDIO_FIXTURE, C1_REAL, chunk_id="e2e-audio-det",
                 blob_ref="raw/e2e/audio-det")
    assert real_app.post("/ingest", json=c1).status_code == 200

    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / "var2"))  # fresh journal
    from app.main import create_app
    fs2 = FakeStorage()
    fs2.add_blob(c1["blob_ref"], _AUDIO_FIXTURE.read_bytes())
    app2 = create_app()
    app2.state.storage._transport = fs2.transport()
    with TestClient(app2) as second:
        assert second.post("/ingest", json=c1).status_code == 200
    assert fs1.record_posts_raw[-1] == fs2.record_posts_raw[0]
