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
# 8x H100 80GB): came up TP=8 from the HF cache, a short text completion
# returned in ~1.9 s, and a full serve-loop turn streamed a real C9 answer.
# =============================================================================
set -euo pipefail

# --- environment ---------------------------------------------------------------
# We serve from the `vllm-vlm` conda env (vLLM 0.19.1, torch 2.10/cu128,
# transformers 5.12.1) — the stack the POC validated for this exact model.
# Point VLLM_BIN elsewhere to use a different env.
VLLM_BIN="${VLLM_BIN:-/home/ubuntu/miniconda3/envs/vllm-vlm/bin/vllm}"

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
#   * Cluster is now on CUDA-13 (driver 580). The cu128 vLLM 0.19.1 wheels run
#     fine here (driver 580 is backward-compatible with the CUDA-12.8 runtime).
#   * FOLLOW-UP (separate slice): push to vLLM >= 0.20 with CUDA-13 (cu13) wheels
#     + a matching flash-attn to leverage the new driver. Deferred deliberately —
#     not bundled into the v0.0 finish so a version bump can't be confused with
#     an app-wiring bug. Track in handoff/engineering.md.
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
