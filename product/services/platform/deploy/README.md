# Platform bring-up — serve-loop MVP (v0.0)

Infra glue that runs the text-only walking skeleton on one box: a user types a
question at `http://localhost:8081`, gets a streamed base-model answer, and the
turn is persisted. This directory is **infra only** — no app logic. The product
lives in the four sibling services; `run_all.sh` just starts them in order and
tracks them.

```
storage (8083) ──► inference (8010, MODEL_BACKEND=mock) ──► output (8082) ──► input (8081)
   /sessions + model dir      /infer streams C9, writes C4        C9 relay          chat surface + QueryBuilder
```

## Prerequisites

- Python **3.11+** (any 3.11+; the box may have 3.12). `run_all.sh` builds one
  shared venv under `deploy/.venv` and installs each service's `requirements.txt`
  into it.
- `curl` (used for `/health` polling; a Python stdlib fallback covers boxes
  without it).
- **No GPU** for the default `mock` backend.
- The four sibling services present under `product/services/{storage,inference,output,input}/`,
  each with a `run.sh` that honours the platform↔service contract (below).

## Bring it up

```bash
cd product/services/platform/deploy
cp .env.example .env         # optional; defaults work as-is
bash run_all.sh
```

`run_all.sh`:
1. creates/activates the shared venv and installs each service's requirements,
2. starts **storage → inference → output → input**, waiting on each `/health`
   before starting the next,
3. prints the surface URL and a checklist.

Then open **http://localhost:8081**.

### Send a test turn (streamed)

The input service streams a **C9** body: answer text, then a single `U+001E`
separator byte, then one JSON end frame.

```bash
curl -N -X POST http://localhost:8081/api/turn \
     -H 'Content-Type: application/json' \
     -d '{"text":"hello, who are you?"}'
```

You should see the answer text stream in, then the end frame
`{"contract":"C9","version":"0","turn_id":...,"finished":true,...}`. The turn is
persisted in storage `/sessions` and re-readable by `session_id`/`turn_id`.

## Manage the fleet

```bash
bash run_all.sh --status      # per-service pid + /health
bash run_all.sh --stop        # stop everything this script started
bash run_all.sh --restart     # stop, then bring up
bash run_all.sh --skip-install  # bring up without re-running pip
bash run_all.sh --help
```

Per-service logs: `deploy/logs/<svc>.log` (pip output: `deploy/logs/pip.log`).
PIDs are tracked in `deploy/run/<svc>.pid`. `--stop` also frees the service
ports directly, so a service whose `run.sh` forked a child is still cleaned up.

## Configuration

All config is in `deploy/.env` (copy from `.env.example`). Keys: `MODEL_BACKEND`,
`MODEL_ID`, `HOST`, the four ports + `VLLM_PORT`, the inter-service `*_URL`s,
`PYTHON_BIN`, `HEALTH_TIMEOUT`. Anything unset falls back to the built-in
defaults, so the script also runs with no `.env` at all.

## Platform ↔ service contract

`run_all.sh` owns the venv and the installs; each sibling `run.sh` must:

- read **`HOST`** and **`PORT`** from the environment and bind uvicorn to them;
- expose **`GET /health`** returning HTTP 200 when ready to serve;
- use the **active venv** already on `PATH` (do not create a private venv);
- **inference** additionally reads `MODEL_BACKEND`, `MODEL_ID`, `VLLM_URL`,
  `STORAGE_URL`;
- **input/output** read `INFERENCE_URL`, `STORAGE_URL`, `OUTPUT_URL` as needed.

If a service has no `run.sh` yet, `run_all.sh` falls back to
`uvicorn app.main:app --host $HOST --port $PORT` so a conventional FastAPI
layout still boots.

## Going from mock → real (Qwen3-VL-32B on a3mega)

1. On the a3mega node, start the real model server:
   `bash product/services/inference/serve_vllm.sh` (vLLM, TP=8, one node, port 8000).
2. Set `MODEL_BACKEND=vllm` (and, if vLLM is on another host, `VLLM_URL`) in `.env`.
3. `bash run_all.sh --restart`.

Inference then proxies to the real model instead of the canned stream; the rest
of the loop is unchanged. Per the honesty rule, the `vllm` path is
scripted-but-unrun until the node is available — only the `mock` loop runs
without a GPU.

## Remote reach / HTTPS (deferred)

Not needed for local mock. When a phone or external tester needs to reach this
box, the intended path is a **cloudflared tunnel** at the input port:

```bash
cloudflared tunnel --url http://localhost:8081
```

That, plus a stable hostname and auth, is later platform work.

## Self-test (control-plane smoke)

`deploy/selftest/run_selftest.sh` exercises `run_all.sh`'s orchestration (venv
skip, ordered start, `/health` gating, `--status`, a streamed turn, `--stop`)
against **stdlib-only fake services** — no sibling code and no network needed. It
proves the bring-up glue independently of whether the real services are built:

```bash
bash selftest/run_selftest.sh
```

## Scope / not yet

Real CI/CD, observability sinks, secrets management, multi-node SLURM
orchestration, and the HTTPS front door are **later platform work** (see the
service CHARTER M1–M4). This deliverable is the thin bring-up that makes the
serve-loop walk locally.
