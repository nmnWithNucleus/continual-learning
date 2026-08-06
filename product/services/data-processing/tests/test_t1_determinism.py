"""T-1 — the determinism matrix (L4): fixed bytes + fixed versions ⇒ a
BYTE-IDENTICAL record across the whole env-var matrix. Any knob that moves
bytes = red.

The matrix deliberately includes the KILLED knobs (ASR_*, INGEST_ISOLATION,
DP_DIALECT_FREEZE, VIDEO_*, ACOUSTIC_*…): setting them must change nothing —
that is what "the knob is dead" means, executably. It also includes the
surviving operational knobs (VERIFY_BLOB_SHA256, METRICS_ENABLED, INGEST_ASYNC,
worker counts): operational-only means output-inert.

Runs on MOCK dialects (client-level fakes named in the version string), fixtures
distinct from the real backends' goldens. WP-C6 extends the same matrix over the
real stage classes with fake clients injected.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from tests.conftest import install_mock_audio_registry, make_c1
from tests.fake_storage import FakeStorage

# Each entry is one matrix cell: env overrides that must all be output-inert.
ENV_MATRIX: list[dict[str, str]] = [
    {},                                              # baseline
    # --- killed knobs: must be COMPLETELY inert (L4 teeth) -------------------
    {"ASR_BACKEND": "faster_whisper"},
    {"ASR_MODEL": "tiny", "ASR_DEVICE": "cuda", "ASR_COMPUTE_TYPE": "float16"},
    {"ASR_BEAM_SIZE": "5", "ASR_LANGUAGE": "fr", "ASR_VAD": "0"},
    {"DP_DIALECT_FREEZE": "1"},
    {"INGEST_ISOLATION": "subprocess", "INGEST_SUBPROC_START": "fork"},
    {"VIDEO_BACKEND": "vlm", "VIDEO_OCR_BACKEND": "ppocr"},
    {"VIDEO_SCENARIO": "screen-win", "VIDEO_VLM_MODEL": "some/other-model"},
    {"ACOUSTIC_TOP_K": "3", "ACOUSTIC_THRESHOLD": "0.9"},
    {"TRANSLATE_BACKEND": "whisper", "TRANSLATE_TARGET": "fr"},
    {"DIARIZE_BACKEND": "off", "INJECT_CAPTION_BACKEND": "index"},
    # --- surviving operational knobs: must be output-inert -------------------
    {"VERIFY_BLOB_SHA256": "0"},
    {"METRICS_ENABLED": "0"},
    {"INGEST_ASYNC": "1", "INGEST_WORKERS": "2", "INGEST_RETRY_BACKOFF": "0"},
    {"DP_HTTP_TIMEOUT": "5", "INGEST_MODALITY_LIMITS": "audio=1"},
]


def _wire_bytes_for(monkeypatch, tmp_path, env: dict[str, str], run_tag: str) -> bytes:
    """Boot a fresh app under `env`, ingest the fixed chunk, return the exact
    bytes POSTed to /context."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / f"var-{run_tag}"))
    install_mock_audio_registry(monkeypatch)
    from app.main import create_app

    fs = FakeStorage()
    app = create_app()
    app.state.storage._transport = fs.transport()
    with TestClient(app) as client:
        c1 = make_c1(fs, chunk_id="t1-fixed-chunk")
        resp = client.post("/ingest", json=c1)
        assert resp.status_code in (200, 202), resp.text
        if resp.status_code == 202:  # async cell: wait for the worker
            deadline = time.time() + 10
            while time.time() < deadline and not fs.record_posts_raw:
                time.sleep(0.01)
    assert len(fs.record_posts_raw) == 1, f"expected one POST under {env!r}"
    return fs.record_posts_raw[0]


@pytest.fixture(scope="module")
def baseline_bytes(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    try:
        yield_dir = tmp_path_factory.mktemp("t1-baseline")
        yield _wire_bytes_for(mp, yield_dir, {}, "baseline")
    finally:
        mp.undo()


@pytest.mark.parametrize("env", ENV_MATRIX,
                         ids=lambda e: "+".join(sorted(e)) or "baseline")
def test_record_bytes_identical_across_env_matrix(monkeypatch, tmp_path,
                                                  baseline_bytes, env):
    got = _wire_bytes_for(monkeypatch, tmp_path, env, "cell")
    assert got == baseline_bytes, (
        f"env {env!r} moved record bytes — an output-affecting knob exists (L4 red)"
    )


def test_reprocess_is_byte_identical(monkeypatch, tmp_path):
    """§4's crash-table claim: a full reprocess under fixed versions is
    byte-identical (no wall-clock field exists inside the record)."""
    first = _wire_bytes_for(monkeypatch, tmp_path, {}, "a")
    time.sleep(0.05)  # any wall-clock leak would move the second run's bytes
    second = _wire_bytes_for(monkeypatch, tmp_path, {}, "b")
    assert first == second


# ---------------------------------------------------------------------------
# The REAL stage classes under the same matrix (golden-fed fake clients).
# The stage code is the code that could cheat with os.getenv — so the matrix
# must run over it, not only over mocks.
# ---------------------------------------------------------------------------

def _real_audio_wire_bytes_for(monkeypatch, tmp_path, env: dict[str, str],
                               run_tag: str) -> tuple[bytes, dict]:
    from app.stagegraph import stage as stage_mod

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / f"rvar-{run_tag}"))
    # Patch a CLEAN registry BEFORE importing the stage modules: their
    # @register_stage decorators fire at first import into whatever registry is
    # active (a leftover mock registry would collide). Re-registering the same
    # class after a cached import is a permitted no-op.
    monkeypatch.setattr(stage_mod, "_REGISTRY", {})
    monkeypatch.setattr(stage_mod, "_discovered", True)
    from app.stages.audio.asr import AsrStage
    from app.stages.audio.diarize import DiarizeStage
    from app.stages.audio.acoustic import AcousticStage
    from app.stages.audio.speaker_align import SpeakerAlignStage
    from tests.test_audio_stages import (
        FakeModelClient, GOLDEN_TRANSCRIBE_REAL, GOLDEN_DIARIZE_REAL,
        GOLDEN_TAGS_REAL, C1_REAL,
    )
    for cls in (AsrStage, DiarizeStage, AcousticStage, SpeakerAlignStage):
        stage_mod.register_stage(cls)

    from app.main import create_app

    fs = FakeStorage()
    app = create_app()
    app.state.storage._transport = fs.transport()
    # Client-level fakes replaying the REAL server goldens.
    app.state.model_clients = {
        "whisper": FakeModelClient(GOLDEN_TRANSCRIBE_REAL),
        "pyannote": FakeModelClient(GOLDEN_DIARIZE_REAL),
        "ast": FakeModelClient(GOLDEN_TAGS_REAL),
    }
    clients = app.state.model_clients
    with TestClient(app) as client:
        c1 = dict(C1_REAL)
        c1["chunk_id"] = "t1-real-chunk"
        fs.add_blob(c1["blob_ref"],
                    b"\x1a\x45\xdf\xa3 fixed bytes; fakes never decode")
        import hashlib
        c1["blob_sha256"] = hashlib.sha256(
            b"\x1a\x45\xdf\xa3 fixed bytes; fakes never decode").hexdigest()
        c1["blob_bytes"] = 44
        resp = client.post("/ingest", json=c1)
        assert resp.status_code in (200, 202), resp.text
        if resp.status_code == 202:
            deadline = time.time() + 10
            while time.time() < deadline and not fs.record_posts_raw:
                time.sleep(0.01)
    assert len(fs.record_posts_raw) == 1
    # The OUTBOUND half of determinism (cleanup round): what the stages SENT
    # to the model servers must also be env-inert, or a knob could steer the
    # models while the fakes mask it.
    payloads = {server: fake.calls for server, fake in clients.items()}
    return fs.record_posts_raw[0], payloads


@pytest.fixture(scope="module")
def real_audio_baseline(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    try:
        yield _real_audio_wire_bytes_for(
            mp, tmp_path_factory.mktemp("t1-real-baseline"), {}, "baseline")
    finally:
        mp.undo()


@pytest.mark.parametrize("env", ENV_MATRIX,
                         ids=lambda e: "+".join(sorted(e)) or "baseline")
def test_real_audio_stages_are_env_inert(monkeypatch, tmp_path,
                                         real_audio_baseline, env):
    got_bytes, got_payloads = _real_audio_wire_bytes_for(
        monkeypatch, tmp_path, env, "cell")
    base_bytes, base_payloads = real_audio_baseline
    assert got_bytes == base_bytes, (
        f"env {env!r} moved bytes through the REAL audio stage code (L4 red)"
    )
    assert got_payloads == base_payloads, (
        f"env {env!r} moved the OUTBOUND /infer payloads (L4 red)"
    )


# ---------------------------------------------------------------------------
# The REAL video stage classes under the same matrix: real ffmpeg clipprep over
# the committed fixture, fake OCR client (golden replay), MockTransport VLM.
# ---------------------------------------------------------------------------

def _real_video_wire_bytes_for(monkeypatch, tmp_path, env: dict[str, str],
                               run_tag: str) -> tuple[bytes, dict]:
    from pathlib import Path

    from app.stagegraph import stage as stage_mod
    import httpx as _httpx
    import json as _json

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / f"vvar-{run_tag}"))
    # Clean registry BEFORE the stage imports (see the audio helper's note).
    monkeypatch.setattr(stage_mod, "_REGISTRY", {})
    monkeypatch.setattr(stage_mod, "_discovered", True)
    from app.stages.video.clipprep import ClipPrepStage
    from app.stages.video.screentext import ScreentextStage
    from app.stages.video.clipcap import ClipcapStage
    from app.vision.clipcap import vlm
    from tests.test_screentext import FakeOcrClient
    for cls in (ClipPrepStage, ScreentextStage, ClipcapStage):
        stage_mod.register_stage(cls)

    vlm_requests: list[bytes] = []

    def handler(request: _httpx.Request) -> _httpx.Response:
        vlm_requests.append(bytes(request.content))
        return _httpx.Response(200, json={"choices": [{
            "message": {"content": _json.dumps({
                "caption": "A fixed screen with static panels.",
                "topics": [], "app": "editor",
            })},
            "finish_reason": "stop",
        }]})

    monkeypatch.setattr(
        vlm, "make_async_client",
        lambda timeout_s: _httpx.AsyncClient(
            transport=_httpx.MockTransport(handler)))

    from app.main import create_app

    fs = FakeStorage()
    app = create_app()
    app.state.storage._transport = fs.transport()
    ocr_fake = FakeOcrClient()
    app.state.model_clients = {"ocr": ocr_fake}
    with TestClient(app) as client:
        blob = (Path(__file__).resolve().parent / "fixtures"
                / "video_scenes.mp4").read_bytes()
        import hashlib
        c1 = _json.loads((Path(__file__).resolve().parent / "fixtures"
                          / "video_scenes.c1.json").read_text())
        c1["chunk_id"] = "t1-video-chunk"
        fs.add_blob(c1["blob_ref"], blob)
        c1["blob_sha256"] = hashlib.sha256(blob).hexdigest()
        c1["blob_bytes"] = len(blob)
        resp = client.post("/ingest", json=c1)
        assert resp.status_code in (200, 202), resp.text
        if resp.status_code == 202:  # async cell: wait for the worker
            deadline = time.time() + 30
            while time.time() < deadline and not fs.record_posts_raw:
                time.sleep(0.01)
    assert len(fs.record_posts_raw) == 1
    return fs.record_posts_raw[0], {"ocr": ocr_fake.calls, "vlm": vlm_requests}


@pytest.fixture(scope="module")
def real_video_baseline(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    try:
        yield _real_video_wire_bytes_for(
            mp, tmp_path_factory.mktemp("t1-video-baseline"), {}, "baseline")
    finally:
        mp.undo()


@pytest.mark.parametrize("env", ENV_MATRIX,
                         ids=lambda e: "+".join(sorted(e)) or "baseline")
def test_real_video_stages_are_env_inert(monkeypatch, tmp_path,
                                         real_video_baseline, env):
    got_bytes, got_payloads = _real_video_wire_bytes_for(
        monkeypatch, tmp_path, env, "cell")
    base_bytes, base_payloads = real_video_baseline
    assert got_bytes == base_bytes, (
        f"env {env!r} moved bytes through the REAL video stage code (L4 red)"
    )
    assert got_payloads == base_payloads, (
        f"env {env!r} moved the OUTBOUND payloads (L4 red)"
    )


# ---------------------------------------------------------------------------
# The canary: the law must FAIL on a violator, or it is decoration. A throwaway
# stage that reads env into both its slot value and its outbound params must
# turn the matrix's two comparisons red.
# ---------------------------------------------------------------------------

def _canary_wire_bytes_for(monkeypatch, tmp_path, env: dict[str, str],
                           run_tag: str) -> tuple[bytes, list]:
    import os as _os

    from app.stagegraph import stage as stage_mod
    from app.stagegraph.stage import Backend, Stage, StageOutput
    from tests.test_audio_stages import FakeModelClient

    class CanaryStage(Stage):
        name = "asr"  # wears a legal slot so the record passes the contract gate
        modality = "audio"
        stage_version = 1
        backend = Backend("mock", 1)
        byte_budget = 4096
        server = "whisper"
        consumer = "speculative:t1_canary"

        async def run_async(self, ctx):
            knob = _os.getenv("T1_CANARY", "unset")  # THE VIOLATION (L4)
            await ctx.clients["whisper"].infer({
                "input_b64": "", "codec": "audio/webm", "params": {"knob": knob},
            })
            return StageOutput(value={"value": f"canary-{knob}"})

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / f"cvar-{run_tag}"))
    monkeypatch.setattr(stage_mod, "_REGISTRY", {})
    monkeypatch.setattr(stage_mod, "_discovered", True)
    stage_mod.register_stage(CanaryStage)

    from app.main import create_app

    fs = FakeStorage()
    app = create_app()
    app.state.storage._transport = fs.transport()
    fake = FakeModelClient({"text": "x", "language": "en", "segments": []})
    app.state.model_clients = {"whisper": fake}
    with TestClient(app) as client:
        c1 = make_c1(fs, chunk_id="t1-canary")
        assert client.post("/ingest", json=c1).status_code == 200
    return fs.record_posts_raw[0], fake.calls


def test_matrix_goes_red_on_a_deliberate_violator(monkeypatch, tmp_path):
    bytes_a, calls_a = _canary_wire_bytes_for(
        monkeypatch, tmp_path, {"T1_CANARY": "alpha"}, "a")
    bytes_b, calls_b = _canary_wire_bytes_for(
        monkeypatch, tmp_path, {"T1_CANARY": "beta"}, "b")
    # Both detectors fire: record bytes AND outbound payloads moved with env.
    assert bytes_a != bytes_b, "canary undetected — the byte matrix is decoration"
    assert calls_a != calls_b, "canary undetected — the payload check is decoration"


# ---------------------------------------------------------------------------
# The new-knob guard (cleanup round): every env read in app/ must be on the
# documented operational allowlist. A new knob is a deliberate, reviewed act —
# it lands HERE with its disposition, or the suite is red.
# ---------------------------------------------------------------------------

# The documented operational surface (each has a disposition in
# docs/refactor_stage_C.md):
OPERATIONAL_ENV_ALLOWLIST = {
    # config.py — the Settings survivors
    "STORAGE_URL", "DP_HTTP_TIMEOUT", "VERIFY_BLOB_SHA256",
    "INGEST_ASYNC", "INGEST_WORKERS", "INGEST_QUEUE_MAX",
    "INGEST_MAX_RETRIES", "INGEST_RETRY_BACKOFF", "INGEST_DRAIN_TIMEOUT",
    "DP_VAR_DIR", "DP_REDRIVE_MAX_ATTEMPTS", "INGEST_MODALITY_LIMITS",
    "METRICS_ENABLED",
    # main.py — fleet + eval-latch operational knobs
    "DP_MANIFEST", "DP_SUPERVISOR", "DP_OFFLINE_EVAL",
    # schemas.py — contract-dir override for tests/CI (operational: points at
    # the same frozen files; content identity is the schema $id's)
    "CONTRACTS_DIR",
    # clipcap — the OpenAI-compatible endpoint wiring (output-inert, matrix-proven)
    "VLM_URL", "VLM_API_KEY", "VLM_TIMEOUT_S",
}


def test_env_reads_in_app_are_exactly_the_operational_allowlist():
    import re
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    named = re.compile(
        r"os\.getenv\(\s*[\"']([A-Z0-9_]+)[\"']"
        r"|os\.environ\.get\(\s*[\"']([A-Z0-9_]+)[\"']"
        r"|os\.environ\[\s*[\"']([A-Z0-9_]+)[\"']\s*\]")
    bare = re.compile(r"os\.environ(?!\.get\(|\[)")

    found: dict[str, set[str]] = {}
    bare_sites: list[str] = []
    for py in sorted(app_dir.rglob("*.py")):
        text = py.read_text()
        rel = str(py.relative_to(app_dir.parent))
        for m in named.finditer(text):
            name = m.group(1) or m.group(2) or m.group(3)
            found.setdefault(name, set()).add(rel)
        for line_no, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if bare.search(line) and not named.search(line):
                bare_sites.append(f"{rel}:{line_no}")

    unknown = {n: sorted(sites) for n, sites in found.items()
               if n not in OPERATIONAL_ENV_ALLOWLIST}
    assert not unknown, (
        f"undocumented env reads in app/: {unknown} — a new knob must be "
        "operational-only, added to this allowlist WITH a worklog disposition "
        "(L4: no output-affecting env knob exists)"
    )
    # Bare `os.environ` (whole-mapping) use: only the supervisor's child-env
    # passthrough is licensed.
    assert all(site.startswith("app/supervisor.py") for site in bare_sites), (
        f"bare os.environ outside the supervisor passthrough: {bare_sites}"
    )
