#!/bin/bash
#SBATCH --job-name=morpheus-ref-s5
#SBATCH --partition=a3mega
#SBATCH --nodes=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=16
#SBATCH --time=10:00:00
#SBATCH --output=/home/ubuntu/nmn/continual_learning/product/services/continuum/var/slurm/logs/%x_%j.out
# Reference chain seed 5. Their LOW TAIL is unmeasured at n=4, and that is half the
# remaining uncertainty about our seed 0. Runs the reference code unmodified.
set -uo pipefail
source /home/ubuntu/nmn/continual_learning/product/services/continuum/slurm/_common.sh
OUT=$SVC/var/diag/refchains/ref_s5
mkdir -p $OUT
cd /home/ubuntu/nmn/cl-research/research/engram/code
$TRAIN_PY phase_d_driver.py --arm replay --replay-frac 0.30 \
  --days 5,9,12,13,17,21 --seed 5 --model Qwen/Qwen3-VL-8B-Instruct --out-dir $OUT || exit 1
$JUDGE_PY $SVC/scripts/judge_preds.py --preds $OUT/preds.jsonl --out $OUT/judge.json --label ref_s5
echo "== DONE $SLURM_JOB_NAME $(date -u +%FT%TZ)"
