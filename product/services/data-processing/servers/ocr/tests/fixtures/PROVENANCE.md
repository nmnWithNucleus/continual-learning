# golden_regions.json — measurement provenance & determinism record

## Input

`screen_planning_notes.jpg`
sha256 `18803e4b1cfddc32e41da98f3515286055a96f1305eb66c6a8cbcb7b3e6a81b0`
(see `INPUT_PROVENANCE.md`; the committed bytes are the fixture).

## What was measured

The `/infer` `result` object, captured through the real wire path — `build_app(OcrBackend)`
+ fastapi `TestClient`, `POST /infer {input_b64: <fixture b64>, codec: "image/jpeg"}` —
in a fresh process, serialized canonically (`json.dumps(..., sort_keys=True, indent=2)`).
Environment: CPU (`CUDA_VISIBLE_DEVICES=`), node-7, Python 3.12.12, 2026-08-06.

Identity of the serving stack, verbatim from `/health` at capture time (identical
in every run):

```json
{
 "model_name": "rapidocr-onnxruntime PP-OCRv4 det+rec",
 "weights": {
 "det_sha256": "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9",
 "rec_sha256": "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b"
 },
 "frameworks": {
 "onnxruntime": "1.27.0",
 "rapidocr_onnxruntime": "1.4.4"
 },
 "device": "cpu",
 "ep": "CPUExecutionProvider",
 "threads": 4
}
```

(These are the same det/rec shas the prior OCR service served — same bundled
ch_PP-OCRv4 ONNX pair.)

## Runs

4 separate fresh processes (golden capture + 3 verification re-runs), same command,
same env. sha256 of each run's canonical `result` JSON:

| run | sha256 of canonical result |
|---|---|
| 1 (golden) | `880af4e4a1ed7bba2aedcdccfb3b45a598169e57953fad593e3cbefcd59711e0` |
| 2 | `880af4e4a1ed7bba2aedcdccfb3b45a598169e57953fad593e3cbefcd59711e0` |
| 3 | `880af4e4a1ed7bba2aedcdccfb3b45a598169e57953fad593e3cbefcd59711e0` |
| 4 | `880af4e4a1ed7bba2aedcdccfb3b45a598169e57953fad593e3cbefcd59711e0` |

`golden_regions.json` is run 1's bytes plus a trailing newline
(file sha256 `1802f5e9414ec853d5ac2703ed1604fb7d9b1dbda7983c122947162cab2a52d6`).

## Verdict

**Byte-identical across all 4 fresh processes.** ONNX Runtime 1.27.0 on
`CPUExecutionProvider` with `intra_op_num_threads=4` pinned is bit-stable for this
det+rec pipeline on this input — verified, not assumed.

## Tolerance policy

**Exact compare, zero tolerance.** `test_golden_smoke_screen_planning_notes` asserts
`result == golden` structurally. Measured bit-stability means any future mismatch is a
real stack change (model file, ORT/rapidocr version, EP, thread pinning) — investigate
and re-ratify the golden; do not loosen the comparison. The whitespace-insensitive
majority check on the known rendered lines is a sanity net on top of the golden, not
a substitute (PP-OCRv4 rec drops many inter-word spaces; the golden pins the measured
text verbatim, e.g. `"QuarterlyPlanningNotes"`).

Content note: the engine returns 7 regions — the synthetic window's 7 rendered text
lines (title, 3 body lines, 2 terminal lines, footer); all 7 match their source
strings after whitespace removal.
