#!/bin/bash
#SBATCH --job-name=phase3-bridge
#SBATCH --partition=a3mega
#SBATCH --nodes=1
#SBATCH --gres=gpu:8
#SBATCH --exclusive
#SBATCH --cpus-per-task=200
#SBATCH --time=08:00:00
#SBATCH --output=/home/ubuntu/nmn/continual_learning/product/services/continuum/var/slurm/logs/%x_%j.out
#
# PHASE 3a — the bridge. Speed's tour days through the REAL services:
#   recording (replay ChunkSource) -> data-processing (faster-whisper + pyannote +
#   injected-caption sidecar) -> storage /raw + /context.
#
# One node, eight GPUs, one storage: the partition is OverSubscribe=EXCLUSIVE, so a job
# gets the whole node anyway, and ASR is the only thing that scales with cards. Each GPU
# runs its OWN data-processing instance (one worker, one card) with a fixed slot of days
# behind it — the same in-allocation placement the Phase-2 wave used, not hand-placement:
# no other tenant is on this node and each instance still sees its card as index 0.
#
# Storage state goes on the node's LOCAL SSD, deliberately: measured on the NFS home,
# 8-way concurrent C2 writes take sqlite's lock 2% of the time (HTTP 500) and single-
# thread throughput drops 27x. The DB is copied back to NFS at the end, which is all 3b
# needs (it reads /context; it never touches the 24 GB of blobs).
#
#   sbatch slurm/phase3-bridge.sbatch                       # all nine days
#   PHASE3_DAYS=5 PHASE3_LIMIT=2 sbatch slurm/phase3-bridge.sbatch   # smoke: 2 chunks
set -uo pipefail
source /home/ubuntu/nmn/continual_learning/product/services/continuum/slurm/_common.sh

PY=${PHASE3_PYTHON:-/home/ubuntu/miniconda3/envs/moe/bin/python}
SERVICES=/home/ubuntu/nmn/continual_learning/product/services
PLAN=${PHASE3_PLAN:-/home/ubuntu/phase3/plan}
LOCAL=${PHASE3_LOCAL:-/mnt/localssd/phase3}
KEEP=${PHASE3_KEEP:-/home/ubuntu/phase3}
LIMIT=${PHASE3_LIMIT:-0}                 # >0 -> replay only the first N chunks of a day
GPUS=${PHASE3_GPUS:-8}
LOGS=$SVC/var/slurm/logs/phase3-bridge-$SLURM_JOB_ID
mkdir -p $LOGS $KEEP/plan $KEEP/reports

# A replay is NOT idempotent: recording mints a fresh ULID chunk_id per run (capturer.py),
# so a second attempt writes a disjoint record set into the same window and the day-log
# double-counts it -- observed, not theorised (a 2-chunk smoke left 40 captions sharing 20
# segments behind). Every bridge run therefore starts from an empty store unless an
# operator explicitly resumes one.
if [ "${PHASE3_RESET:-1}" = "1" ]; then
  echo "== reset: wiping $LOCAL"; rm -rf $LOCAL/storage $LOCAL/dp
fi
mkdir -p $LOCAL/storage/raw

# Slots: one data-processing instance per GPU, with a fixed day list behind it. The two
# smallest days share slot 0 so the longest slot is the 79-chunk day, not 79+58.
SLOT_DAYS=${PHASE3_SLOT_DAYS:-"21,12|5|16|9|6|13|28|17"}
[ -n "${PHASE3_DAYS:-}" ] && SLOT_DAYS="$PHASE3_DAYS"

echo "== plan=$PLAN local=$LOCAL limit=$LIMIT slots=$SLOT_DAYS"

# --- storage ---------------------------------------------------------------------
(
  cd $SERVICES/storage
  export STORAGE_DB_PATH=$LOCAL/storage/context.db STORAGE_RAW_DIR=$LOCAL/storage/raw
  exec $PY -m uvicorn app.main:app --host 127.0.0.1 --port 8083
) > $LOGS/storage.log 2>&1 &
STORAGE_PID=$!

wait_health() {  # url, name
  for _ in $(seq 1 120); do
    curl -sf "$1/health" >/dev/null && { echo "   $2 up"; return 0; }
    sleep 1
  done
  echo "   $2 NEVER CAME UP"; return 1
}
wait_health http://127.0.0.1:8083 storage || { kill $STORAGE_PID; exit 1; }

# --- one data-processing per GPU ---------------------------------------------------
# INGEST_ASYNC=0 (inline): recording then blocks per chunk, which is the back-pressure
# that keeps exactly one 20-min chunk per card in flight and makes the session summary
# the honest per-day receipt. INGEST_WORKERS/limits are moot inline, but the modality
# cap is set anyway so a stray async flip cannot put two chunks on one model.
DP_PIDS=()
for i in $(seq 0 $((GPUS-1))); do
(
  cd $SERVICES/data-processing
  export CUDA_VISIBLE_DEVICES=$i
  export STORAGE_URL=http://127.0.0.1:8083
  export DP_VAR_DIR=$LOCAL/dp/$i
  # large-v3, not base: measured at the SAME wall clock on a 1200 s track (22.7 s vs
  # 23.3 s -- 50x realtime either way, the model is not the bottleneck) while base's
  # output is visibly degraded. Arm 2 is a stage validation, so it runs the backend a
  # real deployment would.
  export ASR_BACKEND=faster_whisper ASR_MODEL=large-v3 ASR_DEVICE=cuda \
         ASR_COMPUTE_TYPE=float16 ASR_BEAM_SIZE=1 ASR_LANGUAGE=en ASR_VAD=1
  export DIARIZE_BACKEND=pyannote
  export TRANSLATE_BACKEND=off ACOUSTIC_BACKEND=off
  export INJECT_CAPTION_BACKEND=index INJECT_CAPTION_INDEX=$PLAN/captions.jsonl
  export INGEST_ASYNC=0 INGEST_MODALITY_LIMITS=audio=1
  export VERIFY_BLOB_SHA256=1
  mkdir -p $DP_VAR_DIR
  exec $PY -m uvicorn app.main:app --host 127.0.0.1 --port $((8085+i))
) > $LOGS/dp-$i.log 2>&1 &
  DP_PIDS+=($!)
done
for i in $(seq 0 $((GPUS-1))); do
  wait_health http://127.0.0.1:$((8085+i)) "data-processing[$i]" || true
done

shutdown() {
  echo "== stopping services"
  kill "${DP_PIDS[@]}" $STORAGE_PID 2>/dev/null
  wait "${DP_PIDS[@]}" $STORAGE_PID 2>/dev/null
}
trap shutdown EXIT

# --- replay: one slot per GPU, days sequential within a slot ------------------------
# Collect the lane PIDs and wait on THOSE: a bare `wait` would also wait on storage and
# the eight data-processing instances, which are services and never exit.
REPLAY_PIDS=()
slot=0
for days in ${SLOT_DAYS//|/ }; do
  port=$((8085 + slot % GPUS))
(
  cd $SERVICES/recording
  export RECORDING_HTTP_TIMEOUT=1800 RECORDING_RETRY_ATTEMPTS=2 RECORDING_RETRY_BACKOFF=2
  export FFMPEG_BIN=${FFMPEG_BIN:-/home/ubuntu/miniconda3/envs/moe/bin/ffmpeg}
  for day in ${days//,/ }; do
    src=$PLAN/day$day.replay.jsonl
    if [ "$LIMIT" -gt 0 ]; then
      src=$LOGS/day$day.limit$LIMIT.jsonl; head -n $LIMIT $PLAN/day$day.replay.jsonl > $src
    fi
    echo "-- day $day -> :$port ($(wc -l < $src) chunks) $(date -u +%FT%TZ)"
    $PY -m app.cli --modality replay --source $src \
        --storage-url http://127.0.0.1:8083 --dp-url http://127.0.0.1:$port \
        --user-id replay-speed --device-id replay-tour-audio
    echo "TASK-EXIT $? day-$day"
  done
) > $LOGS/replay-slot$slot.log 2>&1 &
  REPLAY_PIDS+=($!)
  slot=$((slot+1))
done
wait "${REPLAY_PIDS[@]}"
echo "== REPLAY DRAINED $(date -u +%FT%TZ)"; grep -h TASK-EXIT $LOGS/*.log

# --- exit criterion: real C2 queryable by (user, window) ---------------------------
cd $SVC
DAYS_CSV=$(echo "$SLOT_DAYS" | tr '|' ',')
$PY scripts/phase3_verify.py --plan $PLAN/plan.json --days "$DAYS_CSV" \
    --out $KEEP/reports/bridge-$SLURM_JOB_ID.json | tee $LOGS/verify.log
VERIFY=$?

# 3b reads /context and nothing else, so only the DB travels back to NFS.
cp $LOCAL/storage/context.db $KEEP/context.db && echo "== context.db -> $KEEP/context.db"
du -sh $LOCAL/storage/raw
echo "== DONE $SLURM_JOB_NAME verify=$VERIFY $(date -u +%FT%TZ)"
