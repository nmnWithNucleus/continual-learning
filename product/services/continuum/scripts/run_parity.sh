#!/usr/bin/env bash
# Run the parity harness in both tiers.
#
# Tier A needs nothing but the service venv — render_block, the amplification
# plan, the scorers, and the judge aggregation are all differenced against the
# goldens with no ML stack at all.
#
# Tier B needs a tokenizer and peft, which live in the PINNED train env. Rather
# than install pytest into a shared conda env, we build a venv that inherits its
# site-packages: the same interpreter's libraries, nothing mutated. This is what
# a container image would do.
set -euo pipefail
cd "$(dirname "$0")/.."

TRAIN_PYTHON="${MORPHEUS_TRAIN_PYTHON:-/home/ubuntu/miniconda3/envs/speedlora/bin/python}"

[ -d .venv ] || { python3 -m venv .venv; ./.venv/bin/pip install -q -r requirements.txt; }
if [ ! -d .venv-train ]; then
  "$TRAIN_PYTHON" -m venv --system-site-packages .venv-train
  ./.venv-train/bin/pip install -q pytest
fi

echo "== tier A (no ML stack)"
./.venv/bin/python -m pytest -q

echo
echo "== tier B (pinned train env: tokenizer + peft parity)"
HF_HOME="${HF_HOME:-/home/ubuntu/.cache/huggingface}" \
  ./.venv-train/bin/python -m pytest -q tests/parity
