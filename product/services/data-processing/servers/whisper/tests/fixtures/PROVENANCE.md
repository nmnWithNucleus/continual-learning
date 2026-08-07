# golden_transcribe.json — provenance

## Input
`speech_two_speakers.webm`, sha256
`a2e29465ffc9c4df72a7571e7270fb4d304910637ef56e1770c01ba95ea0756d`
(19 693 bytes, webm/opus 16 kHz mono, 6.496 s — see `INPUT_PROVENANCE.md`; not regenerated).

## Server identity (verbatim from /health at generation time, 2026-08-06)
```json
{
  "compute_type": "float16",
  "device": "cuda",
  "frameworks": {
    "av": "18.0.0",
    "ctranslate2": "4.8.1",
    "faster_whisper": "1.2.1"
  },
  "model_name": "Systran/faster-whisper-large-v3",
  "weights": {
    "revision": "edaa852ec7e145841d8ffdb056a99866b5f0a478"
  }
}
```

## Params
```json
{"task": "transcribe", "beam_size": 1, "language": "en", "vad": true}
```
`codec: "audio/webm"` (informational); input rides base64 in `input_b64`.

## How generated
Once, through the REAL server path: `build_app(WhisperBackend())` + in-process
FastAPI TestClient, POST /infer, result saved as canonical JSON
(`json.dumps(result, sort_keys=True, indent=2)`). No ports bound.

## Determinism evidence
4 runs, each a SEPARATE fresh process (not one process repeating):

| run | GPU (CUDA_VISIBLE_DEVICES) | sha256 of canonical result JSON |
|-----|----------------------------|----------------------------------|
| 1   | 4                          | ccda989fdc134815cfaab84f453654bac19cbcbe9a5f603183936f1388d61376 |
| 2   | 4                          | ccda989fdc134815cfaab84f453654bac19cbcbe9a5f603183936f1388d61376 |
| 3   | 4                          | ccda989fdc134815cfaab84f453654bac19cbcbe9a5f603183936f1388d61376 |
| 4   | 5                          | ccda989fdc134815cfaab84f453654bac19cbcbe9a5f603183936f1388d61376 |

**Verdict: bit-stable across 4 fresh-process runs, GPUs 4 and 5** (H100 80GB
HBM3 both; replica equivalence holds). Text, language, and all segment
start_s/end_s floats byte-identical.

## Tolerance policy
Exact compare (`result == golden`, full dict, floats included). No numeric
tolerance is warranted: zero observed delta across runs and GPUs. If this test
ever fails, that is an identity/behavior change (weights, framework, device, or
decode path) — investigate and re-ratify the golden; do not loosen the compare.

## 2026-08-06 — cleanup round: identity extended (golden unchanged)

`identity.frameworks` now also reports `onnxruntime` (1.28.0): it executes the
Silero VAD gate, which is output-affecting, so it belongs in the stated dialect.
Previously-floating transitives pinned in requirements.txt: onnxruntime==1.28.0,
huggingface_hub==1.26.0, tokenizers==0.23.1 — all equal to what this venv already
ran when the golden was cut, so `golden_transcribe.json` is untouched
(sha256 ccda989fdc134815cfaab84f453654bac19cbcbe9a5f603183936f1388d61376
re-verified after the change; see the migration drill worklog cleanup round).

## 2026-08-06 — cleanup round: second golden, real speech

`golden_transcribe_real.json` (canonical sha256
`f5da3b6ec0a9c70e98db7aaa554cd4989aab6eb79c5b8ae119f69201b58e1ef6`) — input
`speech_real_dialog.webm` (sha256 `8b190553…ef00b24b`, see INPUT_PROVENANCE.md),
params {"task":"transcribe","beam_size":1,"language":"en","vad":true}, identity
unchanged from above. **Bit-stable across 4 fresh-process runs (3× GPU 4, 1× GPU 5)**
— all four canonical hashes identical; exact compare, zero tolerance, same policy.
