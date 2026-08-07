#!/usr/bin/env python3
"""servers/ocr -- PP-OCR det+rec ONNX on CPU, inside the model-server framework.

The PPOCREngine from the prior OCR service, ported faithfully
onto dp_servers_common: same engine construction, same model discovery, same
region shape -- but the framework wire (/health identity + /infer envelope)
instead of v0's bespoke /ocr, and NO OCR_* env knobs (L4: engine
settings are pinned in code below; a model swap is a code change now, not an
env override).

Engine pins (the v0 service's measured operating point, now code constants):
  intra_op_num_threads = 4      -- measured op point on CPU
  use_cls              = False  -- angle classifier off: screen text is upright
  *_use_cuda           = False  -- CPU-only; never spin up CUDA
  models               = the ch_PP-OCRv4 det+rec ONNX bundled with the installed
                         rapidocr-onnxruntime package, discovered from its
                         models/ dir and sha256-hashed at load

/infer contract:
  input_b64 -- one JPEG frame, base64 (decoded PIL -> RGB -> BGR numpy exactly
               as v0 did). Invalid base64 / undecodable image -> BackendError
               (deterministic 422: every replica fails it identically).
  codec     -- optional; if given, must be "image/jpeg".
  params    -- none accepted; any key -> BackendError (behavior is pinned here,
               not negotiated per call).
  result    -- {"regions": [{"text", "bbox": [x0,y0,x1,y1], "conf"}, ...]}
               bbox in pixels of the submitted image, origin top-left; text
               VERBATIM and unfiltered -- no thresholding, no dedup, no
               redaction; that is DP-side post-processing (D-07). regions may
               be [] (ran-and-empty honesty).

Concurrency: v0 guarded self._ocr with a per-call lock; here the
framework already serializes /infer (app.py's infer_lock -- backends are not
assumed thread-safe), so this backend keeps no lock of its own and relies on
that serialization.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import os

from dp_servers_common.backend import BackendError, ModelBackend
from dp_servers_common.runner import serve
from dp_servers_common.wire import InferRequest

# ---- engine settings: PINNED IN CODE (L4) -----------------------------------
_THREADS = 4                     # ORT intra-op threads (v0's measured op point)
_EP = "CPUExecutionProvider"     # CPU-only deployment (manifest: gpu null)
_CODEC = "image/jpeg"


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _strip_data_uri(b64: str) -> str:
    """Tolerate a `data:image/jpeg;base64,....` prefix (v0 decode posture)."""
    if b64.startswith("data:"):
        comma = b64.find(",")
        if comma != -1:
            return b64[comma + 1 :]
    return b64


def _decode_b64_jpeg(image_b64: str) -> bytes:
    """Strict base64 decode: genuinely non-base64 garbage RAISES (-> 422), never
    silently decodes junk to empty bytes. Same posture as v0."""
    raw = "".join(_strip_data_uri(image_b64.strip()).split())
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BackendError(f"input_b64 is not valid base64: {exc}") from exc


class OcrBackend(ModelBackend):
    """PP-OCR det+rec ONNX on CPU via rapidocr-onnxruntime (v0's
    PPOCREngine inside the framework seam)."""

    name = "ocr"

    def load(self) -> None:
        # Heavy imports live here: the framework calls load once, in the
        # warmup thread, before any infer.
        import onnxruntime
        from rapidocr_onnxruntime import RapidOCR

        det_path, rec_path = self._resolve_model_paths()
        # Pinned rapidocr-onnxruntime 1.4.4 is `RapidOCR(config_path=None, **kwargs)`
        # and never raises on unknown kwargs. Call directly and let an incompatible
        # future version fail LOUD rather than silently dropping the CPU-only /
        # cls-off / thread settings.
        self._ocr = RapidOCR(
            det_model_path=det_path,
            rec_model_path=rec_path,
            intra_op_num_threads=_THREADS,
            det_use_cuda=False,
            rec_use_cuda=False,
            cls_use_cuda=False,
            use_cls=False,
        )
        self._det_sha256 = _sha256_file(det_path)
        self._rec_sha256 = _sha256_file(rec_path)

        from importlib.metadata import version as _pkg_version

        self._ort_version = onnxruntime.__version__
        self._rapidocr_version = _pkg_version("rapidocr-onnxruntime")

        # Per-request imports; fail loud at load if absent.
        import numpy  # noqa: F401
        from PIL import Image  # noqa: F401

    @staticmethod
    def _resolve_model_paths() -> tuple[str, str]:
        """Discover the det/rec ONNX files bundled with the installed
        rapidocr-onnxruntime package (its models/ dir), exactly like v0's
        _resolve_model_paths -- minus its OCR_DET_MODEL/OCR_REC_MODEL
        env overrides: a model swap is a code change now (L4)."""
        import rapidocr_onnxruntime as _r

        models_dir = os.path.join(os.path.dirname(_r.__file__), "models")
        det = OcrBackend._first_match(models_dir, "det")
        rec = OcrBackend._first_match(models_dir, "rec")
        for label, p in (("det", det), ("rec", rec)):
            if not p or not os.path.isfile(p):
                raise RuntimeError(
                    f"the {label} ONNX model was not found in the installed "
                    f"rapidocr-onnxruntime package (looked in {models_dir!r}); "
                    f"reinstall the venv from requirements.txt."
                )
        return det, rec

    @staticmethod
    def _first_match(models_dir: str, needle: str) -> str:
        if not os.path.isdir(models_dir):
            return ""
        cands = sorted(
            f for f in os.listdir(models_dir) if f.endswith(".onnx") and needle in f.lower()
        )
        return os.path.join(models_dir, cands[0]) if cands else ""

    def identity(self) -> dict:
        return {
            "model_name": "rapidocr-onnxruntime PP-OCRv4 det+rec",
            "weights": {
                "det_sha256": self._det_sha256,
                "rec_sha256": self._rec_sha256,
            },
            "frameworks": {
                "onnxruntime": self._ort_version,
                "rapidocr_onnxruntime": self._rapidocr_version,
            },
            "device": "cpu",
            "ep": _EP,
            "threads": _THREADS,
        }

    def infer(self, request: InferRequest) -> dict:
        import numpy as np
        from PIL import Image

        if request.params:
            raise BackendError(
                f"ocr accepts no params; got {sorted(request.params)} "
                f"(engine settings are pinned in server code, L4)"
            )
        if request.codec is not None and request.codec != _CODEC:
            raise BackendError(
                f"unsupported codec {request.codec!r}; this server takes {_CODEC!r}"
            )
        if not request.input_b64:
            raise BackendError("input_b64 is required (one JPEG frame, base64)")

        jpeg = _decode_b64_jpeg(request.input_b64)
        try:
            img = Image.open(io.BytesIO(jpeg)).convert("RGB")
        except Exception as exc:
            # Undecodable image bytes fail identically on every replica.
            raise BackendError(f"input is not a decodable image: {exc}") from exc
        arr = np.asarray(img)[:, :, ::-1]  # RGB -> BGR for the PP-OCR pipeline

        # No lock here: the framework serializes /infer (one call at a time).
        result, _elapse = self._ocr(arr)

        regions: list[dict] = []
        if result:
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
        return {"regions": regions}


if __name__ == "__main__":
    serve(OcrBackend())
