"""The REGISTERED video graph end to end — resolve → run_graph → build_c2 → contract.

Real stage discovery (``stages_for("video")``), real ffmpeg over a generated fixture
(clipprep), client-level fakes for the model sides (the ocr client + an
``httpx.MockTransport`` OpenAI endpoint — plan §3), one GraphResult, ONE C2 v1
record, green against the contract mirror.

THE RULED COUPLING (read before "fixing" a red here): ``clipcap`` hard-``needs``
``screentext``. An OCR failure therefore yields an ``ocr`` HOLE **and a CANCELLED
caption** — the record ships with BOTH slots absent and statuses
``{screentext: failed, clipcap: cancelled}``. This is deliberate (L7 + D-09): the
caption's prompt takes the OCR text as input, so a caption computed without it would
be a silently different witness under an unchanged pipeline_version. v0 enforced the
same coupling by making screentext ``required`` (failing the whole chunk); v1 ships
the honest holey record and lets the heal machinery (L8) redrive it.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app import schemas
from app.pipeline import build_c2
from app.stagegraph.executor import resolve, run_graph
from app.stagegraph.stage import stages_for
from app.vision.clipcap import vlm
from tests import conftest_video as fx

requires_decoder = pytest.mark.skipif(
    not fx.ffmpeg_available(), reason="needs ffmpeg (the single canonical decoder)")

_OCR_FIXTURES = (Path(__file__).parent.parent / "servers" / "ocr" / "tests" / "fixtures")
GOLDEN_REGIONS = json.loads((_OCR_FIXTURES / "golden_regions.json").read_text())

EXPECTED_PV = "clipcap.v1-vlm.v2+clipprep.v1-ffmpeg.v1+screentext.v1-ppocr.v1"

CANNED_REPLY = json.dumps({
    "app": "Terminal",
    "activity": "watching a build",
    "description": "A terminal window switches from a dark to a light theme while output scrolls.",
    "sensitive": False,
})


class FakeOcrClient:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0

    async def infer(self, payload: dict) -> dict:
        self.calls += 1
        if self.fail:
            raise RuntimeError("ocr fleet down (fake)")
        assert payload["codec"] == "image/jpeg"
        return json.loads(json.dumps(GOLDEN_REGIONS))


def _fake_vlm(monkeypatch, calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": CANNED_REPLY}, "finish_reason": "stop"}]})

    monkeypatch.setattr(
        vlm, "make_async_client",
        lambda timeout_s: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _run(blob: bytes, c1: dict, *, ocr_client) -> tuple:
    resolved = resolve("video", stages_for("video"))
    result = asyncio.run(run_graph(
        resolved, c1=c1, blob=blob, span_seconds=10.0,
        clients={"ocr": ocr_client},
    ))
    return resolved, result


# ------------------------------------------------------------------ resolution

def test_registered_video_set_resolves_to_the_expected_dialect():
    resolved = resolve("video", stages_for("video"))
    assert [s.name for s in resolved.stages] == ["clipcap", "clipprep", "screentext"]
    assert resolved.pipeline_version == EXPECTED_PV


# ------------------------------------------------------------------ the green path

@requires_decoder
def test_full_graph_produces_one_valid_v1_record(monkeypatch):
    vlm_calls: list = []
    _fake_vlm(monkeypatch, vlm_calls)
    ocr = FakeOcrClient()
    c1 = fx.make_c1(t_start_epoch=1_700_000_000.0, span_seconds=10.0)

    resolved, result = _run(fx.appswitch_clip(), c1, ocr_client=ocr)

    assert result.statuses == {"clipprep": "ok", "screentext": "ok", "clipcap": "ok"}
    # clipprep emits NO slot; the record carries exactly caption + ocr (plan §2).
    assert set(result.slots) == {"ocr", "caption"}
    assert result.slots["ocr"]["version"] == "screentext.v1-ppocr.v1"
    assert result.slots["caption"]["version"] == "clipcap.v1-vlm.v2"
    # The appswitch cut fired one OCR read; the digest is self-anchored at its time.
    assert ocr.calls == 1
    assert "QuarterlyPlanningNotes" in result.slots["ocr"]["value"]
    assert result.slots["caption"]["value"].startswith(
        "Terminal — watching a build.")
    # D-09: the caption call carried the OCR digest in its prompt.
    body = json.loads(vlm_calls[0].content)
    head_text = body["messages"][1]["content"][0]["text"]
    assert "QuarterlyPlanningNotes" in head_text

    # ONE record (L2), valid against the v1 contract mirror + the pydantic mirror.
    record = build_c2(c1, result.slots, result.pipeline_version)
    assert schemas.validate_c2(record) == []
    assert record["modality"] == "video"
    assert record["pipeline_version"] == EXPECTED_PV
    assert record["t_start"] == c1["t_start"] and record["t_end"] == c1["t_end"]


# ------------------------------------------------------------------ the ruled coupling

@requires_decoder
def test_ocr_failure_is_a_hole_AND_a_cancelled_caption(monkeypatch):
    """THE CONE TEST (loud, deliberate): OCR down ⇒ ocr hole + caption CANCELLED —
    never a caption computed from silently-missing OCR. See the module docstring."""
    vlm_calls: list = []
    _fake_vlm(monkeypatch, vlm_calls)
    ocr = FakeOcrClient(fail=True)
    c1 = fx.make_c1(t_start_epoch=1_700_000_000.0, span_seconds=10.0,
                    chunk_id="chunk-clip-cone-1")

    resolved, result = _run(fx.appswitch_clip(), c1, ocr_client=ocr)

    assert result.statuses == {
        "clipprep": "ok",
        "screentext": "failed",       # the hole (L7/L11: attempted, failed)
        "clipcap": "cancelled",       # the cone — the caption NEVER ran
    }
    assert result.slots == {}         # both slots absent; the record still ships
    assert vlm_calls == []            # the endpoint was never even called

    record = build_c2(c1, result.slots, result.pipeline_version)
    assert schemas.validate_c2(record) == []          # an empty slots map is legal
    # L11 honesty: the dialect still NAMES both stages — a reader distinguishes
    # "attempted and failed" (named + absent) from "never attempted" (unnamed).
    assert "screentext.v1-ppocr.v1" in record["pipeline_version"]
    assert "clipcap.v1-vlm.v2" in record["pipeline_version"]


@requires_decoder
def test_vlm_failure_is_a_caption_hole_with_ocr_intact(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "endpoint down"})

    monkeypatch.setattr(
        vlm, "make_async_client",
        lambda timeout_s: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    c1 = fx.make_c1(t_start_epoch=1_700_000_000.0, span_seconds=10.0,
                    chunk_id="chunk-clip-hole-1")
    resolved, result = _run(fx.appswitch_clip(), c1, ocr_client=FakeOcrClient())

    assert result.statuses == {"clipprep": "ok", "screentext": "ok",
                               "clipcap": "failed"}
    assert set(result.slots) == {"ocr"}               # ocr shipped; caption is a hole
    record = build_c2(c1, result.slots, result.pipeline_version)
    assert schemas.validate_c2(record) == []
