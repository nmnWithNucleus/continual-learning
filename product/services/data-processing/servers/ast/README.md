# servers/ast — acoustic-event tagging (AST AudioSet)

`MIT/ast-finetuned-audioset-10-10-0.4593` behind the `dp_servers_common` model-server
seam. Model id, revision, and device (cuda, no CPU fallback) are pinned in `server.py`
(L4: no output-affecting env vars). Copied from `app/audio/acoustic/ast.py`; the caption
folding (`caption_from_tags`) stays client-side — this server returns raw tags only.

- **/infer input**: `input_b64` = raw audio container bytes (webm/opus, mp4/aac, wav —
  the transformers pipeline's `ffmpeg_read` shells to system ffmpeg to demux + resample
  to 16 kHz; `codec` is advisory and not consulted). Params: `top_k` (int, default 20).
  Unknown params, bad base64, or undecodable audio → deterministic 422.
- **result**: `{"tags": [{"label": str, "score": float}]}`, descending score.
- **identity**: model_name, weights.revision, frameworks {transformers, torch, ffmpeg},
  device — checked against `servers/manifest.json` `expected_identity`.

Run (supervisor): `DP_SERVER_PORT=<port> CUDA_VISIBLE_DEVICES=<gpu> .venv/bin/python server.py`

Tests (in-process TestClient, no ports):

    cd servers/ast && CUDA_VISIBLE_DEVICES=6 ./.venv/bin/python -m pytest tests/ -q

Golden fixture + determinism evidence: `tests/fixtures/PROVENANCE.md`.
