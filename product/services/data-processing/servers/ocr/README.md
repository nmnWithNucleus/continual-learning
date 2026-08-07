# servers/ocr — PP-OCR model server (Stage B · WP-B5)

The v0 OCR service's engine (`PPOCREngine` — PP-OCRv4 det+rec ONNX on CPU via
rapidocr-onnxruntime) relocated into the shared model-server framework
(`servers/common`). Same engine, same models, same region semantics — different
wire and different configuration posture:

- **Wire**: the framework contract (`GET /health` with identity, `POST /infer`
  `{input_b64, codec, params}` → `{ok, result}`), not v0's bespoke
  `/ocr`. Error split: deterministic bad input → `422 transient:false`; replica
  hiccup / not warm → `503 transient:true`; unexpected crash → `500 transient:true`.
- **No env knobs (L4)**: v0's `OCR_MODE/OCR_EP/OCR_THREADS/OCR_*_MODEL`
  are gone. Engine settings are pinned in `server.py`: 4 intra-op threads, angle
  classifier off (screen text is upright), CPU-only, models discovered from the
  installed rapidocr-onnxruntime package and sha256-hashed at load. A model swap
  is a code change. Operational env only (`DP_SERVER_HOST/PORT/LOG_LEVEL`).
- **This was built beside the v0 OCR service, not migrated from it.** At the Stage
  F cutover the v0 process was stopped and its tree removed at Stage G; this server
  runs on the manifest's CPU replicas (ports 8151/8152, `gpu: null`).

`/infer` result: `{"regions": [{"text", "bbox": [x0,y0,x1,y1], "conf"}, ...]}` —
bbox in pixels of the submitted image, origin top-left; text **verbatim and
unfiltered** (thresholds/dedup/redaction are DP-side, D-07); `regions: []` when
nothing legible was found (ran-and-empty, not an error). `params` accepts
nothing; unknown params fail deterministically.

## Setup

```bash
cd servers/ocr
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

## Identity (served at /health once warm)

```json
{
  "model_name": "rapidocr-onnxruntime PP-OCRv4 det+rec",
  "weights": {
    "det_sha256": "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9",
    "rec_sha256": "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b"
  },
  "frameworks": {"onnxruntime": "1.27.0", "rapidocr_onnxruntime": "1.4.4"},
  "device": "cpu",
  "ep": "CPUExecutionProvider",
  "threads": 4
}
```

Same det/rec shas v0 served — the same bundled ch_PP-OCRv4 ONNX pair.

## Determinism

**Bit-stable.** The golden fixture's `/infer` result was byte-identical across 4
separate fresh CPU processes (ORT 1.27.0, `CPUExecutionProvider`, 4 threads
pinned); the golden test compares exactly, zero tolerance. Evidence and policy:
`tests/fixtures/PROVENANCE.md`.

## Tests

```bash
cd servers/ocr && CUDA_VISIBLE_DEVICES= ./.venv/bin/python -m pytest tests/ -q
```

In-process via fastapi TestClient — no ports bound. Covers manifest identity
subset-match + hash shape, the golden smoke (`screen_planning_notes.jpg` vs
`golden_regions.json`), deterministic 422s (bad base64, garbage bytes, stray
params, wrong codec, missing input), and empty-regions honesty on a blank JPEG.
