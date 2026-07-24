#!/bin/bash
#SBATCH --job-name=morpheus-wave-a
#SBATCH --partition=a3mega
#SBATCH --nodes=1
#SBATCH --gres=gpu:8
#SBATCH --exclusive
#SBATCH --time=14:00:00
#SBATCH --output=/home/ubuntu/nmn/continual_learning/product/services/continuum/var/slurm/logs/%x_%j.out
#
# ONE job, one node, eight GPUs. The partition is OverSubscribe=EXCLUSIVE, so a
# job gets the whole node whatever it asks for: submitting seven 1-GPU jobs would
# have serialised five of them behind two while each running job left 6-7 GPUs
# idle. Packing the wave into one allocation is what actually uses the node.
#
# Placement inside the allocation is by CUDA_VISIBLE_DEVICES over the GPUs SLURM
# gave us. That is not the hand-placement the exec model forbids — no other tenant
# can be on this node — and every task still sees its card as relative index 0,
# which is what app.config enforces.
set -uo pipefail
source /home/ubuntu/nmn/continual_learning/product/services/continuum/slurm/_common.sh
cd $SVC
LOGS=var/slurm/logs/wave-a-$SLURM_JOB_ID
mkdir -p $LOGS var/diag/refchains var/diag/ourchains
REF=/home/ubuntu/nmn/cl-research/research/engram/code

# --- 32B end-to-end M0 on GPUs 0+1 (32B does not fit one card at any batch size)
(
  export CUDA_VISIBLE_DEVICES=0,1 MORPHEUS_SHARD_GPUS=2 MORPHEUS_SHARD_MAX_MEMORY=76GiB
  O=var/diag/m0_32b
  $TRAIN_PY scripts/m0_smoke.py --day 5 --device cuda:0 \
      --base-model Qwen/Qwen3-VL-32B-Instruct --out $O --skip-vllm \
    && $JUDGE_PY scripts/vllm_load_check.py --adapter $O/adapter \
      --base-model Qwen/Qwen3-VL-32B-Instruct --gpu-mem-util 0.90 --max-model-len 2048 \
      --out $O/vllm_load.json \
    && $JUDGE_PY scripts/judge_preds.py --preds $O/preds.jsonl --out $O/judge.json --label m0-32b
  echo "TASK-EXIT $? m0-32b"
) > $LOGS/m0-32b.log 2>&1 &

# --- four more REFERENCE chains: their low tail is unmeasured at n=4
gpu=2
for s in 4 5 6 7; do
(
  export CUDA_VISIBLE_DEVICES=$gpu
  O=$SVC/var/diag/refchains/ref_s$s; mkdir -p $O
  cd $REF && $TRAIN_PY phase_d_driver.py --arm replay --replay-frac 0.30 \
      --days 5,9,12,13,17,21 --seed $s --model Qwen/Qwen3-VL-8B-Instruct --out-dir $O \
    && $JUDGE_PY $SVC/scripts/judge_preds.py --preds $O/preds.jsonl --out $O/judge.json --label ref_s$s
  echo "TASK-EXIT $? ref-s$s"
) > $LOGS/ref-s$s.log 2>&1 &
  gpu=$((gpu+1))
done

# --- two more of OUR chains -> ensemble n=5
for s in 3 4; do
(
  export CUDA_VISIBLE_DEVICES=$gpu
  $TRAIN_PY scripts/morpheus_chain.py --seed $s --device cuda:0 \
      --base-model Qwen/Qwen3-VL-8B-Instruct --out var/diag/ourchains/morpheus_s$s
  echo "TASK-EXIT $? chain-s$s"
) > $LOGS/chain-s$s.log 2>&1 &
  gpu=$((gpu+1))
done

wait
echo "== WAVE A DRAINED $(date -u +%FT%TZ)"; grep -h TASK-EXIT $LOGS/*.log
