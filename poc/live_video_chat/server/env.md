# WS1 — Inference server: environment & runtime notes

OpenAI-compatible Qwen3-VL-32B-Instruct on vLLM, video-enabled, TP=8.
Endpoint (Contract A): `POST http://127.0.0.1:8000/v1/chat/completions`.

## Validated build

| Component | Version / value | Notes |
|---|---|---|
| **vLLM** | **0.19.1** | highest CUDA-12 build the node's driver can run; post-fix (see below). NOT 0.24.0 — that wheel is CUDA-13-only and won't load on this driver. |
| conda env | `vllm-vlm` (`/home/ubuntu/miniconda3/envs/vllm-vlm`) | fresh; does not disturb `moe` or the old `vllm` env (0.17.1) |
| Python | 3.12 | |
| torch | 2.10.0+cu128 | **cu128** — matches node driver |
| transformers | 5.12.1 (pulled by vLLM) | Qwen3-VL arch `Qwen3VLForConditionalGeneration` |
| Model | `Qwen/Qwen3-VL-32B-Instruct` (bf16, 14 safetensors, ~63 GB) | snapshot `0cfaf48183f594c314753d30a4c4974bc75f3ccb` |
| GPUs | 8× H100 80 GB on `nucla3m-a3meganodeset-7`, **TP=8** | ~74 GB/GPU used at `--gpu-memory-utilization 0.90` |
| HF cache | `/mnt/localssd/.hf-home` (local SSD, off NFS) | set via `HF_HOME` in `serve.sh` |

### Why 0.19.1 and not 0.24.0 (important — deviation from the ~0.24.0 target, justified)
- **vLLM 0.20.0–0.24.0 PyPI wheels link `libcudart.so.13` (CUDA 13)** and pin torch 2.11.0;
  loading them needs an NVIDIA driver ≥ 580. **This node's driver is 570.211.01 (CUDA 12.8),**
  so the 0.24.0 wheel dies with `ImportError: libcudart.so.13`.
- **0.19.1 (released 2026-04-18) is the highest release whose wheel links `libcudart.so.12`**
  (verified by inspecting `vllm/_C.abi3.so`), pinning torch 2.10.0 (cu128 build available).
  cu128 torch loads against driver 570 and `torch.cuda.is_available()` is `True` on all 8 GPUs.
- **0.19.1 is post-fix.** The `Qwen3-VL` video timestamp `AssertionError` (issue #35909) was
  fixed by PR #36136, merged to `main` **2026-03-11**; every release ≥ 0.18.0 (2026-03-20)
  contains it. Validated end-to-end: a `file://` video request with `fps` passed alongside
  `num_frames` streams a grounded answer with **no timestamp assertion**.
- To run 0.24.0 here you would need a host driver upgrade to ≥ 580 (needs sudo; out of WS1 scope).

## Install (reproduce)
```bash
conda create -y -n vllm-vlm python=3.12
PY=/home/ubuntu/miniconda3/envs/vllm-vlm/bin
# 0.19.1 = highest CUDA-12 (libcudart.so.12) build; runs on driver 570 / CUDA 12.8.
$PY/pip install "vllm==0.19.1" --extra-index-url https://download.pytorch.org/whl/cu128
# (0.19.1 already pulls torch 2.10.0; pin the cu128 build explicitly if needed:)
$PY/pip install --index-url https://download.pytorch.org/whl/cu128 \
  torch==2.10.0+cu128 torchvision==0.25.0+cu128 torchaudio==2.10.0+cu128
# model (HF_TOKEN is in ~/.bashrc), cached to local SSD:
HF_HOME=/mnt/localssd/.hf-home $PY/python -c \
  "from huggingface_hub import snapshot_download; \
   snapshot_download('Qwen/Qwen3-VL-32B-Instruct', ignore_patterns=['*.pt','original/*'])"
```

## TP=8 head-count check (from `config.json`)
All divide by 8 → **TP=8 valid, no TP=4 fallback**:
- text `num_attention_heads = 64` (→ 8/rank), `num_key_value_heads = 8` (→ 1/rank, GQA),
  `num_hidden_layers = 64`, `head_dim = 128`, `hidden_size = 5120`.
- vision `num_heads = 16` (→ 2/rank), `patch_size = 16`, `spatial_merge_size = 2`, `temporal_patch_size = 2`.

## Launch flags (validated spellings on 0.19.1 — see `serve.sh`)
```
--tensor-parallel-size 8
--max-model-len 32768
--max-num-seqs 8
--allowed-local-media-path /mnt/localssd        # REQUIRED for file:// (see Contract notes)
--limit-mm-per-prompt '{"video":1,"image":0}'
--mm-processor-kwargs '{"fps":2.0,"max_pixels":262144,"min_pixels":131072}'
--media-io-kwargs '{"video":{"num_frames":60}}' # round(30s*fps2)=60; caps decoded frames
--gpu-memory-utilization 0.90
--host 0.0.0.0 --port 8000
```

## Measured TTFT (TP=8, full node, true cold prefill — unique salt per run, no cache hit)

| Input size | prompt_tokens | TTFT | total (64 out tok) |
|---|---|---|---|
| ~2K  (256px, 30s clip) | 2204  | **0.19 s** | 0.59 s |
| ~8K  (512px, 30s clip) | 7964  | **0.56 s** | 0.77 s |
| ~16K (768px, 30s clip) | 12284 | **0.89 s** | 1.09 s |

- Token math confirmed: ~6s @512px clip = 1596 prompt tokens (~266 tok/sec of video ≈ the
  "~256 tok/unit, ~1 unit/sec at fps 2" budget). 30s @512px ≈ 7964 ≈ the predicted ~8K.
- **The V0 30s cap @ 512px lands at ~8K tokens → ~0.56 s TTFT.** Far inside the 5–10 s target;
  the cap could safely be raised. (16K row needed a temporary `--max-num-batched-tokens 16384`;
  the production config's encoder-cache budget is ~8192, which is exactly the 30s@512px size.)

## Encoder-cache ceiling (operational note)
A single video item must fit the encoder cache budget (≈ `max_num_batched_tokens`, default 8192
under chunked prefill). At the production config that is **~8192 video tokens** = the 30s@512px
clip. Larger items (e.g. 768px → 12k tokens) 400 with *"exceeds the pre-allocated encoder cache
size"*. ⚠️ **Important (found in phone testing):** vLLM does NOT downsample VIDEO to `max_pixels`, so a
raw phone clip (e.g. 640×480 @ num_frames=60) is ~9000 tokens and **exceeds** this 8192 budget → 400.
The **backend therefore normalizes every clip with ffmpeg** (downscale longest side to 512 px, drop audio)
before the model call — see `backend/app.py` `_normalize_clip()` — bounding it to ~6k tokens for any
resolution. To allow longer/higher-res clips beyond that, raise `--max-num-batched-tokens` here.

## Run / stop / status
```bash
server/serve.sh --bg       # start backgrounded (model load ~3.5 min)
server/serve.sh --status   # UP/DOWN
server/serve.sh --stop     # stop
python server/healthcheck.py   # end-to-end streaming video test (exit 0 = PASS)
python server/bench_ttft.py    # reproduce the TTFT table
```
Logs: `/mnt/localssd/.hf-home/vllm_serve.log`. Pidfile: `/mnt/localssd/.hf-home/vllm_serve.pid`.
