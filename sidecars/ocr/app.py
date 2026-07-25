#!/usr/bin/env python3
# =============================================================================
# app.py -- the Nucleus OCR sidecar service (WS-C / tab C1).
#
#   A co-located, loopback HTTP service that reads on-screen text out of a
#   single screen-capture frame at native resolution on CPU, and hands DP
#   (data-processing) back `[(text, bbox, confidence)]`. It is a SEPARATE
#   deployable, not a DP library, because the PP-OCR stack (onnxruntime,
#   opencv, shapely, pyclipper -- and `numpy<2.4` if paddleocr is chosen)
#   conflicts with DP's installed numpy 2.5.1. The seam quarantines all of it
#   inside this service's own venv and makes the model a URL/file swap (D-06).
#
#   THE WIRE CONTRACT IS FROZEN IN README.md. DP (tab C2) codes against the
#   README, never against this file. Keep the two in lockstep.
#
#   Endpoints:
#     POST /ocr   {image: <base64-jpeg>}
#         -> {regions: [{text, bbox:[x0,y0,x1,y1], conf}, ...],
#             engine, model_sha_det, model_sha_rec}
#     GET  /health
#         -> {ok, model_sha_det, model_sha_rec, ort_version, ep, engine}
#
#   Backends (env `OCR_MODE`):
#     mock  (default) -- no models, no deps, no network, no GPU. Deterministic
#                        synthetic regions derived from the image bytes so the
#                        HTTP wire and the DP `screentext` stage can be exercised
#                        headless in CI. Reports model shas "mock" -- by design
#                        it never matches a pinned production sha.
#     ppocr           -- PP-OCR det+rec ONNX on CPU via onnxruntime. Real text.
#                        Requires the sidecar venv (see requirements.txt).
#
#   The HTTP layer is pure Python stdlib on purpose: `mock` mode must import
#   nothing outside the standard library. The `ppocr` engine lazily imports its
#   heavy deps only when selected, so a bare `python3 app.py` in mock mode has
#   zero third-party imports.
#
#   Posture mirrors services/inference/serve_vllm.sh: env-driven, loopback by
#   default (127.0.0.1), fail-loud on a misconfigured real backend, one exec.
# =============================================================================
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---- config (env, read once at startup) -------------------------------------
OCR_MODE = os.getenv("OCR_MODE", "mock").strip().lower()
OCR_HOST = os.getenv("OCR_HOST", "127.0.0.1")
OCR_PORT = int(os.getenv("OCR_PORT", "8091"))
OCR_EP = os.getenv("OCR_EP", "CPUExecutionProvider").strip()
OCR_THREADS = int(os.getenv("OCR_THREADS", "4"))
# Explicit model files (optional). Empty -> the engine falls back to the ONNX
# files bundled with the installed PP-OCR package, whose paths it discovers and
# whose sha256 it still reports at /health.
OCR_DET_MODEL = os.getenv("OCR_DET_MODEL", "").strip()
OCR_REC_MODEL = os.getenv("OCR_REC_MODEL", "").strip()
OCR_REC_DICT = os.getenv("OCR_REC_DICT", "").strip()
# Cap request bodies: a 1728px JPEG base64 is well under this.
MAX_BODY_BYTES = int(os.getenv("OCR_MAX_BODY_BYTES", str(48 * 1024 * 1024)))


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _strip_data_uri(b64: str) -> str:
    """Tolerate a `data:image/jpeg;base64,....` prefix."""
    if b64.startswith("data:"):
        comma = b64.find(",")
        if comma != -1:
            return b64[comma + 1 :]
    return b64


def _decode_b64_jpeg(image_b64: str) -> bytes:
    # Strip a data-URI prefix and any wrapping whitespace/newlines, then decode
    # with validate=True so genuinely non-base64 garbage RAISES (-> HTTP 400),
    # honoring the frozen contract's "invalid base64 -> 400" guarantee rather
    # than silently decoding junk to empty bytes.
    raw = "".join(_strip_data_uri(image_b64.strip()).split())
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"image is not valid base64: {exc}") from exc


# =============================================================================
# Engines
# =============================================================================
class MockEngine:
    """No models, no deps, no network. Deterministic synthetic regions so the
    HTTP wire and the DP `screentext` stage are exercisable headless.

    Regions are a pure function of the request image bytes: identical bytes ->
    identical regions. This is for wire/shape testing only -- it is NOT the
    corpus-determinism mock (that one lives DP-side and keys off
    `(n_frames, span_seconds, chunk_id)`, never pixels, per handoff finding #5).
    A mock read never claims a real model sha, so a DP `/health`-vs-config sha
    assertion against a mock sidecar fails loud, which is the correct signal
    ("you pointed production at a mock").
    """

    engine = "mock"
    model_sha_det = "mock"
    model_sha_rec = "mock"
    ort_version = None
    ep = None

    # A small fixed pool of realistic macOS-UI-ish strings. Chosen so an
    # integration test that drives frames through the mock has plausible OCR to
    # assemble; none is a real secret.
    _POOL = (
        "Inbox",
        "Compose",
        "Reply all",
        "def process_chunk(ctx, blob):",
        "Q3 planning notes",
        "Terminal - bash",
        "Search or type a command",
        "Send",
        "Draft saved",
        "screentext.py",
    )

    def health(self) -> dict:
        return {
            "ok": True,
            "model_sha_det": self.model_sha_det,
            "model_sha_rec": self.model_sha_rec,
            "ort_version": self.ort_version,
            "ep": self.ep,
            "engine": self.engine,
        }

    def read(self, jpeg: bytes) -> list[dict]:
        seed = hashlib.sha256(jpeg).digest()
        # 0..3 regions, deterministic from the seed.
        n = seed[0] % 4
        regions: list[dict] = []
        for i in range(n):
            idx = seed[1 + i] % len(self._POOL)
            text = self._POOL[idx]
            # Deterministic, non-overlapping, in-frame bboxes in a vertical stack.
            y0 = 40 + i * 60
            x0 = 24 + (seed[5 + i] % 40)
            x1 = x0 + 12 * len(text)
            y1 = y0 + 22
            conf = round(0.80 + (seed[9 + i] % 20) / 100.0, 4)  # 0.80..0.99
            regions.append(
                {"text": text, "bbox": [float(x0), float(y0), float(x1), float(y1)], "conf": conf}
            )
        return regions


class PPOCREngine:
    """PP-OCR det+rec ONNX on CPU via onnxruntime (rapidocr-onnxruntime).

    Loads two explicit ONNX files (detection + recognition) so their sha256 can
    be reported at /health and asserted against DP's pinned config at graph
    resolution -- a swapped model file fails loud, never silently in the corpus
    (D-06). The execution provider is reported too, because ONNX Runtime is not
    guaranteed bit-exact across providers, which is why DP folds `ocr_ep` into
    the version tag.
    """

    engine = "ppocr"

    def __init__(self) -> None:
        # Heavy imports are deferred to here so `mock` mode never touches them.
        import onnxruntime  # noqa: F401
        from rapidocr_onnxruntime import RapidOCR

        self.ort_version = onnxruntime.__version__
        self.ep = OCR_EP

        det_path, rec_path, dict_path = self._resolve_model_paths()
        self._det_path = det_path
        self._rec_path = rec_path

        kwargs: dict = {
            "det_model_path": det_path,
            "rec_model_path": rec_path,
            "intra_op_num_threads": OCR_THREADS,
            # CPU-only; never spin up CUDA even if a GPU build is present.
            "det_use_cuda": False,
            "rec_use_cuda": False,
            "cls_use_cuda": False,
            "use_cls": False,  # angle classifier off: screen text is upright
        }
        if dict_path:
            kwargs["rec_keys_path"] = dict_path
        # Pinned rapidocr-onnxruntime 1.4.4 is `RapidOCR(config_path=None, **kwargs)`
        # and never raises on unknown kwargs. Call directly and let an incompatible
        # future version fail LOUD, rather than a bare fallback silently dropping the
        # CPU-only / angle-classifier-off / thread / rec-dict settings.
        self._ocr = RapidOCR(**kwargs)

        self.model_sha_det = _sha256_file(det_path)
        self.model_sha_rec = _sha256_file(rec_path)
        self._lock = threading.Lock()

        # Import numpy/Pillow used per-request; fail loud now if absent.
        import numpy  # noqa: F401
        from PIL import Image  # noqa: F401

    def _resolve_model_paths(self) -> tuple[str, str, str]:
        det = OCR_DET_MODEL
        rec = OCR_REC_MODEL
        dic = OCR_REC_DICT
        if not det or not rec:
            # Discover the ONNX files bundled with the installed package.
            import rapidocr_onnxruntime as _r

            models_dir = os.path.join(os.path.dirname(_r.__file__), "models")
            if not det:
                det = self._first_match(models_dir, "det")
            if not rec:
                rec = self._first_match(models_dir, "rec")
        for label, p in (("det", det), ("rec", rec)):
            if not p or not os.path.isfile(p):
                raise RuntimeError(
                    f"OCR_MODE=ppocr but the {label} ONNX model was not found "
                    f"(path={p!r}). Set OCR_{label.upper()}_MODEL or install the "
                    f"PP-OCR package into the sidecar venv (see requirements.txt)."
                )
        return det, rec, dic

    @staticmethod
    def _first_match(models_dir: str, needle: str) -> str:
        if not os.path.isdir(models_dir):
            return ""
        cands = sorted(
            f for f in os.listdir(models_dir) if f.endswith(".onnx") and needle in f.lower()
        )
        return os.path.join(models_dir, cands[0]) if cands else ""

    def health(self) -> dict:
        return {
            "ok": True,
            "model_sha_det": self.model_sha_det,
            "model_sha_rec": self.model_sha_rec,
            "ort_version": self.ort_version,
            "ep": self.ep,
            "engine": self.engine,
        }

    def read(self, jpeg: bytes) -> list[dict]:
        import io

        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(jpeg)).convert("RGB")
        arr = np.asarray(img)[:, :, ::-1]  # RGB -> BGR for the PP-OCR pipeline
        with self._lock:
            result, _elapse = self._ocr(arr)
        regions: list[dict] = []
        if not result:
            return regions
        for item in result:
            # rapidocr returns [box(4x2), text, score]
            box, text, score = item[0], item[1], item[2]
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            regions.append(
                {
                    "text": str(text),
                    "bbox": [min(xs), min(ys), max(xs), max(ys)],
                    "conf": round(float(score), 4),
                }
            )
        return regions


def build_engine():
    if OCR_MODE == "mock":
        return MockEngine()
    if OCR_MODE == "ppocr":
        return PPOCREngine()
    raise SystemExit(
        f"[ocr-sidecar] unknown OCR_MODE={OCR_MODE!r}; expected 'mock' or 'ppocr'."
    )


# =============================================================================
# HTTP
# =============================================================================
class Handler(BaseHTTPRequestHandler):
    server_version = "nucleus-ocr-sidecar/1"
    protocol_version = "HTTP/1.1"

    # `engine` is injected onto the server instance by main().
    @property
    def engine(self):
        return self.server.engine  # type: ignore[attr-defined]

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/health":
            try:
                self._send_json(200, self.engine.health())
            except Exception as exc:  # pragma: no cover - defensive
                self._send_json(503, {"ok": False, "error": str(exc)})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/ocr":
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            # A malformed Content-Length must not crash the handler.
            self.close_connection = True
            self._send_json(400, {"error": "invalid Content-Length"})
            return
        if length <= 0:
            self._send_json(400, {"error": "empty body"})
            return
        if length > MAX_BODY_BYTES:
            # We are not going to read the oversized body; leaving it undrained
            # would desync the next request on a keep-alive connection, so close.
            self.close_connection = True
            self._send_json(413, {"error": "body too large"})
            return
        raw = self.rfile.read(length)
        try:
            req = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._send_json(400, {"error": f"invalid json: {exc}"})
            return
        image_b64 = req.get("image") if isinstance(req, dict) else None
        if not isinstance(image_b64, str) or not image_b64:
            self._send_json(400, {"error": "missing 'image' (base64 jpeg)"})
            return
        try:
            jpeg = _decode_b64_jpeg(image_b64)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        try:
            regions = self.engine.read(jpeg)
        except Exception as exc:  # a bad frame must not take the service down
            self._send_json(500, {"error": f"ocr failed: {exc}"})
            return
        self._send_json(
            200,
            {
                "regions": regions,
                "engine": self.engine.engine,
                "model_sha_det": self.engine.model_sha_det,
                "model_sha_rec": self.engine.model_sha_rec,
            },
        )

    def log_message(self, fmt: str, *args) -> None:  # quieter access log
        sys.stderr.write("[ocr-sidecar] %s - %s\n" % (self.address_string(), fmt % args))


def main() -> None:
    t0 = time.time()
    print(
        f"[ocr-sidecar] mode={OCR_MODE} host={OCR_HOST} port={OCR_PORT} "
        f"ep={OCR_EP} threads={OCR_THREADS}",
        flush=True,
    )
    engine = build_engine()  # loads models eagerly in ppocr mode; fails loud
    h = engine.health()
    print(
        f"[ocr-sidecar] engine={h['engine']} ort={h['ort_version']} "
        f"det={h['model_sha_det'][:12]} rec={h['model_sha_rec'][:12]} "
        f"(ready in {time.time() - t0:.2f}s)",
        flush=True,
    )
    httpd = ThreadingHTTPServer((OCR_HOST, OCR_PORT), Handler)
    httpd.engine = engine  # type: ignore[attr-defined]
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
