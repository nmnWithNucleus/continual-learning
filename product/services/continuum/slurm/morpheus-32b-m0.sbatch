#!/bin/bash
#SBATCH --job-name=morpheus-32b-m0
#SBATCH --partition=a3mega
#SBATCH --nodes=1
#SBATCH --gres=gpu:2
#SBATCH --cpus-per-task=32
#SBATCH --time=08:00:00
#SBATCH --output=/home/ubuntu/nmn/continual_learning/product/services/continuum/var/slurm/logs/%x_%j.out
# 2b's one unestablished deliverable: a 32B adapter WE train, end to end, through
# gate -> C5 publish -> vLLM load. 32B needs 2 cards (measured: it does not fit one
# at any batch size). Bar is M0 mechanics + in-band sanity, NOT strict parity —
# there is no 32B golden at this probe set.
set -uo pipefail
source /home/ubuntu/nmn/continual_learning/product/services/continuum/slurm/_common.sh
cd $SVC
OUT=var/diag/m0_32b
export MORPHEUS_SHARD_GPUS=2 MORPHEUS_SHARD_MAX_MEMORY=76GiB
$TRAIN_PY scripts/m0_smoke.py --day 5 --device cuda:0 \
  --base-model Qwen/Qwen3-VL-32B-Instruct --out $OUT --skip-vllm || exit 1
# vLLM lives in its own pinned env (incompatible transformers). util 0.90 / len 2048:
# a 32B base leaves little room for KV cache, which is what sank the first attempt.
$JUDGE_PY scripts/vllm_load_check.py --adapter $OUT/adapter \
  --base-model Qwen/Qwen3-VL-32B-Instruct --gpu-mem-util 0.90 --max-model-len 2048 \
  --out $OUT/vllm_load.json || exit 1
$JUDGE_PY scripts/judge_preds.py --preds $OUT/preds.jsonl --out $OUT/judge.json --label m0-32b
echo "== DONE $SLURM_JOB_NAME $(date -u +%FT%TZ)"
