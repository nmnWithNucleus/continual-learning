#!/usr/bin/env bash
# =============================================================================
# serve_vllm.sh — launch the REAL base model for inference's `vllm` backend.
#
#   Qwen3-VL-32B-Instruct (dense) on vLLM, TP=8, one a3mega node,
#   OpenAI-compatible server on :8000. TEXT-ONLY MVP (no video/image flags).
#
#   >>> GPU NODE ONLY. Needs 8x H100 (one full a3mega node). NOT run by the
#   >>> mock loop / run.sh. Once up, run the app with MODEL_BACKEND=vllm and
#   >>> VLLM_URL=http://<node>:8000 (or restart run_all.sh with .env flipped).
#
# VERIFIED 2026-07-09 on node nucla3m-a3meganodeset-7 (driver 580.159.03,
# 8x H100 80GB): came up TP=8 from the HF cache, short text completions
# returned in ~2-3 s, and full serve-loop turns streamed real C9 answers —
# on BOTH vLLM 0.24.0 (primary, below) and 0.19.1 (fallback).
# =============================================================================
set -euo pipefail

# --- environment ---------------------------------------------------------------
# PRIMARY: conda `vllm-cu13` — vLLM 0.24.0, torch 2.11.0, transformers 5.13.0,
# CUDA-13 (cu13) wheels + flashinfer. Validated 2026-07-09 on driver 580.
# FALLBACK: conda `vllm-vlm` — vLLM 0.19.1 / cu128 (the POC-proven stack). To
# fall back: VLLM_BIN=/home/ubuntu/miniconda3/envs/vllm-vlm/bin/vllm bash serve_vllm.sh
VLLM_BIN="${VLLM_BIN:-/home/ubuntu/miniconda3/envs/vllm-cu13/bin/vllm}"

MODEL_ID="${MODEL_ID:-Qwen/Qwen3-VL-32B-Instruct}"
PORT="${VLLM_PORT:-8000}"
HOST="${VLLM_HOST:-127.0.0.1}"     # 127.0.0.1 for the local loop; 0.0.0.0 to expose (e.g. behind a tunnel)
TP="${TP_SIZE:-8}"                 # full a3mega node = 8x H100
MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}"   # text-only context; bf16@TP8 leaves ample KV headroom
GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.90}"

# The model is expected in the default HF cache (~/.cache/huggingface), where it
# is already downloaded (~63 GB). Do NOT override HF_HOME to an empty path or
# vLLM will try to re-download. To stage on node-local SSD for faster load, first
# copy the cache there and export HF_HOME to it.
: "${HF_TOKEN:?set HF_TOKEN (gated model download) before running}"

# -----------------------------------------------------------------------------
# Runtime notes (verified 2026-07-09):
#   * Cluster is on CUDA-13 (driver 580). PRIMARY is now vLLM 0.24.0 with cu13
#     wheels + flashinfer (the modern attention backend — no separate flash-attn
#     build needed). Done as its own step AFTER v0.0 closed on 0.19.1, so the
#     version bump was isolated from app wiring (it validated first try).
#   * 0.19.1/cu128 (vllm-vlm) stays as the fallback env; both serve this model.
#   * Head-count check for TP=8 (Qwen3-VL-32B config): text attn heads 64, kv
#     heads 8, vision heads 16 — all divisible by 8, so TP=8 is valid. Re-verify
#     if the base model changes.
#   * Weights ~63 GB bf16 -> ~8.3 GB/GPU of weights at TP=8; ~75 GB/GPU total at
#     util 0.90 (KV + vision + activations). Startup (weight load from NFS) takes
#     a few minutes; watch for "Application startup complete" in the log.
#   * TEXT-ONLY: the POC's video knobs (--limit-mm-per-prompt / --mm-processor-kwargs
#     / --media-io-kwargs) are intentionally omitted; add them only for a
#     multimodal slice (and note vLLM 0.19.1 is post-fix for the Qwen3-VL video
#     timestamp AssertionError, PR #36136).
# -----------------------------------------------------------------------------

echo "[serve_vllm] ${VLLM_BIN}"
echo "[serve_vllm] ${MODEL_ID}  TP=${TP}  max_model_len=${MAX_MODEL_LEN}  ${HOST}:${PORT}"

exec "${VLLM_BIN}" serve "${MODEL_ID}" \
  --tensor-parallel-size "${TP}" \
  --max-model-len "${MAX_MODEL_LEN}" \
  --gpu-memory-utilization "${GPU_MEM_UTIL}" \
  --served-model-name "${MODEL_ID}" \
  --host "${HOST}" --port "${PORT}"
