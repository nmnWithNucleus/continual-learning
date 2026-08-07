# servers/pyannote — speaker diarization model server

Serves `pyannote/speaker-diarization-3.1` behind the model-server seam
(`dp_servers_common`): warmup thread, `GET /health` (identity), `POST /infer`.
The diarization behavior, smoke-validated on node-7 (2026-07-19): ffmpeg pre-decode to
16 kHz mono WAV (torchaudio's soundfile backend can't demux webm/opus), scoped
`weights_only=False` around the checkpoint load (torch >= 2.6 rejects pyannote's
Lightning checkpoints otherwise), turns clamped to `[0, span_seconds]`, labels
renormalized to `spk_0..` by first onset, overlapping turns permitted.

## Setup

```sh
cd servers/pyannote
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt   # torch is ~2 GB; be patient
```

Pins of note: `torch==2.8.0` + `torchaudio==2.8.0` + `pyannote.audio==3.3.2`
(the trio validated on this node 2026-07-19) plus two companions the resolver
would otherwise get wrong: `huggingface_hub==0.36.2` (hub 1.x removed the
`use_auth_token` kwarg pyannote 3.3.2 still passes — TypeError at load) and
`matplotlib==3.10.8` (pyannote 3.3.2 imports it at import time without
declaring it). Both are pinned to the versions the node's validated 2026-07-19
stack actually ran with.

## Gated model / auth

The pipeline is HF-gated. The token's account must have accepted the user
conditions for **both** `pyannote/speaker-diarization-3.1` **and**
`pyannote/segmentation-3.0`. Set `HF_TOKEN` (or `HUGGINGFACE_TOKEN`) in the
environment — operational auth only; the value is never printed, logged, or
written. On a cold cache the first load also downloads the (ungated) embedding
model `pyannote/wespeaker-voxceleb-resnet34-LM`. A missing/invalid token makes
`Pipeline.from_pretrained` return `None` (it does not raise) — the server
detects that and fails the load loudly.

## Wire contract

`POST /infer` body (`dp_servers_common.wire.InferRequest`, extra fields forbidden):

- `input_b64` — raw audio container bytes (webm/opus, m4a/aac, wav, ...), base64
- `codec` — e.g. `"audio/webm"`; drives the ffmpeg temp-file extension
- `params`:
  - `span_seconds` (float, **required**) — chunk span; turns are clamped to it
  - `min_speakers` / `max_speakers` (int >= 1, optional) — clustering hints
  - anything else → deterministic 422

Result: `{"turns": [{"start_s": float, "end_s": float, "speaker": "spk_0"}, ...]}`
— chunk-relative seconds, sorted by `(start_s, end_s)`, first-onset-normalized
labels, overlaps allowed. Undecodable input (ffmpeg failure) → 422
`transient=false`; the same bytes fail identically everywhere.

## Identity (L4)

Everything output-affecting is pinned in `server.py` — never env:
`MODEL_ID="pyannote/speaker-diarization-3.1"`,
`REVISION="84fd25912480287da0247647c3d2b4853cb3ee5d"` (pinned via the
`repo@revision` checkpoint string; pyannote 3.3.2's `from_pretrained` has no
`revision=` kwarg), `DEVICE="cuda"` (no CPU fallback — load fails loud without
CUDA). After load, the resolved snapshot revisions of the two sub-models
(`pyannote/segmentation-3.0`, `pyannote/wespeaker-voxceleb-resnet34-LM`) are
read back from the local HF cache and reported in `identity().weights`, next to
framework versions (`pyannote.audio`, `torch`, `torchaudio`, `ffmpeg`). The
supervisor subset-matches this against `servers/manifest.json`
`expected_identity` (`model_name` + `weights.pipeline_revision`).

## Tests

```sh
cd servers/pyannote && CUDA_VISIBLE_DEVICES=2 ./.venv/bin/python -m pytest tests/ -q
```

In-process via `fastapi.testclient.TestClient` — no port is ever bound. The
golden fixture and its measured output are under `tests/fixtures/`
(`INPUT_PROVENANCE.md`, `PROVENANCE.md`).

## Determinism verdict

See `tests/fixtures/PROVENANCE.md` for the full study. Verdict (measured
2026-08-06): byte-identical canonical JSON across 4 fresh processes — 3 on
GPU 2 and 1 on GPU 3 — with no torch determinism flags forced. The golden test
is an exact compare, no tolerance.
