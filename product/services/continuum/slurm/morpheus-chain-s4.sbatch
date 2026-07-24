#!/bin/bash
#SBATCH --job-name=morpheus-chain-s4
#SBATCH --partition=a3mega
#SBATCH --nodes=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=16
#SBATCH --time=10:00:00
#SBATCH --output=/home/ubuntu/nmn/continual_learning/product/services/continuum/var/slurm/logs/%x_%j.out
# Our chain seed 4, taking the ensemble to n=5. Only LoRA init differs between our
# seeds: the rehearsal draw is seeded by a constant and is byte-identical across them.
set -uo pipefail
source /home/ubuntu/nmn/continual_learning/product/services/continuum/slurm/_common.sh
cd $SVC
$TRAIN_PY scripts/morpheus_chain.py --seed 4 --device cuda:0 \
  --base-model Qwen/Qwen3-VL-8B-Instruct --out var/diag/ourchains/morpheus_s4
echo "== DONE $SLURM_JOB_NAME $(date -u +%FT%TZ)"
