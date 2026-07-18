# WS1 — Inference server (Qwen3-VL-32B on vLLM)

Status: **done** · Owner/agent: WS1 build agent · Last updated: 2026-06-30

> **Start here:** read the global [`../HANDOFF.md`](../HANDOFF.md) in full (architecture, CONTRACTS,
> decisions, infra), then this file. Together they're enough to work independently. You own
> **Contract A** (WS2 ↔ vLLM). Keep the Worklog below current; flip your status row in the global file when done.

## Goal
Stand up a **self-hosted Qwen3-VL-32B-Instruct (dense)** inference server that accepts **video + text**
and **streams** tokens, reachable at `http://127.0.0.1:8000/v1` (OpenAI-compatible) on a single a3mega
node. Prove a real video clip in → grounded answer streaming out.

## Deliverables (in `server/`)
- `serve.sh` — launches vLLM with the validated flags (TP=8 / full node, video enabled, the clip/token knobs).
- `healthcheck.sh` (or `.py`) — confirms the server is up and answers a **video** request end-to-end
  (use a tiny sample MP4) with streaming.
- `env.md` (or notes in this file) — exact vLLM version installed, how to install/upgrade it (likely a
  dedicated env, not stock `moe`), GPU/TP layout, and any `transformers`/`qwen-vl-utils` pins.
- A short **measured TTFT table** (≈2K / 8K / 16K input tokens) so we can confirm the clip cap — see risks.

## The contract you must honor (from global → Contract A)
- Endpoint: `POST http://127.0.0.1:8000/v1/chat/completions`, model id `Qwen/Qwen3-VL-32B-Instruct`.
- Must accept a `user` message whose content is `[{type:"video_url",video_url:{url:"file:///abs/clip.mp4"}}, {type:"text",text:"..."}]`.
- Must support `stream:true` (SSE) and per-request `extra_body.mm_processor_kwargs` (`fps`, `max_pixels`, `min_pixels`).
- The clip files WS2 writes live on the **same node filesystem**, referenced by `file://` — no HTTP fetch needed.

## Suggested steps
1. **Pick the runtime build.** Install a **post-March-2026 vLLM** (target latest stable, ~v0.24.0) — the
   pre-fix builds have the video timestamp `AssertionError` (#35909, fixed PR #36136). Likely a fresh
   conda/venv so you don't disturb `moe`. Pull the model (`HF_TOKEN` is in `~/.bashrc`); cache to
   `/mnt/localssd/.hf-home/` to keep it off NFS.
2. **Allocate GPUs.** Grab a **full a3mega node — all 8× H100 — and serve at TP=8** (the user wants
   lowest latency; single user → the whole node serves each request). bf16 weights are 66.7 GB → ~8.3
   GB/GPU, so KV/vision headroom is ample and you can raise `--max-model-len`. **First verify the model's
   attention/KV-head counts divide by 8** — if not, fall back to TP=4. FP8 (`…-Instruct-FP8`, 35.5 GB) is
   an optional further-speed variant to A/B against bf16 once both run.
3. **Launch (planning template — validate flag spellings on YOUR build):**
   ```bash
   vllm serve Qwen/Qwen3-VL-32B-Instruct \
     --tensor-parallel-size 8 \
     --max-model-len 32768 \
     --limit-mm-per-prompt '{"video":1,"image":0}' \
     --mm-processor-kwargs '{"fps":2.0,"max_pixels":262144,"min_pixels":131072}' \
     --media-io-kwargs '{"video":{"num_frames":60}}' \
     --gpu-memory-utilization 0.90 \
     --host 0.0.0.0 --port 8000
   ```
4. **Validate video.** Send a real MP4 via `file://` with `stream:true`; confirm grounded output and that
   **passing `fps` alongside `num_frames`** avoids the timestamp assertion. Verify token counts roughly
   match the budget (~256 tok/unit ≈ 1s/unit at fps 2).
5. **Benchmark TTFT** at a few input sizes; record the table. This is what lets us lock the 30s cap.
6. **Document** the final flags + version in `serve.sh` + notes, and confirm Contract A with WS2/WS6.

## Key files & paths
- `server/serve.sh`, `server/healthcheck.*`, model cache `/mnt/localssd/.hf-home/`.
- Sample MP4 for the healthcheck: record one with WS3's UI, or any short H.264 clip.

## Gotchas / decisions
- **Always pass `fps` with `num_frames`** (timestamp assertion on older builds; harmless on new).
- `num_frames = round(clip_s × fps)` = 60 for the 30s cap; this caps decoded frames, it doesn't pad.
- bf16 over **TP=8** is only ~8.3 GB/GPU of weights — ample KV/vision headroom; you can run a generous
  `--max-model-len`. Confirm head-count divisibility by 8 (else TP=4). FP8 is the optional further-speed
  variant; A/B its TTFT vs bf16 and check vision quality before preferring it.
- patch factor: model config uses patch 16 / merged factor 32; when serving via vLLM the model config
  wins over the `qwen-vl-utils` `image_patch_size=14` default — sanity-check token counts.
- Keep it **single-node with WS2** so `file://` + `127.0.0.1` are trivial. If you must split nodes, WS2
  would have to serve the clip over HTTP instead — flag this to WS6 before doing it.

## Definition of done
vLLM up at `127.0.0.1:8000/v1`, a `file://` video request streams a correct answer, `serve.sh` +
healthcheck committed, version/flags documented, TTFT table recorded, Contract A confirmed with WS2.

## Worklog
- 2026-06-30 — file created (scaffolding). Not started.
- 2026-06-30 — WS1 started. Env recon:
  - On node `nucla3m-a3meganodeset-7`; all 8× H100 80GB **idle** (0 MiB), node State=IDLE+CLOUD,
    not inside a SLURM alloc but GPUs directly visible to `nvidia-smi`. Using the node directly.
  - Existing conda envs: `vllm` has **vLLM 0.17.1** = TOO OLD (has the Qwen3-VL video timestamp
    AssertionError #35909). `moe` has no vLLM. Decision: build a fresh env (don't disturb either).
  - HF cache at `/mnt/localssd/.hf-home/` (1.2 TB free; 81% used). `HF_HOME` NOT exported in
    `~/.bashrc` — must set explicitly. `HF_TOKEN` is in `~/.bashrc`. No Qwen3-VL-32B cached yet
    (Qwen2.5-VL-32B is). Need to pull `Qwen/Qwen3-VL-32B-Instruct` (~66.7 GB).
- 2026-06-30 — **Head-count check (TP=8 valid):** `config.json` → text `num_attention_heads=64`
  (64/8=8 ✓), `num_key_value_heads=8` (8/8=1 ✓, GQA), `num_hidden_layers=64`; vision `num_heads=16`
  (16/8=2 ✓). **All divide by 8 → TP=8, no fallback to TP=4 needed.** patch_size=16, spatial_merge=2,
  temporal_patch=2 (matches the patch-16/merge-32 note).
- 2026-06-30 — **Model downloaded:** `Qwen/Qwen3-VL-32B-Instruct` snapshot
  `0cfaf48183f594c314753d30a4c4974bc75f3ccb`, 63 GB, 14 safetensors shards + all config
  (incl. `video_preprocessor_config.json`, `chat_template.json`). On `/mnt/localssd/.hf-home`.
- 2026-06-30 — **vLLM version BLOCKER + resolution (deviation from ~0.24.0 target, justified):**
  - vLLM **0.24.0** PyPI wheel links **`libcudart.so.13` (CUDA 13)** and pins torch 2.11.0; needs
    driver ≥580. This node's driver is **570.211.01 = CUDA 12.8** → CUDA-13 wheels won't load
    (`ImportError: libcudart.so.13`). vLLM 0.20.0–0.24.0 ALL require CUDA 12.9/13.0 (driver ≥575/580).
  - **Highest CUDA-12 (`libcudart.so.12`) vLLM = 0.19.1** (released 2026-04-18, torch 2.10.0+cu128).
    Verified its `_C.abi3.so` links `libcudart.so.12`. **0.19.1 is post-fix:** PR #36136 (the
    Qwen3-VL `num_frames`/`fps` timestamp AssertionError fix for issue #35909) merged to main
    **2026-03-11**; all releases ≥0.18.0 (Mar 20) include it. So 0.19.1 satisfies "post-March-2026,
    has the timestamp fix" while being installable on the available driver.
  - **Decision: install vLLM 0.19.1** (not 0.24.0). To run 0.24.0 we'd need a driver upgrade to
    ≥580 (out of scope / needs sudo). Flagging to WS6: runtime is **vLLM 0.19.1**, not 0.24.0.
- 2026-06-30 — **Server STOOD UP & validated (vLLM 0.19.1, TP=8, port 8000).**
  - All 8 GPUs loaded (~74 GB/GPU at `--gpu-memory-utilization 0.90`); `/health` 200, `/v1/models`
    lists `Qwen/Qwen3-VL-32B-Instruct`, max_model_len 32768. Model load ≈ 3.5 min.
  - Flag spellings validated on 0.19.1: `--limit-mm-per-prompt`, `--mm-processor-kwargs`,
    `--media-io-kwargs '{"video":{"num_frames":60}}'`, `--tensor-parallel-size`, `--max-model-len`,
    `--gpu-memory-utilization`. (`--media-io-kwargs num_frames` caps decoded frames at 60.)
  - **Video end-to-end PASSED.** `file://` + `video_url` + text, `stream:true`, per-request
    `mm_processor_kwargs{fps,max_pixels,min_pixels}` → grounded streamed answer. On the 6s sample
    (blue bg, white counter) the model correctly read **"counting from 1 to 6"** and named the blue
    background. TTFT 0.9–2.6s, full answer in ~2–4s. **No timestamp AssertionError** (fps passed
    with num_frames). Token math: 6s@512px = 1596 prompt tokens ≈ 266 tok/sec of video ≈ the
    "~256 tok/unit, ~1 unit/s at fps2" budget; 30s@512px = 7964 ≈ predicted ~8K.
  - **TTFT table (TP=8, true cold prefill, unique salt/run → no cache hit):**

    | Input | prompt_tokens | TTFT | total (64 out) |
    |---|---|---|---|
    | ~2K  (256px,30s) | 2204  | **0.19 s** | 0.59 s |
    | ~8K  (512px,30s) | 7964  | **0.56 s** | 0.77 s |
    | ~16K (768px,30s) | 12284 | **0.89 s** | 1.09 s |

    → The V0 **30s cap @ 512px ≈ 8K tokens → 0.56 s TTFT**, far inside the 5–10 s target. The cap
    could safely be raised. (16K row needed a one-off `--max-num-batched-tokens 16384`; production
    encoder-cache budget ≈ 8192 = exactly the 30s@512px clip, so the V0 contract sits at/under it.)
- 2026-06-30 — **CONTRACT A confirmed, with ONE addition WS2/WS6 must honor:**
  - vLLM blocks `file://` media by default. Server launches with
    **`--allowed-local-media-path /mnt/localssd`**. ⇒ **WS2 MUST write its `turn_<id>.mp4` clips
    under `/mnt/localssd/...`** (recommended: `/mnt/localssd/live_video_chat/clips/`). Clips written
    elsewhere (e.g. NFS `/home`, `/tmp` outside this root) will 400 with "Cannot load local files…".
  - Everything else in Contract A holds verbatim: endpoint `127.0.0.1:8000/v1/chat/completions`,
    model id `Qwen/Qwen3-VL-32B-Instruct`, `video_url{file://…}`+`text` parts, `stream:true` SSE
    (`data:{...}` / `[DONE]`), per-request `mm_processor_kwargs{fps,max_pixels,min_pixels}`.
    Verified vLLM accepts these as top-level request keys (no nested `extra_body` needed via raw HTTP;
    the OpenAI Python client would put them under `extra_body`).
  - **Operational ceiling:** a single video item must fit the encoder cache (≈8192 tokens at the
    production config). The 30s/fps2/max_pixels≈262144 contract sits at/under this. If a clip exceeds
    it the response is HTTP 400 "exceeds the pre-allocated encoder cache size" — raise
    `--max-num-batched-tokens` if ever needed.

## Deliverables (all in `server/`)
- `serve.sh` — validated launch (vLLM 0.19.1, TP=8, video flags, `--allowed-local-media-path`).
  `--bg` / `--stop` / `--status` subcommands. Logs `/mnt/localssd/.hf-home/vllm_serve.log`.
- `healthcheck.py` — end-to-end **streaming video** test (`file://` sample MP4 → grounded answer),
  exit 0 = PASS. Prints TTFT.
- `bench_ttft.py` — reproduces the 2K/8K/16K TTFT table.
- `env.md` — exact versions, install steps, head-count check, flags, TTFT table, the encoder-cache
  ceiling note, and run/stop/status commands.
- `samples/sample_counter.mp4` — 6s H.264 groundable test clip (also copied to the allowed clip dir).

## HOW TO RUN
- **Server is currently UP** on node `nucla3m-a3meganodeset-7`, `http://127.0.0.1:8000/v1`,
  vLLM 0.19.1, TP=8. (pid in `/mnt/localssd/.hf-home/vllm_serve.pid`.)
- Start: `server/serve.sh --bg` · Status: `server/serve.sh --status` · Stop: `server/serve.sh --stop`
- Verify: `/home/ubuntu/miniconda3/envs/vllm-vlm/bin/python server/healthcheck.py` (exit 0 = PASS).
- Env: conda `vllm-vlm`. Model cached on `/mnt/localssd/.hf-home`.
