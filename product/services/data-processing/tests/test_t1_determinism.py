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
    {"VIDEO_BACKEND": "vlm", "OCR_BACKEND": "ppocr"},
    {"ACOUSTIC_TOP_K": "3", "ACOUSTIC_THRESHOLD": "0.9"},
    {"TRANSLATE_BACKEND": "whisper", "INJECT_CAPTION_BACKEND": "index"},
    {"DP_OFFLINE_EVAL_PACKS": "experimental"},
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
                               run_tag: str) -> bytes:
    from app.stagegraph import stage as stage_mod
    from app.stages.audio.asr import AsrStage
    from app.stages.audio.diarize import DiarizeStage
    from app.stages.audio.acoustic import AcousticStage
    from app.stages.audio.speaker_align import SpeakerAlignStage
    from tests.test_audio_stages import (
        FakeModelClient, GOLDEN_TRANSCRIBE_REAL, GOLDEN_DIARIZE_REAL,
        GOLDEN_TAGS_REAL, C1_REAL,
    )

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / f"rvar-{run_tag}"))
    # The REAL registered classes in a clean registry (discovery off so the
    # video half can't interfere), REAL backends named in the dialect.
    monkeypatch.setattr(stage_mod, "_REGISTRY", {})
    monkeypatch.setattr(stage_mod, "_discovered", True)
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
    return fs.record_posts_raw[0]


@pytest.fixture(scope="module")
def real_baseline_bytes(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    try:
        yield _real_audio_wire_bytes_for(
            mp, tmp_path_factory.mktemp("t1-real-baseline"), {}, "baseline")
    finally:
        mp.undo()


@pytest.mark.parametrize("env", ENV_MATRIX,
                         ids=lambda e: "+".join(sorted(e)) or "baseline")
def test_real_audio_stages_are_env_inert(monkeypatch, tmp_path,
                                         real_baseline_bytes, env):
    got = _real_audio_wire_bytes_for(monkeypatch, tmp_path, env, "cell")
    assert got == real_baseline_bytes, (
        f"env {env!r} moved bytes through the REAL audio stage code (L4 red)"
    )
