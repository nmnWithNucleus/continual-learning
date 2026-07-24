"""WS-C consolidation (tab C3): the screentext-stage END-TO-END integration test.

`tests/test_screentext.py` (C2) drives the stage against the IN-PROCESS `mock` backend
and a `MockTransport` — no real socket. THIS test closes the loop the §11 WS-C / C3 prompt
asks for: it boots the REAL co-located OCR sidecar (`sidecars/ocr/app.py`) as a subprocess
in `OCR_MODE=mock` and drives a fixture chunk through the full `ppocr` path over a real
loopback socket:

    select  ->  POST /ocr (real HTTP)  ->  assemble  ->  emit

and asserts the frozen record shape: exactly one `kind='ocr'` unit, `discriminator="ocr"`,
`t_start=None`. It runs the path once directly (`run_sync`) and once through the real
executor (`resolve` + `run_graph`), and confirms the D-06 `/health` sha gate fires loud
over the real wire.

Headless + offline (§11 house rule 5): the sidecar `mock` engine is Python-stdlib only —
no GPU, no models, no network egress (loopback only). It is booted with a cleaned
environment so the mock path cannot lean on DP's venv. The JPEG frames are synthesized
in-process (a valid SOI+SOF0 header so the DP-side `_jpeg_dims` parser reads real
dimensions; the mock engine ignores pixels, seeding off the byte hash). No binary is
committed. The zero-third-party-import PROOF of the sidecar is C1's `sidecars/ocr/test_app.py`;
here we exercise the wire + the DP seam against it.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.processing.base import ProcessedContent, ProcessedUnit
from app.stagegraph import Stage, StageResult
from app.stagegraph.executor import resolve, run_graph
from app.stagegraph.stage import StageContext
from app.vision.clip_types import ClipFrames, Frame
from app.vision.ocr import ppocr
from app.stages.video.screentext import ScreentextStage

# sidecars/ocr/app.py lives at the monorepo root, 4 parents above tests/.
_SIDECAR_APP = Path(__file__).resolve().parents[4] / "sidecars" / "ocr" / "app.py"


# --------------------------------------------------------------------------- fixtures


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    try:
        return s.getsockname()[1]
    finally:
        s.close()


def _tiny_jpeg(width: int = 1728, height: int = 1080, nonce: bytes = b"") -> bytes:
    """A minimal JPEG-shaped blob: SOI + a valid SOF0 marker carrying (width, height),
    a nonce, and EOI. `ppocr._jpeg_dims` reads the SOF0 dims off this; the mock sidecar
    engine never decodes pixels (it seeds off the byte hash), so no real image is needed.
    The nonce lets a caller steer the mock's per-frame region count deterministically."""
    soi = bytes([0xFF, 0xD8])
    # FF C0 | len=0x000B | precision=8 | height(2) | width(2) | ncomp=1 | id,samp,q
    sof = bytes(
        [0xFF, 0xC0, 0x00, 0x0B, 0x08,
         (height >> 8) & 0xFF, height & 0xFF,
         (width >> 8) & 0xFF, width & 0xFF,
         0x01, 0x01, 0x11, 0x00]
    )
    return soi + sof + nonce + bytes([0xFF, 0xD9])


def _legible_frame_bytes(seed: int = 0) -> bytes:
    """Frame bytes the mock sidecar turns into >= 1 region. MockEngine emits
    `sha256(jpeg)[0] % 4` regions, so we pick the first nonce whose blob hashes to a
    non-zero count — deterministic, and it guarantees the OCR text actually crosses the
    wire (an empty read is indistinguishable from `off`, which would make the test hollow)."""
    i = seed
    while True:
        b = _tiny_jpeg(nonce=i.to_bytes(4, "big"))
        if hashlib.sha256(b).digest()[0] % 4 >= 1:
            return b
        i += 1


# Two distinct legible frames, computed once (deterministic).
_FRAME0 = _legible_frame_bytes(0)
_FRAME1 = _legible_frame_bytes(101)


@pytest.fixture(scope="module")
def ocr_sidecar():
    """Boot `sidecars/ocr/app.py` in OCR_MODE=mock on a free loopback port, wait for
    `/health`, yield (base_url, health_json), tear down. Module-scoped: one process for
    the whole file."""
    if not _SIDECAR_APP.is_file():
        pytest.skip(f"OCR sidecar not found at {_SIDECAR_APP}")
    port = _free_port()
    # Cleaned env: only what the mock path needs. Proves the wire does not depend on any
    # DP env (PYTHONPATH etc.); the stdlib-only proof itself is C1's test_app.py.
    env = {
        "OCR_MODE": "mock",
        "OCR_HOST": "127.0.0.1",
        "OCR_PORT": str(port),
        "PATH": os.environ.get("PATH", ""),
    }
    proc = subprocess.Popen(
        [sys.executable, str(_SIDECAR_APP)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    health = None
    try:
        deadline = time.time() + 15.0
        with httpx.Client(timeout=2.0) as c:
            while time.time() < deadline:
                if proc.poll() is not None:  # the sidecar died during boot
                    break
                try:
                    r = c.get(f"{base}/health")
                    if r.status_code == 200:
                        health = r.json()
                        break
                except httpx.HTTPError:
                    time.sleep(0.05)
        if health is None:
            out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
            raise RuntimeError(f"OCR sidecar did not come up on {base}\n{out}")
        assert health["engine"] == "mock"
        yield base, health
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# ------------------------------------------------------------------ context builders


def _clipframes(frame_bytes, span=10.0):
    """A ClipFrames whose frames carry real hi-res JPEG bytes and whose ocr_times select
    every frame (the ppocr client requires jpeg_hi != None)."""
    frames = tuple(
        Frame(index=i, t_offset_s=float(i) * 5.0, jpeg_lo=None, jpeg_hi=fb)
        for i, fb in enumerate(frame_bytes)
    )
    times = tuple(f.t_offset_s for f in frames)
    return ClipFrames(frames=frames, ocr_times=times, idle=False, span_s=span)


def _ctx(clip_frames, *, chunk_id="chunk-INTEG-1", span=10.0, metrics=None):
    return StageContext(
        c1={"chunk_id": chunk_id, "t_start": "2026-07-09T12:00:00Z",
            "t_end": "2026-07-09T12:00:10Z"},
        blob=b"", settings=None, span_seconds=span,
        slots={"clip_frames": clip_frames},
        resources=SimpleNamespace(metrics=metrics),
    )


def _point_dp_at(monkeypatch, base_url, *, sha_det="", sha_rec=""):
    """Wire the DP seam to the REAL sidecar over ppocr. Unpinned shas (default) pass the
    /health ok-gate against the mock; a pinned sha is asserted (and, against a mock
    sidecar reporting sha 'mock', deliberately mismatches → fail-loud)."""
    monkeypatch.setenv("VIDEO_OCR_BACKEND", "ppocr")
    monkeypatch.setenv("VIDEO_OCR_URL", base_url)
    monkeypatch.setenv("VIDEO_OCR_MODEL_SHA_DET", sha_det)
    monkeypatch.setenv("VIDEO_OCR_MODEL_SHA_REC", sha_rec)
    ppocr._HEALTH_VERIFIED.clear()  # no cache carryover between tests


def _assert_frozen_ocr_shape(unit: ProcessedUnit):
    assert unit.content.kind == "ocr"
    assert unit.discriminator == "ocr"
    assert unit.t_start is None and unit.t_end is None
    assert "\n" not in unit.content.text and "\r" not in unit.content.text


# =========================================================================== the wire


def test_sidecar_health_shape_over_the_wire(ocr_sidecar):
    """The live /health is exactly the frozen contract (mock reports sentinel shas)."""
    _base, health = ocr_sidecar
    assert set(health) >= {"ok", "model_sha_det", "model_sha_rec", "ort_version", "ep"}
    assert health["ok"] is True
    assert health["model_sha_det"] == "mock" and health["model_sha_rec"] == "mock"
    assert health["ort_version"] is None and health["ep"] is None


def test_ppocr_over_real_sidecar_emits_one_ocr_record(ocr_sidecar, monkeypatch):
    """select -> POST /ocr (real loopback) -> assemble -> emit, driven directly through
    the stage. Exactly one kind='ocr' unit with the frozen shape, and non-empty
    self-anchored text proving OCR crossed the wire."""
    base, _ = ocr_sidecar
    _point_dp_at(monkeypatch, base)
    result = ScreentextStage().run_sync(_ctx(_clipframes([_FRAME0, _FRAME1])))

    assert len(result.units) == 1
    unit = result.units[0]
    _assert_frozen_ocr_shape(unit)
    # one witness, two channels (R2 corollary): the provided slot IS the record text.
    assert result.slots["ocr_text"] == unit.content.text
    # the mock sidecar returned legible pool text → it flowed through assemble.
    assert unit.content.text != ""
    assert "+0s " in unit.content.text  # self-anchored offset for the first frame


def test_ppocr_over_real_sidecar_through_the_executor(ocr_sidecar, monkeypatch):
    """The same wire, but resolved and run through the REAL executor next to a primary —
    the fullest select→POST /ocr→assemble→emit: the OCR record lands in the assembled
    unit list with the frozen shape, and the dialect carries the ppocr fragment."""
    base, _ = ocr_sidecar
    monkeypatch.setenv("VIDEO_PIPELINE", "clip")
    _point_dp_at(monkeypatch, base)

    class FakeClipprep(Stage):
        name = "clipprep"; modality = "video"; kind = "sidecar"; order = 0
        provides = ("clip_frames",)

        def run_sync(self, ctx):
            return StageResult(slots={"clip_frames": _clipframes([_FRAME0, _FRAME1])})

    class FakePrimary(Stage):
        name = "clipcap"; modality = "video"; kind = "primary"; order = 20
        needs = ("clipprep", "screentext")

        def version_fragment(self, settings):
            return "vidclip-integ-v1"

        def run_sync(self, ctx):
            return StageResult()

        def assemble(self, ctx):
            return [ProcessedUnit(
                content=ProcessedContent(kind="caption", text="a caption"),
                discriminator="")]

    stages = [FakePrimary(), FakeClipprep(), ScreentextStage()]
    resolved = resolve("video", stages, None)
    # Names the actually-bundled engine (PP-OCRv4); reconciled from the design's aspirational
    # ppv6 token per the lead's provenance-honesty fix (see this file's own build-log note).
    assert "+ocr-ppv4-cpu-v1" in resolved.pipeline_version

    ctx = _ctx(None)
    ctx.slots.clear()  # clip_frames comes from FakeClipprep over the graph
    units = asyncio.run(run_graph(resolved, ctx))

    kinds = [(u.content.kind, u.discriminator) for u in units]
    assert ("caption", "") in kinds
    ocr_units = [u for u in units if u.content.kind == "ocr"]
    assert len(ocr_units) == 1
    _assert_frozen_ocr_shape(ocr_units[0])
    assert ctx.slots["ocr_text"] == ocr_units[0].content.text
    assert ocr_units[0].content.text != ""  # real OCR text, injected + emitted


def test_pinned_sha_mismatch_fails_loud_over_the_real_wire(ocr_sidecar, monkeypatch):
    """D-06 corpus safety, proven over the real socket (C2's unit test uses a
    MockTransport): pin a real-looking sha, point ppocr at the mock sidecar (which serves
    sha 'mock'), and the stage RAISES before any read/assemble/emit — nothing is written."""
    base, _ = ocr_sidecar
    _point_dp_at(monkeypatch, base, sha_det="a" * 64, sha_rec="b" * 64)
    with pytest.raises(RuntimeError, match="mismatch"):
        ScreentextStage().run_sync(_ctx(_clipframes([_FRAME0])))


def test_unpinned_sha_passes_health_ok_gate(ocr_sidecar, monkeypatch):
    """The complement: with shas UNPINNED the ok-gate still runs (health.ok must be true)
    and the read proceeds — an operator who has not pinned shas is not falsely blocked."""
    base, _ = ocr_sidecar
    _point_dp_at(monkeypatch, base)  # empty shas
    # Does not raise; emits its record.
    result = ScreentextStage().run_sync(_ctx(_clipframes([_FRAME0])))
    assert len(result.units) == 1
    _assert_frozen_ocr_shape(result.units[0])
