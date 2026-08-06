# golden_diarize.json — provenance + determinism study

## Input

`speech_two_speakers.webm` —
sha256 `a2e29465ffc9c4df72a7571e7270fb4d304910637ef56e1770c01ba95ea0756d`
(committed bytes are the fixture; see `INPUT_PROVENANCE.md` — do not regenerate).

## Params

```json
{"span_seconds": 6.496}
```

`codec = "audio/webm"`; no speaker hints.

## Identity (verbatim `/health` identity, byte-identical across all 4 runs)

```json
{
  "device": "cuda",
  "frameworks": {
    "ffmpeg": "7.1",
    "pyannote.audio": "3.3.2",
    "torch": "2.8.0+cu128",
    "torchaudio": "2.8.0+cu128"
  },
  "model_name": "pyannote/speaker-diarization-3.1",
  "weights": {
    "embedding_revision": "837717ddb9ff5507820346191109dc79c958d614",
    "pipeline_revision": "84fd25912480287da0247647c3d2b4853cb3ee5d",
    "segmentation_revision": "e66f3d3b9eb0873085418a7b813d3b369bf160bb"
  }
}
```

Sub-model revisions are resolved from pyannote's local model cache
(`~/.cache/torch/pyannote`, hub layout — pyannote 3.3.2's own `cache_dir`, not
the default HF hub cache; found empirically on node-7 2026-08-06).

## Runs (2026-08-06, node-7, H100s; no torch determinism flags forced)

Each run is a SEPARATE fresh process: server built in-process
(`fastapi.testclient.TestClient`, no port), warm waited, one `POST /infer`,
result serialized as canonical JSON (`sort_keys=True, indent=2`).

| run | GPU (CUDA_VISIBLE_DEVICES) | result sha256      |
|-----|----------------------------|--------------------|
| 1   | 2                          | `fef8b89c0b925afe…`|
| 2   | 2                          | `fef8b89c0b925afe…`|
| 3   | 2                          | `fef8b89c0b925afe…`|
| 4   | 3                          | `fef8b89c0b925afe…`|

Full digest, all four runs and the committed golden:
`fef8b89c0b925afe6ad6ab3f6d35a3851b168f8bd92fb2163eb425395f81a11b`.

All four runs used a warm local model cache (the wespeaker embedding model was
downloaded once by the very first load attempt of the day, which then failed
before inference on a since-fixed cache-path lookup in `server.py`; no
inference output ever differed).

## Verdict

**Byte-identical everywhere** — 3/3 fresh processes on GPU 2 and 1/1 on GPU 3
produced the identical canonical JSON (2 turns, 2 speakers, identical float
boundaries). No variance observed in speaker count, turn count, boundaries, or
labels.

## Tolerance policy

Exact compare. `tests/test_server.py::test_golden_smoke_two_speakers` asserts
`result == golden` with no tolerance. If a future stack bump introduces
variance, re-run this study and replace the policy with a measured tolerance —
do not loosen the compare speculatively.
