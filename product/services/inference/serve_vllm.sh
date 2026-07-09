#!/usr/bin/env bash
# =============================================================================
# serve_vllm.sh — launch the REAL base model for inference's `vllm` backend.
#
#   Qwen3-VL-32B-Instruct (dense) on vLLM, TP=8, one a3mega node,
#   OpenAI-compatible server on :8000. TEXT-ONLY MVP (no video/image flags).
#
#   >>> GPU NODE ONLY. This is NOT run by the mock loop and NOT invoked by
#   >>> run.sh. It needs 8x H100 (one full a3mega node). On a box without GPUs
#   >>> it will simply fail to start — that is expected. Once it is up, run the
#   >>> app with MODEL_BACKEND=vllm and VLLM_URL=http://<node>:8000.
#
# Provenance: flags/version distilled from poc/live_video_chat WS1 LEARNINGS
# (serving Qwen3-VL-32B on this exact hardware) — written fresh, not lifted.
# The MVP is text-only, so the POC's video knobs
# (--limit-mm-per-prompt / --mm-processor-kwargs / --media-io-kwargs) are
# intentionally omitted; add them back only when a multimodal slice starts.
# =============================================================================
set -euo pipefail

MODEL_ID="${MODEL_ID:-Qwen/Qwen3-VL-32B-Instruct}"
PORT="${VLLM_PORT:-8000}"
TP="${TP_SIZE:-8}"                 # full a3mega node = 8x H100
MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}"   # sane text-only context; bf16@TP8 leaves ample KV headroom
GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.90}"

# Keep the HF cache off NFS (POC used node-local SSD). HF_TOKEN is expected in
# the environment (POC kept it in ~/.bashrc).
export HF_HOME="${HF_HOME:-/mnt/localssd/.hf-home}"
: "${HF_TOKEN:?set HF_TOKEN (gated model download) before running}"

# -----------------------------------------------------------------------------
# Runtime notes (from POC WS1, verified on driver 570.211.01 = CUDA 12.8):
#   * vLLM >= 0.20 wheels link libcudart.so.13 (CUDA 13, driver >= 580) and will
#     NOT load on a CUDA-12 driver. The highest CUDA-12 vLLM is 0.19.1 — and it
#     is already post-fix for the Qwen3-VL timestamp AssertionError (#35909,
#     PR #36136, merged 2026-03-11). Install vLLM 0.19.1 in a dedicated env if
#     this node's driver is < 580; upgrade the driver first to run newer vLLM.
#   * Head-count check for TP=8 (from the model config): text attn heads 64,
#     kv heads 8, vision heads 16 — all divisible by 8, so TP=8 is valid
#     (no TP=4 fallback). Re-verify if the base model changes.
#   * Model weights ~63 GB bf16 -> ~8.3 GB/GPU at TP=8; plenty of KV room.
# -----------------------------------------------------------------------------

echo "[serve_vllm] ${MODEL_ID}  TP=${TP}  max_model_len=${MAX_MODEL_LEN}  :${PORT}"

exec vllm serve "${MODEL_ID}" \
  --tensor-parallel-size "${TP}" \
  --max-model-len "${MAX_MODEL_LEN}" \
  --gpu-memory-utilization "${GPU_MEM_UTIL}" \
  --served-model-name "${MODEL_ID}" \
  --host 0.0.0.0 --port "${PORT}"
