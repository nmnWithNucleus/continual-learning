# Nucleus OCR sidecar (WS-C · tab C1)

A co-located, loopback HTTP service that reads on-screen text out of a single
screen-capture frame **at native resolution on CPU**, and hands data-processing (DP)
back `[(text, bbox, confidence)]`. It exists as a **separate deployable** (not a DP
library) so the PP-OCR stack — onnxruntime, opencv, shapely, pyclipper, and `numpy<2.4`
on the paddleocr path, stays quarantined in *its own venv* and never collides with DP's
installed numpy 2.5.1 (design doc D-06). The model is a *file swap*: point
`OCR_DET_MODEL` / `OCR_REC_MODEL` at a different det+rec ONNX pair and `/health`
re-hashes it.

> Design of record: `product/services/data-processing/handoff/ws-video-clip.md`
> — §3 D-06, D-07, D-08 and §11 → WS-C.

---

## THE WIRE CONTRACT (pinned)

**DP (tab C2) codes against this section, never against `app.py`.** The two are
kept in lockstep. Requests and responses are JSON over HTTP/1.1 on loopback.

### `POST /ocr`

Request body:

```json
{ "image": "<base64-encoded JPEG bytes>" }
```

- `image` — required, a base64 string of one JPEG frame. A `data:image/jpeg;base64,`
  prefix is tolerated and stripped. Recommended: the frame rendered at
  `VIDEO_OCR_FRAME_WIDTH` (1728, the mac capture cap — no resample).

Response `200`:

```json
{
  "regions": [
    { "text": "Send reply to Sarah", "bbox": [30.0, 44.0, 327.0, 75.0], "conf": 0.9446 }
  ],
  "engine": "ppocr",
  "model_sha_det": "d2a7720d…",
  "model_sha_rec": "48fc40f2…"
}
```

- `regions` — list, **reading-order not guaranteed** (the DP seam sorts by bbox
  and assigns region roles; D-07). May be empty (`[]`) when nothing legible was
  found.
  - `text` — the recognized string, **verbatim, unfiltered**. The sidecar does
    *no* confidence thresholding, *no* min-length drop, *no* dedup, *no*
    secret redaction — all of that is DP-side post-processing (D-07). The sidecar
    is a dumb specialist: detect, recognize, return.
  - `bbox` — `[x0, y0, x1, y1]` in **pixels of the submitted image**, the
    axis-aligned envelope of the detected quad. Origin top-left. Used by DP for
    reading order + role assignment, then discarded — *not* emitted to C2 (D-08).
  - `conf` — recognition confidence in `[0, 1]`.
- `engine` — `"mock"` | `"ppocr"`.
- `model_sha_det`, `model_sha_rec` — echo of the loaded model shas (so a caller
  can cross-check the response provenance without a second `/health` round-trip).

### `GET /health`

Response `200`:

```json
{
  "ok": true,
  "model_sha_det": "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9",
  "model_sha_rec": "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b",
  "ort_version": "1.27.0",
  "ep": "CPUExecutionProvider",
  "engine": "ppocr"
}
```

*(This is a `ppocr` example. In `mock` mode `model_sha_det`/`model_sha_rec` are the
literal string `"mock"`, `ort_version`/`ep` are `null`, and `engine` is `"mock"` —
see below.)*

- `ok` — `true` when the engine loaded.
- `model_sha_det`, `model_sha_rec` — **sha256 of the two loaded ONNX model files**
  (det + rec). DP pins these in config and asserts them at *graph resolution* —
  a swapped model file fails loud at resolve, never silently in the corpus (D-06).
- `ort_version` — the ONNX Runtime version. Version-relevant because ORT is not
  guaranteed bit-exact across releases.
- `ep` — the execution provider (`CPUExecutionProvider`). Version-relevant
  because ORT is not guaranteed bit-exact across providers; DP folds `ocr_ep`
  into its `cfg_tag`.
- `engine` — `"mock"` | `"ppocr"` (additive to the five contract keys above).

In **mock** mode both shas are the literal string `"mock"` and `ort_version`/`ep`
are `null`. This is deliberate: a mock read never carries a real model sha, so a
DP `/health`-vs-config sha assertion pointed at a mock sidecar **fails loud** —
the correct signal for "you aimed production at a mock."

### Error semantics

| condition | status | body |
|---|---|---|
| `POST /ocr` missing/empty `image` | `400` | `{"error": …}` |
| body is not valid JSON / not valid base64 | `400` | `{"error": …}` |
| body larger than `OCR_MAX_BODY_BYTES` (48 MB) | `413` | `{"error": …}` |
| a single frame fails to decode/recognize | `500` | `{"error": …}` |
| unknown path | `404` | `{"error": "not found"}` |

DP absorbs and counts a per-frame `500` (`dp_ocr_frame_errors_total`) and only
raises when >50 % of a chunk's frames error — so one bad frame never takes the
service or the chunk down.

---

## Backends

Selected by env `OCR_MODE`:

- `mock` (default) — no models, no deps, no network, no GPU. The HTTP layer
  is **Python-stdlib only**; the mock engine imports nothing third-party. Regions
  are a deterministic function of the request image bytes (identical bytes →
  identical regions), so the wire and the DP `screentext` stage are exercisable
  in headless CI. This is the CI/integration backend.
- `ppocr` — PP-OCR det+rec ONNX on CPU via onnxruntime (rapidocr-onnxruntime).
  Real text. Requires the sidecar venv. Heavy deps are imported **lazily** at
  engine construction, so nothing leaks into the mock path.

The default bundled models are **PP-OCRv4** det+rec (what rapidocr-onnxruntime
1.4.4 ships). The design names PP-OCRv6 as the v1 target; the engine is
model-agnostic — set `OCR_DET_MODEL` / `OCR_REC_MODEL` (and `OCR_REC_DICT` for a
non-default rec dictionary) to a v5/v6 pair and `/health` reports the new shas.

---

## Run

```bash
# mock (zero setup — any python3):
OCR_MODE=mock ./run.sh                       # -> 127.0.0.1:8091

# ppocr (one-time venv setup, then run):
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
OCR_MODE=ppocr ./run.sh
```

Point DP at it:

```
VIDEO_OCR_BACKEND=ppocr
VIDEO_OCR_URL=http://127.0.0.1:8091
# pin the two shas from `curl 127.0.0.1:8091/health` into DP config
```

`run.sh` mirrors `services/inference/serve_vllm.sh`: env-driven, loopback default,
one `exec`, fail loud on a misconfigured real backend.

### Config (env)

| var | default | meaning |
|---|---|---|
| `OCR_MODE` | `mock` | `mock` \| `ppocr` |
| `OCR_HOST` | `127.0.0.1` | bind host (loopback for the co-located loop) |
| `OCR_PORT` | `8091` | bind port (**fixed default**; keep DP's `VIDEO_OCR_URL` in sync) |
| `OCR_EP` | `CPUExecutionProvider` | ORT execution provider; reported at `/health` |
| `OCR_THREADS` | `4` | ORT intra-op threads |
| `OCR_DET_MODEL` | *(bundled)* | path to the detection ONNX file |
| `OCR_REC_MODEL` | *(bundled)* | path to the recognition ONNX file |
| `OCR_REC_DICT` | *(bundled)* | path to the rec char dictionary |
| `OCR_MAX_BODY_BYTES` | `50331648` | request body cap (48 MB) |

---

## Files

- `app.py` — the service (stdlib HTTP + `MockEngine` + `PPOCREngine`).
- `run.sh` — launcher (serve_vllm.sh posture).
- `requirements.txt` — real-backend deps (sidecar venv only; never DP's venv).
- `.venv/` — the sidecar's own venv (git-ignored).
- `bakeoff/` — **O-2's bake-off report** and its harness (see below).

## O-2 bake-off — the pilot gate

Can PP-OCR (or any current OCR model) read 13 pt macOS UI text at 1728 px through
CRF 28? This is the WS-C deliverable that **gates the production `ppocr` default**:
ship `VIDEO_OCR_BACKEND=mock` until a model clears **≥ 0.85 key-string recall**
(≥5-char strings, *lenient substring* matching — not exact equality) and
*≤ 0.10 CER* on the focused region. The report, its scorer, and the corpus
provenance are in [`bakeoff/REPORT.md`](bakeoff/report.md).
