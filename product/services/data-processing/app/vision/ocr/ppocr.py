"""PP-OCRv6 backend — the DP-side CLIENT of the co-located OCR sidecar (D-06).

LATE-BOUND (imported only when ``VIDEO_OCR_BACKEND=ppocr``): plain ``httpx`` against the
loopback sidecar service that tab C1 builds and deploys. We NEVER import the sidecar's
code — the only coupling is the HTTP contract frozen in ``sidecars/ocr/README.md``:

  * ``POST /ocr {image: <base64-jpeg>}`` ->
        ``{regions:[{text, bbox:[x0,y0,x1,y1], conf}], engine, model_sha_det, model_sha_rec}``
  * ``GET /health`` -> ``{ok, model_sha_det, model_sha_rec, ort_version, ep}``

``paddleocr`` pulls ``numpy<2.4`` (conflicts with DP's numpy 2.5.1), so ALL of it lives in
the sidecar's own venv; this process stays a thin HTTP client with no new heavy dep.

The pinned model shas are asserted against ``/health`` ONCE per process (``assert_health``)
before any read — a swapped model file fails loud, never silently in the corpus (D-06).
"""
from __future__ import annotations

import base64
import logging

import httpx

from ..clip_types import Frame, OcrRead, OcrRegion
from . import assemble
from .config import OcrConfig

logger = logging.getLogger("data-processing.vision.ocr.ppocr")

# Model-sha verification is a per-process fact (the sidecar cannot swap a model file under
# a running process), so we assert once per (url, det, rec) and cache — not once per chunk.
_HEALTH_VERIFIED: set[tuple[str, str, str]] = set()


def make_client(cfg: OcrConfig) -> httpx.Client:
    """A per-run httpx client (connection reuse across a chunk's OCR frames). A single
    patchable factory — tests inject a ``MockTransport`` here."""
    return httpx.Client(timeout=cfg.timeout)


def _health_mismatch(health: dict, cfg: OcrConfig) -> str | None:
    """Pure check: return an error message if ``/health`` disagrees with the pinned config,
    else ``None``. A configured sha that is empty is treated as "unpinned" and skipped, so
    an operator who has not pinned shas still gets the ``ok`` gate without a false mismatch."""
    if not health.get("ok", False):
        return "OCR sidecar /health reports not ok"
    for field, want in (
        ("model_sha_det", cfg.model_sha_det),
        ("model_sha_rec", cfg.model_sha_rec),
    ):
        got = str(health.get(field, ""))
        if want and got != want:
            return (
                f"OCR sidecar {field} mismatch: config pinned {want!r} but /health serves "
                f"{got!r} — refusing to write a corpus under a swapped model file"
            )
    return None


def assert_health(cfg: OcrConfig) -> None:
    """Assert the sidecar's model shas match config, RAISING on mismatch. Cached per
    process per (url, det, rec). Called at the START of the ``screentext`` run, before any
    read and before any record is assembled — so a mismatch fails the chunk loudly with
    NOTHING written (D-06: "fails loudly at resolve, not silently in the corpus")."""
    key = (cfg.url, cfg.model_sha_det, cfg.model_sha_rec)
    if key in _HEALTH_VERIFIED:
        return
    with make_client(cfg) as client:
        resp = client.get(f"{cfg.url}/health")
        resp.raise_for_status()
        health = resp.json()
    msg = _health_mismatch(health, cfg)
    if msg:
        raise RuntimeError(msg)
    _HEALTH_VERIFIED.add(key)


def _jpeg_dims(jpeg: bytes) -> tuple[int, int] | None:
    """Parse (width, height) from a JPEG's SOF marker — dependency-free (no PIL). Returns
    ``None`` if the bytes are not a parseable JPEG (bbox then normalized against the config
    frame width and a 16:10 height estimate)."""
    if not jpeg or len(jpeg) < 4 or jpeg[0] != 0xFF or jpeg[1] != 0xD8:
        return None
    i, n = 2, len(jpeg)
    while i + 9 < n:
        if jpeg[i] != 0xFF:
            i += 1
            continue
        marker = jpeg[i + 1]
        # SOF0..SOF15 (excluding DHT/JPG/DAC 0xC4/0xC8/0xCC) carry the frame dimensions.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = (jpeg[i + 5] << 8) | jpeg[i + 6]
            width = (jpeg[i + 7] << 8) | jpeg[i + 8]
            return (width, height) if width and height else None
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = (jpeg[i + 2] << 8) | jpeg[i + 3]
        if seg_len < 2:
            return None
        i += 2 + seg_len
    return None


def _normalize_bbox(
    raw: list, frame_w: int, frame_h: int
) -> tuple[float, float, float, float]:
    """Normalize a wire bbox ``[x0,y0,x1,y1]`` to [0,1]. Already-normalized coordinates
    (all <= 1.0) pass through; pixel coordinates divide by the frame dims."""
    try:
        x0, y0, x1, y1 = (float(v) for v in raw[:4])
    except (TypeError, ValueError, IndexError):
        return (0.0, 0.0, 0.0, 0.0)
    if max(abs(x0), abs(y0), abs(x1), abs(y1)) <= 1.0:
        return (x0, y0, x1, y1)
    w = float(frame_w) or 1.0
    h = float(frame_h) or 1.0
    return (x0 / w, y0 / h, x1 / w, y1 / h)


def read(cfg: OcrConfig, frame: Frame, client: httpx.Client, chunk_id: str) -> OcrRead:
    """OCR one frame via ``POST /ocr``. Raises on a missing hi-res frame or any transport /
    protocol error — the ``screentext`` stage absorbs and counts per-frame failures, and
    raises only if > 50% of a chunk's frames fail."""
    if frame.jpeg_hi is None:
        raise RuntimeError(
            f"ppocr: no hi-res frame at t+{frame.t_offset_s:.2f}s (undecodable/synthetic)"
        )
    image_b64 = base64.b64encode(frame.jpeg_hi).decode("ascii")
    resp = client.post(f"{cfg.url}/ocr", json={"image": image_b64})
    resp.raise_for_status()
    body = resp.json()
    dims = _jpeg_dims(frame.jpeg_hi)
    frame_w = dims[0] if dims else cfg.frame_width
    frame_h = dims[1] if dims else max(1, round(cfg.frame_width * 10 / 16))
    regions = []
    for r in body.get("regions") or []:
        text = str(r.get("text", ""))
        conf = float(r.get("conf", 0.0) or 0.0)
        bbox = _normalize_bbox(r.get("bbox") or [], frame_w, frame_h)
        # Role is assigned DP-side from bbox in assemble.render (role="" here).
        regions.append(OcrRegion(text=text, role="", bbox=bbox, conf=conf))
    return OcrRead(t_offset_s=frame.t_offset_s, regions=tuple(regions))
