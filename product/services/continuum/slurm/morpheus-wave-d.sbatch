#!/bin/bash
#SBATCH --job-name=morpheus-wave-d
#SBATCH --partition=a3mega
#SBATCH --nodes=1
#SBATCH --gres=gpu:8
#SBATCH --exclusive
#SBATCH --time=14:00:00
#SBATCH --output=/home/ubuntu/nmn/continual_learning/product/services/continuum/var/slurm/logs/%x_%j.out
#
# Wave D: the two highest-value remaining PARITY claims, plus ensemble depth.
set -uo pipefail
source /home/ubuntu/nmn/continual_learning/product/services/continuum/slurm/_common.sh
cd $SVC
LOGS=var/slurm/logs/wave-d-$SLURM_JOB_ID
mkdir -p $LOGS var/diag/seqchains var/diag/ourchains

# --- SEQ ARM (rehearsal OFF): reproducing a FAILURE. Every parity result so far
#     shows we agree with GOOD runs; none shows we would have caught a bad one.
#     The reference's seq arm collapses (day-5 -> 0.00, retention 0.131).
for s in 0 1; do
(
  export CUDA_VISIBLE_DEVICES=$s
  $TRAIN_PY scripts/morpheus_chain.py --seed $s --device cuda:0 \
      --recipe recipes/consolidation-seq-v1.0.json \
      --base-model Qwen/Qwen3-VL-8B-Instruct --out var/diag/seqchains/seq_s$s
  echo "TASK-EXIT $? seq-s$s"
) > $LOGS/seq-s$s.log 2>&1 &
done

# --- FULL DECAY MATRIX: our eval over every reference adapter snapshot, all 21
#     cells. Takes the eval-path proof from one validated cell to complete.
(
  export CUDA_VISIBLE_DEVICES=2
  O=var/diag/decaymatrix
  $TRAIN_PY scripts/decay_matrix_repro.py --device cuda:0 \
      --run-dir /home/ubuntu/engram/results/phased/repro_replay_f30 --out $O \
    && $JUDGE_PY scripts/judge_preds.py --preds $O/preds.jsonl --out $O/judge.json --label decaymatrix
  echo "TASK-EXIT $? decaymatrix"
) > $LOGS/decaymatrix.log 2>&1 &

# --- ensemble depth: our chains to n=8
gpu=3
for s in 5 6 7 8 9; do
(
  export CUDA_VISIBLE_DEVICES=$gpu
  $TRAIN_PY scripts/morpheus_chain.py --seed $s --device cuda:0 \
      --base-model Qwen/Qwen3-VL-8B-Instruct --out var/diag/ourchains/morpheus_s$s
  echo "TASK-EXIT $? chain-s$s"
) > $LOGS/chain-s$s.log 2>&1 &
  gpu=$((gpu+1))
done

wait
echo "== WAVE D DRAINED $(date -u +%FT%TZ)"; grep -h TASK-EXIT $LOGS/*.log
