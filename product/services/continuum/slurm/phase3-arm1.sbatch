#!/bin/bash
#SBATCH --job-name=phase3-arm1
#SBATCH --partition=a3mega
#SBATCH --nodes=1
#SBATCH --gres=gpu:8
#SBATCH --exclusive
#SBATCH --cpus-per-task=64
#SBATCH --time=12:00:00
#SBATCH --output=/home/ubuntu/nmn/continual_learning/product/services/continuum/var/slurm/logs/%x_%j.out
#
# PHASE 3b — ARM 1, the measurement. The bridge's real C2 becomes the number:
#
#   /context  ->  day-log (caption-only, train-split, rule-bent 60s/5)
#             ->  amplify 48x  ->  6-night chain x N seeds  ->  judge  ->  the table
#
# Ordered by what can go wrong cheapest first. The ok-rate SLICE runs before anything
# expensive: the product day-log's blocks carry no tour anchor in their text (the
# amplify prompt supplies the day and city instead), and SpeedProfile.is_valid demands
# the day number back in the generated prose — so if that combination does not hold, it
# has to fail in two minutes, not four GPU-hours in.
#
#   sbatch --dependency=afterok:<bridge job> slurm/phase3-arm1.sbatch
set -uo pipefail
source /home/ubuntu/nmn/continual_learning/product/services/continuum/slurm/_common.sh

SERVE_PY=${PHASE3_PYTHON:-/home/ubuntu/miniconda3/envs/moe/bin/python}
AMPLIFY_PY=$JUDGE_PY                       # vLLM env; the trainer's cannot hold it
SERVICES=/home/ubuntu/nmn/continual_learning/product/services
KEEP=${PHASE3_KEEP:-/home/ubuntu/phase3}
LOCAL=${PHASE3_LOCAL:-/mnt/localssd/phase3}
BASE=${PHASE3_BASE_MODEL:-Qwen/Qwen3-VL-8B-Instruct}
RECIPE=${PHASE3_RECIPE:-consolidation-test-1min-v1.0}
DAYS=${PHASE3_TRAIN_DAYS:-5,9,12,13,17,21}
# Five seeds, not one: the baseline's own separation has sd 0.058 across ten chains,
# so a single draw cannot tell a surviving signal from a collapsed one. Five fit the
# node we already hold exclusively, at no extra wall-clock.
SEEDS=${PHASE3_SEEDS:-"0 1 2 3 4"}
LOGS=$SVC/var/slurm/logs/phase3-arm1-$SLURM_JOB_ID
mkdir -p $LOGS $KEEP/reports $LOCAL/arm1
cd $SVC
export CONTINUUM_RECIPE_ID=$RECIPE

# --- 1. day-log: continuum's own client over the REAL /context ----------------------
# Storage serves the bridge's DB; only the metadata travels (3b never reads a blob).
cp $KEEP/context.db $LOCAL/arm1/context.db || exit 1
(
  cd $SERVICES/storage
  export STORAGE_DB_PATH=$LOCAL/arm1/context.db STORAGE_RAW_DIR=$LOCAL/arm1/raw
  exec $SERVE_PY -m uvicorn app.main:app --host 127.0.0.1 --port 8093
) > $LOGS/storage.log 2>&1 &
STORAGE_PID=$!
for _ in $(seq 1 60); do curl -sf http://127.0.0.1:8093/health >/dev/null && break; sleep 1; done

# Re-check the bridge's own exit criterion against the DB this job will actually read.
# The dependency only says the bridge process exited; this says its DATA is complete, and
# a day-log silently missing a lane's captions is exactly the failure that would still
# produce a plausible-looking number four GPU-hours later.
$SERVE_PY scripts/phase3_verify.py --plan $KEEP/plan/plan.json --storage-url http://127.0.0.1:8093 \
    --days $DAYS --out $KEEP/reports/arm1-input.json 2>&1 | tee $LOGS/verify-input.log
if [ "${PIPESTATUS[0]}" != "0" ] && [ "${PHASE3_FORCE:-0}" != "1" ]; then
  echo "== BRIDGE DATA INCOMPLETE for $DAYS — refusing to measure (PHASE3_FORCE=1 overrides)"
  kill $STORAGE_PID 2>/dev/null; exit 1
fi

$SERVE_PY scripts/phase3_daylog.py --days $DAYS --storage-url http://127.0.0.1:8093 \
    --plan $KEEP/plan/plan.json \
    --out $KEEP/daylog --report $KEEP/reports/daylog.json 2>&1 | tee $LOGS/daylog.log
DAYLOG_RC=${PIPESTATUS[0]}
kill $STORAGE_PID 2>/dev/null
[ "$DAYLOG_RC" = "0" ] || { echo "== day-log build FAILED"; exit 1; }

# --- 2. the ok-rate slice (two minutes that protect four GPU-hours) -----------------
$AMPLIFY_PY scripts/phase3_amplify.py --days 5 --limit-blocks 10 --device cuda:0 \
    --base-model $BASE --daylog $KEEP/daylog \
    --report $KEEP/reports/amplify-slice.json 2>&1 | tee $LOGS/amplify-slice.log
[ "${PIPESTATUS[0]}" = "0" ] || { echo "== SLICE FAILED (ok-rate gate) — stopping"; exit 1; }

# --- 3. amplify the six nights (one engine load, six days) -------------------------
$AMPLIFY_PY scripts/phase3_amplify.py --days $DAYS --device cuda:0 --base-model $BASE \
    --daylog $KEEP/daylog --out $KEEP/corpus --plan $KEEP/plan/plan.json \
    --report $KEEP/reports/amplify.json 2>&1 | tee $LOGS/amplify.log
[ "${PIPESTATUS[0]}" = "0" ] || { echo "== AMPLIFY FAILED"; exit 1; }

# --- 4. the chains: one seed per GPU ------------------------------------------------
# One seed cannot separate a surviving signal (baseline sd 0.058) from a collapsed one.
# --corpus-pattern is passed EXPLICITLY: its default points at the Phase-2 5-min
# corpora, and silently training on those would make the whole dogfood measure nothing.
# Chain outputs are cleared first and their exit codes are GATED: the report globs
# var/phase3/arm1_s*, so a stale chain from an earlier attempt would otherwise be averaged
# into this run's table, and a chain that died after writing judge.json would look finished.
gpu=0
CHAIN_PIDS=()
for s in $SEEDS; do
  rm -rf $SVC/var/phase3/arm1_s$s
(
  export CUDA_VISIBLE_DEVICES=$gpu
  $TRAIN_PY scripts/morpheus_chain.py --seed $s --device cuda:0 --base-model $BASE \
      --days $DAYS --recipe $SVC/recipes/$RECIPE.json \
      --corpus-pattern $KEEP/corpus/day{day}.x48neg.corpus.txt \
      --blocks-pattern $KEEP/daylog/day{day}/blocks.jsonl \
      --out $SVC/var/phase3/arm1_s$s
  echo "TASK-EXIT $? chain-s$s"
) > $LOGS/chain-s$s.log 2>&1 &
  CHAIN_PIDS+=($!)
  gpu=$((gpu+1))
done
wait "${CHAIN_PIDS[@]}"
echo "== CHAINS DRAINED $(date -u +%FT%TZ)"; grep -h TASK-EXIT $LOGS/chain-*.log
FAILED=$(grep -h TASK-EXIT $LOGS/chain-*.log | grep -cv "TASK-EXIT 0 ")
if [ "$FAILED" != "0" ]; then
  echo "== $FAILED chain(s) FAILED — the table would be an average over survivors"
  [ "${PHASE3_FORCE:-0}" = "1" ] || exit 1
fi

# --- 5. the Arm-1 table -------------------------------------------------------------
$TRAIN_PY scripts/phase3_report.py --runs "var/phase3/arm1_s*" --days $DAYS \
    --control /home/ubuntu/engram/results/phased/seq \
              /home/ubuntu/engram/results/phased/seq_s1 \
              /home/ubuntu/engram/results/phased/seq_s2 \
    --out $KEEP/reports/arm1.json 2>&1 | tee $LOGS/report.log
echo "== DONE $SLURM_JOB_NAME $(date -u +%FT%TZ)"
