# Sourced by every Morpheus sbatch script. Environment only — no GPU placement.
#
# We never choose a card: SLURM sets CUDA_VISIBLE_DEVICES for the allocation and the
# allocated GPUs are renumbered from 0 inside the job, so every script is given
# `--device cuda:0` and app.config refuses to let MORPHEUS_DEVICE override it.
export HF_HOME=/home/ubuntu/.cache/huggingface
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export SPEED_CODE=/home/ubuntu/speed_lora/code
export VERTEX_PROJECT=poetic-avenue-438401-a7
SVC=/home/ubuntu/nmn/continual_learning/product/services/continuum
TRAIN_PY=/home/ubuntu/miniconda3/envs/speedlora/bin/python
JUDGE_PY=/home/ubuntu/miniconda3/envs/vllm23/bin/python
echo "== $SLURM_JOB_NAME job=$SLURM_JOB_ID node=$SLURMD_NODENAME gpus=$CUDA_VISIBLE_DEVICES $(date -u +%FT%TZ)"
