#!/usr/bin/env python3
# =============================================================================
# test_app.py -- tests for the OCR sidecar service (WS-C / tab C1).
#
#   The mock-path tests use only the Python standard library (+ pytest), so they
#   run under any interpreter -- proving the headless-CI promise. The ppocr tests
#   skip unless the sidecar venv's engine is importable.
#
#   Run:  python3 -m pytest test_app.py -q   (from this sidecar dir)
# =============================================================================
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402
import pytest  # noqa: E402

HEALTH_KEYS = {"ok", "model_sha_det", "model_sha_rec", "ort_version", "ep"}


# ---- MockEngine (no deps) ----------------------------------------------------
def test_mock_health_has_frozen_contract_keys():
    h = app.MockEngine().health()
    assert HEALTH_KEYS <= set(h)          # the five frozen keys are present
    assert h["ok"] is True
    assert h["model_sha_det"] == "mock" and h["model_sha_rec"] == "mock"
    assert h["ort_version"] is None and h["ep"] is None
    assert h["engine"] == "mock"


def test_mock_ocr_shape_and_bounds():
    eng = app.MockEngine()
    regions = eng.read(b"some-jpeg-bytes-xyz")
    for r in regions:
        assert set(r) == {"text", "bbox", "conf"}
        assert isinstance(r["text"], str) and len(r["text"]) >= 1
        assert len(r["bbox"]) == 4 and all(isinstance(v, float) for v in r["bbox"])
        assert 0.0 <= r["conf"] <= 1.0


def test_mock_is_deterministic_in_bytes():
    eng = app.MockEngine()
    a = eng.read(b"identical-bytes")
    b = eng.read(b"identical-bytes")
    assert a == b
    # different bytes may differ, but must not raise
    eng.read(b"other-bytes")


def test_mock_path_imports_no_third_party():
    # Loading app + exercising the mock engine must import nothing heavy. Run in
    # a clean subprocess and measure what *app itself* adds to sys.modules, so a
    # fat parent env (which may pre-import numpy) can't mask or fake the result.
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    snippet = (
        "import sys;"
        "base={m.split('.')[0] for m in sys.modules};"
        f"sys.path.insert(0, {here!r});"
        "import app;"
        "app.MockEngine().read(b'abc');"
        "app.MockEngine().health();"
        "heavy={'numpy','cv2','onnxruntime','PIL','rapidocr_onnxruntime','shapely'};"
        "added={m.split('.')[0] for m in sys.modules}-base;"
        "print(sorted(heavy & added))"
    )
    out = subprocess.check_output([sys.executable, "-c", snippet], text=True).strip()
    assert out == "[]", f"mock path imported heavy modules: {out}"


# ---- HTTP wire (mock engine over the real server) ----------------------------
def _serve(engine):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    httpd.engine = engine
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, httpd.server_address[1]


def _post(port, body: bytes, path="/ocr"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _get(port, path="/health"):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture()
def mock_server():
    httpd, port = _serve(app.MockEngine())
    yield port
    httpd.shutdown()
    httpd.server_close()


def test_http_health(mock_server):
    status, body = _get(mock_server, "/health")
    assert status == 200
    assert HEALTH_KEYS <= set(body)


def test_http_ocr_roundtrip(mock_server):
    import base64
    # a minimal JPEG (mock ignores the pixels, only hashes the bytes)
    img_b64 = base64.b64encode(b"\xff\xd8\xff\xd9mock-image").decode()
    status, body = _post(mock_server, json.dumps({"image": img_b64}).encode())
    assert status == 200
    assert set(body) == {"regions", "engine", "model_sha_det", "model_sha_rec"}
    assert body["engine"] == "mock"
    for r in body["regions"]:
        assert set(r) == {"text", "bbox", "conf"}


def test_http_data_uri_prefix_tolerated(mock_server):
    import base64
    b64 = base64.b64encode(b"abc").decode()
    status, _ = _post(mock_server, json.dumps({"image": f"data:image/jpeg;base64,{b64}"}).encode())
    assert status == 200


def test_http_errors(mock_server):
    assert _post(mock_server, json.dumps({}).encode())[0] == 400          # missing image
    assert _post(mock_server, b"not-json")[0] == 400                       # bad json
    assert _post(mock_server, json.dumps({"image": "%%%"}).encode())[0] == 400  # invalid base64 -> 400 (per contract)
    assert _get(mock_server, "/nope")[0] == 404                            # unknown GET path
    assert _post(mock_server, b"{}", path="/nope")[0] == 404               # unknown POST path


def test_malformed_content_length_does_not_crash(mock_server):
    # A non-integer Content-Length must yield 400, not crash the handler.
    import socket

    s = socket.create_connection(("127.0.0.1", mock_server), timeout=5)
    s.sendall(b"POST /ocr HTTP/1.1\r\nHost: x\r\nContent-Length: abc\r\n\r\n")
    resp = s.recv(4096).decode("latin-1")
    s.close()
    assert resp.startswith("HTTP/1.1 400"), resp.splitlines()[0]


# ---- ppocr engine (needs the sidecar venv) -----------------------------------
_has_rapidocr = False
try:
    import rapidocr_onnxruntime  # noqa: F401
    import PIL  # noqa: F401
    _has_rapidocr = True
except Exception:
    pass


@pytest.mark.skipif(not _has_rapidocr, reason="rapidocr/PIL not installed (run in the sidecar venv)")
def test_ppocr_health_and_read():
    import base64
    import io
    from PIL import Image, ImageDraw, ImageFont

    eng = app.PPOCREngine()
    h = eng.health()
    assert HEALTH_KEYS <= set(h)
    assert len(h["model_sha_det"]) == 64 and len(h["model_sha_rec"]) == 64  # sha256 hex
    assert h["ep"] == "CPUExecutionProvider"
    assert h["ort_version"]

    img = Image.new("RGB", (800, 200), (250, 250, 250))
    d = ImageDraw.Draw(img)
    try:
        fnt = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)
    except Exception:
        fnt = ImageFont.load_default()
    d.text((30, 60), "Quarterly review notes", fill=(0, 0, 0), font=fnt)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    regions = eng.read(buf.getvalue())
    joined = " ".join(r["text"] for r in regions).lower().replace(" ", "")
    assert "quarterly" in joined  # read the text back
