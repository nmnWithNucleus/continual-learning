#!/usr/bin/env bash
# Capture the pinned environments as lockfiles, by ABSOLUTE INTERPRETER PATH.
#
# Morpheus never activates an environment, so neither does this: each lock is
# produced by asking a specific interpreter what it has. That is the same
# question the nightly job asks at preflight, so a lock that disagrees with a run
# is a real difference, not a shell artifact.
#
# `conda env export` is captured too where conda is available — pip freeze alone
# misses the CUDA/driver-adjacent conda packages that decide whether torch runs.
set -euo pipefail
cd "$(dirname "$0")/.."

TRAIN_PYTHON="${MORPHEUS_TRAIN_PYTHON:-/home/ubuntu/miniconda3/envs/speedlora/bin/python}"
JUDGE_PYTHON="${MORPHEUS_JUDGE_PYTHON:-/home/ubuntu/miniconda3/envs/vllm23/bin/python}"
CONDA="${CONDA_EXE:-/home/ubuntu/miniconda3/bin/conda}"
OUT=env
mkdir -p "$OUT"

capture() {
  local name=$1 interpreter=$2 conda_env=$3
  echo "== $name: $interpreter"
  "$interpreter" -c 'import sys; print(sys.version)' > "$OUT/$name.python.txt"
  "$interpreter" -m pip freeze > "$OUT/$name.pip.lock.txt"
  if [ -x "$CONDA" ]; then
    "$CONDA" env export -n "$conda_env" > "$OUT/$name.conda.yml" 2>/dev/null \
      || echo "  (conda export unavailable for $conda_env)"
  fi
  echo "  $(wc -l < "$OUT/$name.pip.lock.txt") pip packages"
}

capture train "$TRAIN_PYTHON" speedlora
capture judge "$JUDGE_PYTHON" vllm23

# The versions that actually decide reproducibility, in one greppable place.
{
  echo "# Morpheus environment fingerprint — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "train_interpreter: $TRAIN_PYTHON"
  echo "judge_interpreter: $JUDGE_PYTHON"
  "$TRAIN_PYTHON" - <<'PY'
import importlib.metadata as meta
for pkg in ("torch", "transformers", "peft", "accelerate", "safetensors"):
    try:
        print(f"{pkg}: {meta.version(pkg)}")
    except meta.PackageNotFoundError:
        print(f"{pkg}: MISSING")
PY
  "$JUDGE_PYTHON" - <<'PY'
import importlib.metadata as meta
for pkg in ("litellm", "google-cloud-aiplatform"):
    try:
        print(f"{pkg}: {meta.version(pkg)}")
    except meta.PackageNotFoundError:
        print(f"{pkg}: MISSING")
PY
  nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | head -1 | sed 's/^/gpu: /'
} > "$OUT/fingerprint.txt"

cat "$OUT/fingerprint.txt"
