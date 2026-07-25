"""WS-G — the legacy keyframe dialect, frozen behind the VIDEO_PIPELINE gate (D-14).

The legacy graph (``keyframes`` order 0 → ``captions`` order 10) now gates on the single
``resolve_pipeline()`` resolver. The gate touches ENABLEDNESS only — never a version
fragment — so the legacy path reproduces ``vidproc-vlm-v0`` / ``vidproc-mock-v0``
byte-for-byte. These tests pin the four WS-G exit criteria:

  * ``pipeline_version`` in legacy mode is the LITERAL frozen string under each backend;
  * ``keyframes`` remains the SINGLE frozen R1 exemption (empty ``version_fragment``), so
    the sidecar contributes nothing and the dialect is the primary's base fragment alone;
  * an unknown / mistyped ``VIDEO_PIPELINE`` resolves to ``keyframe`` and the graph
    resolves — never zero enabled primaries → ``GraphResolutionError`` → a 500 on ingest;
  * the two legacy stages flip TOGETHER off the one resolver (a typo can never disable
    exactly one graph), and ``clip`` disables both — proving the gate is real. With the clip
    cohort integrated (WS-B/C/D), ``clip`` now resolves to the ``clipcap`` primary rather
    than surfacing the pre-integration zero-primary error.

Headless + offline: mock backend, no GPU, no network. The E2E "never 500" check rides the
mock captioner's synthetic-keyframe fallback, so it needs no decoder.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.pipeline import compute_record_id
from app.processing.processors import video as video_proc
from app.stagegraph.executor import resolve
from app.stagegraph.stage import stages_for
from app.vision.mode import MODES, resolve_pipeline

_FX = Path(__file__).resolve().parent / "fixtures"


def _load_video():
    c1 = json.loads((_FX / "video_scenes.c1.json").read_text())
    blob = (_FX / "video_scenes.mp4").read_bytes()
    return c1, blob


def _video_stage(name: str):
    return next(s for s in stages_for("video") if s.name == name)


# ---- The legacy dialect is the LITERAL frozen string, byte-for-byte ----------

def test_legacy_mock_dialect_is_literal(monkeypatch):
    monkeypatch.setenv("VIDEO_PIPELINE", "keyframe")
    monkeypatch.setenv("VIDEO_BACKEND", "mock")
    pv = video_proc.VideoProcessor().pipeline_version(None)
    assert pv == "vidproc-mock-v0"
    assert pv.encode() == b"vidproc-mock-v0"  # byte-for-byte, not merely a prefix


def test_legacy_vlm_dialect_is_literal(monkeypatch):
    monkeypatch.setenv("VIDEO_PIPELINE", "keyframe")
    monkeypatch.setenv("VIDEO_BACKEND", "vlm")
    pv = video_proc.VideoProcessor().pipeline_version(None)
    assert pv == "vidproc-vlm-v0"
    assert pv.encode() == b"vidproc-vlm-v0"


def test_default_unset_reproduces_legacy_dialect(monkeypatch):
    """The shipped default (VIDEO_PIPELINE unset) is the legacy path verbatim — the
    gate cannot change the dialect anyone is running today."""
    monkeypatch.delenv("VIDEO_PIPELINE", raising=False)
    monkeypatch.setenv("VIDEO_BACKEND", "vlm")
    assert resolve_pipeline() == "keyframe"
    assert video_proc.VideoProcessor().pipeline_version(None) == "vidproc-vlm-v0"


# ---- keyframes is the SINGLE frozen R1 exemption -----------------------------

def test_keyframes_is_the_single_frozen_exemption(monkeypatch):
    """`keyframes` is a sidecar with non-empty `provides` yet an empty `version_fragment`
    — the one R1 exemption (D-14). It therefore contributes NOTHING to the dialect, which
    is exactly the primary's base fragment. This is what makes `vidproc-*-v0` reproduce."""
    monkeypatch.setenv("VIDEO_PIPELINE", "keyframe")
    monkeypatch.setenv("VIDEO_BACKEND", "vlm")
    kf = _video_stage("keyframes")
    assert kf.kind == "sidecar" and kf.provides            # would normally demand a fragment
    assert kf.version_fragment(None) == ""                 # ...but it is frozen-exempt

    resolved = resolve("video", stages_for("video"), None)
    assert resolved.primary.name == "captions"
    # Composed dialect == primary base alone: the sidecar adds no fragment term.
    assert resolved.pipeline_version == resolved.primary.version_fragment(None)
    assert resolved.pipeline_version == "vidproc-vlm-v0"


# ---- Unknown / mistyped VIDEO_PIPELINE resolves to keyframe -------------------

@pytest.mark.parametrize(
    "bad",
    ["", "   ", "clipp", "keyframes", "keyfram", "banana", "kf", "0",
     "true", "none", "off", "clip-v1", "key frame"],
)
def test_unknown_value_resolves_to_keyframe(monkeypatch, bad):
    monkeypatch.setenv("VIDEO_PIPELINE", bad)
    assert resolve_pipeline() == "keyframe"


@pytest.mark.parametrize(
    "raw,expect",
    [("keyframe", "keyframe"), ("  keyframe  ", "keyframe"), ("KEYFRAME", "keyframe"),
     ("KeyFrame", "keyframe"), ("clip", "clip"), (" CLIP ", "clip"), ("Clip", "clip")],
)
def test_resolver_normalizes_case_and_whitespace(monkeypatch, raw, expect):
    monkeypatch.setenv("VIDEO_PIPELINE", raw)
    assert resolve_pipeline() == expect


def test_resolver_reads_env_fresh_each_call(monkeypatch):
    """D-14: resolved per call so a test (or a live redeploy) can flip the mode without a
    re-import; the value is read at graph-build time, not frozen at process start."""
    monkeypatch.setenv("VIDEO_PIPELINE", "clip")
    assert resolve_pipeline() == "clip"
    monkeypatch.setenv("VIDEO_PIPELINE", "keyframe")
    assert resolve_pipeline() == "keyframe"
    monkeypatch.setenv("VIDEO_PIPELINE", "banana")
    assert resolve_pipeline() == "keyframe"


def test_modes_are_exactly_keyframe_and_clip():
    assert MODES == ("keyframe", "clip")


def test_unknown_value_graph_resolves_never_zero_primary(monkeypatch):
    """The load-bearing D-14 safety property: a typo must not disable BOTH graphs, leave
    resolution with zero enabled primaries, and 500 every video ingest. Unknown → keyframe
    → both legacy stages enabled → the graph resolves to the legacy dialect."""
    monkeypatch.setenv("VIDEO_PIPELINE", "banana")
    monkeypatch.setenv("VIDEO_BACKEND", "mock")
    resolved = resolve("video", stages_for("video"), None)  # must NOT raise
    assert resolved.primary.name == "captions"
    assert {s.name for s in resolved.enabled} == {"keyframes", "captions"}
    assert resolved.pipeline_version == "vidproc-mock-v0"


# ---- The two legacy stages flip TOGETHER off the one resolver ----------------

@pytest.mark.parametrize("mode", ["keyframe", "clip", "banana", "", "CLIP", "  keyframe "])
def test_both_legacy_stages_flip_together(monkeypatch, mode):
    """One resolver drives both gates, so `keyframes` and `captions` are always enabled or
    disabled as a pair — a mistype can never disable exactly one and orphan the other."""
    monkeypatch.setenv("VIDEO_PIPELINE", mode)
    kf = _video_stage("keyframes").is_enabled(None)
    cap = _video_stage("captions").is_enabled(None)
    assert kf == cap
    assert kf == (resolve_pipeline() == "keyframe")


def test_clip_mode_disables_the_legacy_pair(monkeypatch):
    """`clip` disables both legacy stages — proof the gate is real. With the clip stages now
    integrated (WS-B/C/D), clip mode resolves to the clip primary `clipcap` (not the
    zero-primary error this asserted pre-integration), and the legacy pair is absent from the
    resolved graph."""
    monkeypatch.setenv("VIDEO_PIPELINE", "clip")
    monkeypatch.setenv("VIDEO_BACKEND", "mock")
    assert _video_stage("keyframes").is_enabled(None) is False
    assert _video_stage("captions").is_enabled(None) is False
    resolved = resolve("video", stages_for("video"), None)
    assert resolved.primary.name == "clipcap"
    enabled = {s.name for s in resolved.enabled}
    assert "keyframes" not in enabled and "captions" not in enabled


# ---- E2E: an unknown VIDEO_PIPELINE ingests (200), never 500 -----------------

def test_e2e_unknown_pipeline_ingests_under_frozen_dialect(client, monkeypatch):
    """End to end through /ingest: an unknown VIDEO_PIPELINE never 500s (never zero
    primaries), and the record identities are minted under the frozen `vidproc-mock-v0`
    dialect byte-for-byte. Rides the mock synthetic-keyframe fallback, so no decoder."""
    monkeypatch.setenv("VIDEO_PIPELINE", "banana")  # unknown → keyframe
    c1, blob = _load_video()
    client.fake_storage.add_blob(c1["blob_ref"], blob)

    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 200, resp.text
    record_ids = resp.json()["record_ids"]
    assert record_ids, "an unknown mode must still resolve a primary and emit records"

    # Byte-for-byte record identity under the frozen legacy dialect (discriminators are
    # the keyframe indices 0..N-1, real or synthetic).
    for i, rid in enumerate(record_ids):
        assert rid == compute_record_id(c1["chunk_id"], "vidproc-mock-v0", str(i))

    for c2 in client.fake_storage.record_posts:
        assert c2["pipeline_version"] == "vidproc-mock-v0"


def test_e2e_keyframe_mode_is_byte_identical_to_default(client, monkeypatch):
    """Explicit VIDEO_PIPELINE=keyframe produces the identical record set to the default
    (unset) run — the gate is inert in the mode it defends."""
    c1, blob = _load_video()

    monkeypatch.setenv("VIDEO_PIPELINE", "keyframe")
    client.fake_storage.add_blob(c1["blob_ref"], blob)
    explicit = client.post("/ingest", json=c1).json()["record_ids"]

    # Same chunk, default (unset) mode → identical ids (idempotent upsert, one dialect).
    monkeypatch.delenv("VIDEO_PIPELINE", raising=False)
    default = client.post("/ingest", json=c1).json()["record_ids"]
    assert explicit == default
    assert explicit == [
        compute_record_id(c1["chunk_id"], "vidproc-mock-v0", str(i))
        for i in range(len(explicit))
    ]
