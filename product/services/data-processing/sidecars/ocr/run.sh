#!/usr/bin/env bash
# =============================================================================
# run.sh -- launch the co-located OCR sidecar service (WS-C / tab C1).
#
#   The OCR analogue of services/inference/serve_vllm.sh, and it mirrors that
#   posture: env-driven, loopback by default (127.0.0.1), one `exec`, fail loud
#   on a misconfigured real backend.
#
#   >>> CPU ONLY. No GPU. Co-located with data-processing on the same host; DP
#   >>> calls it over loopback HTTP per the frozen contract in README.md.
#
#   Two backends:
#     OCR_MODE=mock  (default) -- Python-stdlib only. No venv, no models, no
#                                 network, no new DP dependency. This is the
#                                 headless-CI backend. `./run.sh` just works.
#     OCR_MODE=ppocr           -- PP-OCR det+rec ONNX on CPU. Needs the sidecar
#                                 venv (.venv, in this sidecar dir). Set it up once:
#                                   python3 -m venv .venv
#                                   ./.venv/bin/pip install -r requirements.txt
#
#   Once up, point DP at it:  VIDEO_OCR_BACKEND=ppocr  VIDEO_OCR_URL=http://127.0.0.1:8091
#   and pin the two model sha256 from `curl 127.0.0.1:8091/health` into DP config
#   (asserted at graph resolution).
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- environment (defaults; override via env) --------------------------------
OCR_MODE="${OCR_MODE:-mock}"                       # mock | ppocr
OCR_HOST="${OCR_HOST:-127.0.0.1}"                  # loopback for the co-located loop
OCR_PORT="${OCR_PORT:-8091}"                       # frozen default; keep DP's VIDEO_OCR_URL in sync
OCR_EP="${OCR_EP:-CPUExecutionProvider}"           # reported at /health; folded into DP's version tag
OCR_THREADS="${OCR_THREADS:-4}"                    # ORT intra-op threads (measured op point: 4)
# Optional explicit model files (else the bundled PP-OCRv4 det/rec ONNX are used):
OCR_DET_MODEL="${OCR_DET_MODEL:-}"
OCR_REC_MODEL="${OCR_REC_MODEL:-}"
OCR_REC_DICT="${OCR_REC_DICT:-}"
export OCR_MODE OCR_HOST OCR_PORT OCR_EP OCR_THREADS OCR_DET_MODEL OCR_REC_MODEL OCR_REC_DICT

# --- pick the interpreter ----------------------------------------------------
# mock -> any python3 (stdlib only). ppocr -> the sidecar's own venv.
VENV_PY="$HERE/.venv/bin/python"
if [ "$OCR_MODE" = "mock" ]; then
  PYBIN="${OCR_PYTHON:-$(command -v python3)}"
elif [ "$OCR_MODE" = "ppocr" ]; then
  if [ ! -x "$VENV_PY" ]; then
    echo "[run.sh] OCR_MODE=ppocr but the sidecar venv is missing at $VENV_PY" >&2
    echo "[run.sh] create it:" >&2
    echo "           python3 -m venv $HERE/.venv" >&2
    echo "           $HERE/.venv/bin/pip install -r $HERE/requirements.txt" >&2
    exit 1
  fi
  PYBIN="${OCR_PYTHON:-$VENV_PY}"
else
  echo "[run.sh] unknown OCR_MODE=$OCR_MODE (expected 'mock' or 'ppocr')" >&2
  exit 1
fi

echo "[run.sh] mode=$OCR_MODE  ${OCR_HOST}:${OCR_PORT}  ep=$OCR_EP  threads=$OCR_THREADS"
echo "[run.sh] python=$PYBIN"

exec "$PYBIN" "$HERE/app.py"
