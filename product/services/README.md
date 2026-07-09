# Serve-loop MVP (v0.0) — how to run it

The text-only walking skeleton: a user types a question at the computer surface, gets a
streamed base-model answer, and the turn is persisted + re-readable. Five services:

```
input :8081  ──C3──►  inference :8010  ──C6 resolve / C4 write──►  storage :8083
 (surface +            (mock|vllm; streams C9,                     (/sessions + model dir)
  QueryBuilder)         writes C4)
      ▲                     │
      └────── C9 stream ─────┘   output :8082 owns the browser C9 reader (c9_reader.js,
      relayed to the browser     vendored into the input surface) + a standalone /deliver relay
```

Default backend is **`mock`** (canned streamed answer, **no GPU** — runs on any box).
Contracts: [`../contracts/*.json`](../contracts) (C3, C6, C9, C4). Status +
integration notes: [`../handoff/engineering.md`](../handoff/engineering.md).

## 1. Bring it up

```bash
cd product/services/platform/deploy
cp .env.example .env            # optional; defaults work as-is
bash run_all.sh                 # builds a shared venv, installs deps, starts all 4 in order
```

`run_all.sh` starts **storage → inference → output → input**, waiting on each `/health`,
then prints the surface URL. Manage the fleet:

```bash
bash run_all.sh --status        # per-service pid + /health
bash run_all.sh --stop          # stop everything it started (also frees the ports)
bash run_all.sh --restart
bash run_all.sh --skip-install  # bring up without re-running pip
```

Logs: `deploy/logs/<svc>.log`. Requires Python 3.11+ (3.12 fine) and network for the first
`pip install`. No GPU for `mock`.

## 2. Send a turn

Open **http://localhost:8081** in a browser and ask something — the answer streams in as
safe-rendered markdown. Or from the shell (the streamed body is the C9 wire format: answer
text, one `U+001E` separator byte, then a JSON end frame):

```bash
curl -N -X POST http://localhost:8081/api/turn \
     -H 'Content-Type: application/json' \
     -d '{"text":"What is 2+2?"}' -D -
```

The `X-Session-Id` / `X-Turn-Id` response headers carry the ids. Confirm the turn persisted:

```bash
curl -s http://localhost:8083/sessions/turns/<turn_id>          # the stored C4 record
curl -s http://localhost:8083/sessions/<session_id>/turns       # all turns in the session
```

## 3. mock → real (Qwen3-VL-32B on the a3mega node)

Only `mock` runs without a GPU. To serve the real base model:

1. On the a3mega node (8× H100, one node): `bash product/services/inference/serve_vllm.sh`
   — vLLM, TP=8, OpenAI-compatible server on `:8000`, text-only. Needs `HF_TOKEN`.
2. In `deploy/.env` set `MODEL_BACKEND=vllm` (and `VLLM_URL` if vLLM is on another host).
3. `bash run_all.sh --restart`.

Inference then proxies to the real model instead of the canned stream; the rest of the loop
is unchanged. This path is **scripted but unrun** until the GPU node is available — no
real-model run is claimed here.

## Ports

| Service | Port | Role |
|---|---|---|
| input | 8081 | chat surface + QueryBuilder (C3) — **the URL users open** |
| inference | 8010 | `/infer`: consume C3 → resolve C6 → generate → stream C9 → write C4 |
| output | 8082 | browser C9 reader (`c9_reader.js`) + standalone `/deliver` relay |
| storage | 8083 | `/sessions` (C4 write/read) + model directory (C6 resolve) |
| vLLM | 8000 | real base model — only when `MODEL_BACKEND=vllm` |

## Per-service tests

```bash
cd product/services/<svc> && python3 -m pytest    # storage / inference / input / output
```
Last integrator run (2026-07-09, mock): storage 10 · inference 6 · input 19 · output 46 =
**81 passed**.
