# golden_tags.json — provenance & determinism evidence

## Input
`speech_two_speakers.webm` — sha256
`a2e29465ffc9c4df72a7571e7270fb4d304910637ef56e1770c01ba95ea0756d` (19 693 bytes;
generation provenance in `INPUT_PROVENANCE.md`; the committed bytes are the fixture).

## Params
`{"top_k": 20}` — the prior pipeline construction value, this server's in-code default.
`codec` sent as `"audio/webm"` (advisory; ffmpeg sniffs the container).

## Identity (verbatim, from /health on every run — all runs identical)
```json
{
 "device": "cuda",
 "frameworks": {
 "ffmpeg": "7.1",
 "torch": "2.8.0+cu128",
 "transformers": "5.14.1"
 },
 "model_name": "MIT/ast-finetuned-audioset-10-10-0.4593",
 "weights": {
 "revision": "f826b80d28226b62986cc218e5cec390b1096902"
 }
}
```

## Runs (2026-08-06, node-7, H100 80GB)
Each run: a FRESH process — `build_app(AstBackend)` under fastapi TestClient (no port
bound), wait for `/health` 200, POST `/infer` with the fixture bytes + params above,
canonicalize `result` as `json.dumps(..., sort_keys=True, indent=2) + "\n"`.

| run | GPU (`CUDA_VISIBLE_DEVICES`) | sha256 of canonical result |
|-----|------------------------------|----------------------------|
| 1 | 6 | `8905b4a1f8a5b46cab4004935206d112017f4dce812b448ca61752ca80605022` |
| 2 | 6 | `8905b4a1f8a5b46cab4004935206d112017f4dce812b448ca61752ca80605022` |
| 3 | 6 | `8905b4a1f8a5b46cab4004935206d112017f4dce812b448ca61752ca80605022` |
| 4 | 7 | `8905b4a1f8a5b46cab4004935206d112017f4dce812b448ca61752ca80605022` |

`golden_tags.json` is run 1's canonical bytes verbatim (same sha256). Golden was cut
ONCE; runs 2–4 are verification only.

## Verdict
**Byte-identical across 4 fresh processes on two GPUs** — zero score jitter, zero
label-order flips (fp32 GPU inference on this fixed 6.496 s input is reproducible on
this stack: identical wheels, identical weights revision, same GPU model).

## Tolerance policy
Exact equality (`tags == golden`) in `tests/test_server.py`. No tolerance is justified
by measurement. If jitter ever appears (driver/library change under the same pins),
re-measure across fresh processes and derive a per-label score atol from the observed
deltas — comparing as a label→score mapping if near-tie order flips — rather than
loosening the exact compare blindly.

## Notes
- Top-4 labels are exactly the Speech family — `Speech` (0.663), `Speech synthesizer`
 (0.279), `Narration, monologue` (0.015), `Male speech, man speaking` (0.006).
 "Speech synthesizer" is a correct detection: the fixture is piper-TTS synthesized.
- transformers 5.14.1 computes AST fbank features via its numpy `spectrogram` path
 (torchaudio not installed; not demanded by this stack). It emits a benign
 `audio_utils` UserWarning about one all-zero mel filter — cosmetic, deterministic,
 present on every run.

## 2026-08-06 — cleanup round: second golden, real speech

`golden_tags_real.json` (canonical sha256
`009e2c731204286d6c9a4d108d480dedcaa976984c6a2963224b6ab0461bc654`) — input
`speech_real_dialog.webm` (sha256 `8b190553…ef00b24b`, see INPUT_PROVENANCE.md),
params {"top_k":20}, identity unchanged from above. **Bit-stable across 4
fresh-process runs (3× GPU 6, 1× GPU 7)** — exact compare, zero tolerance.
