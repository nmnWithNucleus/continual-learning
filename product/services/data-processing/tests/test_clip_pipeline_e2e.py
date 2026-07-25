"""Full clip-pipeline resolution + E2E — the moment clip mode lights up (WS-D integration).

Driven through REAL stage discovery (``stages_for("video")``) and the REAL executor
(``resolve`` + ``run_graph``), never a hand-built stage list — so this is the integration
gate the per-stage unit tests deliberately could not be: it proves the three clip stages
(WS-B ``clipprep``, WS-C ``screentext``, WS-D ``clipcap``) compose into exactly the design's
two-record set with a byte-identical span, and that the legacy keyframe graph is untouched.

Headless (house rule 5): ``VIDEO_BACKEND=mock`` + ``VIDEO_OCR_BACKEND=mock`` — no GPU, no
network, no sidecar subprocess. The blob is undecodable, so ``clipprep`` takes its documented
mock synthetic-frames fallback (no ffmpeg dependency); the record SET (count, kinds,
discriminators, span) is a pure function of the chunk, not of decode/model output (D-05).
"""
from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import pytest

from app import schemas
from app.pipeline import build_c2, compute_record_id
from app.stagegraph.executor import resolve, run_graph
from app.stagegraph.stage import StageContext, stages_for

_CLIP_BAND = {"keyframes": 0, "clipprep": 5, "captions": 10, "screentext": 15, "clipcap": 20}


def _video_c1(chunk_id="chunk-CLIP-E2E-1", t_start="2026-07-25T10:00:00Z",
              t_end="2026-07-25T10:00:10Z"):
    return {
        "contract": "C1", "version": "0", "user_id": "pilot-user",
        "device_id": "mac-cli-1", "stream_id": "stream-AAAA", "sequence": 0,
        "chunk_id": chunk_id, "modality": "video", "codec": "video/mp4",
        "t_start": t_start, "t_end": t_end,
        "blob_ref": f"raw/pilot-user/{chunk_id}.mp4",
        "blob_sha256": "0" * 64, "blob_bytes": 9,
    }


def _ctx(c1, *, span=10.0):
    # blob is deliberately undecodable → clipprep's mock synthetic-frames fallback (no ffmpeg).
    return StageContext(
        c1=c1, blob=b"notavideo", settings=None, span_seconds=span,
        slots={}, resources=SimpleNamespace(metrics=None),
    )


def _clip_env(monkeypatch, *, backend="mock", ocr="mock"):
    monkeypatch.setenv("VIDEO_PIPELINE", "clip")
    monkeypatch.setenv("VIDEO_BACKEND", backend)
    monkeypatch.setenv("VIDEO_OCR_BACKEND", ocr)


# ======================================================================== real discovery

def test_video_discovery_lists_all_five_stages_with_unique_orders():
    """After _discover(), the video modality carries all five stages at the locked band
    orders, no duplicate — the property register_stage enforces per modality."""
    stages = stages_for("video")               # real discovery (imports every stage module)
    by_name = {s.name: s for s in stages}
    assert set(by_name) == set(_CLIP_BAND)
    for name, order in _CLIP_BAND.items():
        assert by_name[name].order == order, f"{name} order {by_name[name].order} != {order}"
    orders = [s.order for s in stages]
    assert len(orders) == len(set(orders)) == 5     # no dup orders
    assert by_name["clipcap"].kind == "primary"
    assert by_name["clipcap"].needs == ("clipprep", "screentext")


# ======================================================================== clip resolution

def test_clip_mode_resolves_one_primary_plus_two_sidecars(monkeypatch):
    """VIDEO_PIPELINE=clip: exactly ONE primary (clipcap; legacy captions gated off), plus
    the clipprep + screentext sidecars, and the dialect composes base#cfg+cp+ocr."""
    _clip_env(monkeypatch)
    resolved = resolve("video", stages_for("video"), None)

    assert resolved.primary.name == "clipcap"
    assert {s.name for s in resolved.enabled} == {"clipprep", "screentext", "clipcap"}
    # legacy primary is NOT co-enabled (exactly one primary is a resolve() invariant).
    assert "captions" not in {s.name for s in resolved.enabled}
    assert "keyframes" not in {s.name for s in resolved.enabled}

    # base(vidclip-mock-v1 + no prompt tag + #cfg) + sorted sidecar frags (+cp-v1, +ocr-mock-v1)
    assert re.fullmatch(
        r"vidclip-mock-v1#[0-9a-f]{8}\+cp-v1\+ocr-mock-v1", resolved.pipeline_version
    ), resolved.pipeline_version


def test_clip_pipeline_version_composes_the_vlm_pack_form(monkeypatch):
    """The lead's template vidclip-<be>-v1@<pack>#<cfg>+cp-v1+ocr-<be>-v1 — proven for the
    vlm captioner (which carries the @<pack>.pN.<digest> prompt tag) via resolve() alone,
    no HTTP. A .prompt.md edit forks <digest>, an OUTPUT_AFFECTING knob forks <cfg>."""
    _clip_env(monkeypatch, backend="vlm", ocr="ppocr")
    monkeypatch.setenv("VIDEO_SCENARIO", "screen-mac")
    resolved = resolve("video", stages_for("video"), None)
    assert re.fullmatch(
        r"vidclip-vlm-v1@screen-clip-v1\.p\d+\.[0-9a-f]{8}#[0-9a-f]{8}\+cp-v1\+ocr-ppv4-cpu-v1",
        resolved.pipeline_version,
    ), resolved.pipeline_version


# ======================================================================== the E2E record set

def test_clip_graph_yields_exactly_two_records_caption_and_ocr(monkeypatch):
    """The whole point: one fixture chunk through the REAL executor yields EXACTLY two C2
    records — kind='caption' (disc "") and kind='ocr' (disc "ocr") — both carrying the C1
    span byte-for-byte, both C2-valid, with record_ids folded from the composed dialect."""
    _clip_env(monkeypatch)
    resolved = resolve("video", stages_for("video"), None)
    c1 = _video_c1(t_start="2026-07-25T10:00:00Z", t_end="2026-07-25T10:00:10Z")

    units = asyncio.run(run_graph(resolved, _ctx(c1)))

    # EXACTLY two records, the two frozen kinds/discriminators (D-05).
    assert len(units) == 2
    by_kind = {u.content.kind: u for u in units}
    assert set(by_kind) == {"caption", "ocr"}
    assert by_kind["caption"].discriminator == ""
    assert by_kind["ocr"].discriminator == "ocr"

    pv = resolved.pipeline_version
    for u in units:
        c2 = build_c2(c1, u, pv, "2026-07-25T10:00:12Z")
        # byte-identical span — t_start=None on both units, so build_c2 carries C1 verbatim
        # (abs_time never called → the Z vs +00:00 boundary hazard is structurally unreachable).
        assert c2["t_start"] == c1["t_start"] == "2026-07-25T10:00:00Z"
        assert c2["t_end"] == c1["t_end"] == "2026-07-25T10:00:10Z"
        assert "\n" not in c2["content"]["text"]
        assert c2["record_id"] == compute_record_id(c1["chunk_id"], pv, u.discriminator)
        assert schemas.validate_c2(c2) == []

    # the two records have DISTINCT ids (disjoint discriminators), so their /context upserts
    # never collide — the same-span, same-pv pair is addressable as two claims.
    ids = {build_c2(c1, u, pv, "2026-07-25T10:00:12Z")["record_id"] for u in units}
    assert len(ids) == 2


def test_clip_graph_is_deterministic_across_two_runs(monkeypatch):
    """Determinism contract (house rule 6): identical chunk + settings → identical record
    set (kinds, discriminators, span, text, record_id) on any worker."""
    _clip_env(monkeypatch)
    c1 = _video_c1()

    def run():
        resolved = resolve("video", stages_for("video"), None)
        units = asyncio.run(run_graph(resolved, _ctx(c1)))
        return sorted(
            (u.content.kind, u.discriminator, u.content.text,
             compute_record_id(c1["chunk_id"], resolved.pipeline_version, u.discriminator))
            for u in units
        )

    assert run() == run()


# ======================================================================== legacy untouched

def test_default_keyframe_mode_resolves_legacy_unchanged(monkeypatch):
    """The default (and any unknown VIDEO_PIPELINE) still resolves the legacy keyframe graph
    byte-for-byte: primary 'captions', dialect literally 'vidproc-mock-v0', clip stages
    dormant. The flip is one env var and it is reversible (D-14)."""
    monkeypatch.delenv("VIDEO_PIPELINE", raising=False)   # default -> keyframe
    monkeypatch.setenv("VIDEO_BACKEND", "mock")
    resolved = resolve("video", stages_for("video"), None)
    assert resolved.primary.name == "captions"
    assert {s.name for s in resolved.enabled} == {"keyframes", "captions"}
    assert resolved.pipeline_version == "vidproc-mock-v0"   # frozen legacy dialect, byte-for-byte

    # an unknown value is coerced to keyframe (never disables both graphs — D-14).
    monkeypatch.setenv("VIDEO_PIPELINE", "typo-not-a-mode")
    resolved2 = resolve("video", stages_for("video"), None)
    assert resolved2.primary.name == "captions"
    assert resolved2.pipeline_version == "vidproc-mock-v0"
