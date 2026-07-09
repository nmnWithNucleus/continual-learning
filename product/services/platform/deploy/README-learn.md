# Platform bring-up — learn-loop capture MVP (M0)

Infra glue that runs the **capture skeleton** on one box: a continuous audio
source is carved into chunks, each blob lands in storage `/raw`, a **C1** envelope
is pushed to data-processing, which runs **ASR** and writes a **C2** record to
storage `/context`. This directory is **infra only** — no app logic. The product
lives in the three sibling services; `run_learn.sh` just starts them in order,
tracks them, and can trigger one capture run.

```
recording (8084) ──C1 envelope──►  data-processing (8085) ──C2──►  storage (8083)
   capturer +          push, at-least-once    ASR (mock|faster_whisper)   /context (C2 write/read)
   PUT blob ─────────────────────────────────────────────────────────►  /raw  (blob write/read)
```

Start/dependency order is the reverse of the data flow — storage first (both
peers write to it), then data-processing (recording pushes to it), then
recording:

```
storage (8083)  ──►  data-processing (8085, ASR_BACKEND=mock)  ──►  recording (8084)
```

Default ASR backend is **`mock`** (canned transcript + fake segments spanning the
chunk, **no GPU, no torch** — runs on any box). Contracts:
[`../../../contracts/c1_raw_stream_envelope.v0.json`](../../../contracts/c1_raw_stream_envelope.v0.json)
(C1) and [`../../../contracts/c2_processed_record.v0.json`](../../../contracts/c2_processed_record.v0.json)
(C2). Slice + status: [`../../../handoff/engineering.md`](../../../handoff/engineering.md)
§ "Learn-loop MVP slice".

This is **separate** from the serve loop (`run_all.sh` + `.env` + `README.md`):
different services, different env file (`learn.env`), different venv
(`.venv-learn`). Running one does not touch the other. Note `storage :8083` is
common to both loops, so only **one loop can be up at a time**.

## Ports (localhost dev, pinned)

| Service | Port | Role | State |
|---|---|---|---|
| storage | **8083** | `/raw` (blob write/read) + `/context` (C2 write/read) — plus the serve-loop `/sessions` + model dir | existing service, **extended** |
| recording | **8084** | continuous-source capturer + `POST /capture/run` (carve → `PUT /raw` → push C1) | new |
| data-processing | **8085** | `POST /ingest` (C1 receiver) → pull blob → ASR → `POST /context/records` | new |

Consistent with [STACK.md](../../../STACK.md) (serve-loop app ports: input 8081 ·
inference 8010 · output 8082 · storage 8083 · vLLM 8000). The learn loop adds
**recording 8084** and **data-processing 8085**; storage 8083 is shared.

## Prerequisites

- Python **3.11+** (any 3.11+; the box may have 3.12). `run_learn.sh` builds one
  shared venv under `deploy/.venv-learn` and installs each service's
  `requirements.txt` into it.
- `curl` (used for `/health` polling; a Python stdlib fallback covers boxes
  without it).
- **No GPU** for the default `mock` ASR backend.
- The three sibling services present under
  `product/services/{storage,data-processing,recording}/`, each with a `run.sh`
  that honours the platform↔service contract (below). recording and
  data-processing are built by parallel workstreams; until their `run.sh` land,
  bring-up stops at the first missing service (the self-test below proves the
  glue independently).

## Bring it up

```bash
cd product/services/platform/deploy
cp learn.env.example learn.env      # optional; defaults work as-is
bash run_learn.sh
```

`run_learn.sh`:
1. creates/activates the shared `.venv-learn` and installs each service's requirements,
2. starts **storage → data-processing → recording**, waiting on each `/health`
   before starting the next,
3. prints a checklist (how to drive a capture run + read a C2 back).

### Drive one capture run (smoke)

```bash
bash run_learn.sh --smoke
```

This generates a synthetic sample WAV (`deploy/sample/sample_audio.wav`, stdlib
`make_sample_wav.py` — 12 s → three 5 s chunks) if one is not present, POSTs
`{source, chunk_seconds, dp_url, storage_url}` to recording `/capture/run`, and
prints the returned **record_ids**. Or by hand:

```bash
curl -sS -X POST http://127.0.0.1:8084/capture/run \
     -H 'Content-Type: application/json' \
     -d '{"source":"/abs/path/sample_audio.wav","chunk_seconds":5,
          "dp_url":"http://127.0.0.1:8085","storage_url":"http://127.0.0.1:8083"}'
```

The **E2E assertion is the integrator's** — the trigger just fires the run and
surfaces the record_ids. Confirm a record persisted + re-read it:

```bash
curl -s http://127.0.0.1:8083/context/records/<record_id>
curl -s 'http://127.0.0.1:8083/context/records?user_id=<uid>&from=<t>&to=<t>'
```

Re-delivering the same `chunk_id` must be a no-op (no dup blob, no dup record) —
idempotency is the sibling services' contract, verified by the integrator.

## Manage the fleet

```bash
bash run_learn.sh --status        # per-service pid + /health
bash run_learn.sh --stop          # stop everything this script started (frees the ports)
bash run_learn.sh --restart       # stop, then bring up
bash run_learn.sh --skip-install  # bring up without re-running pip
bash run_learn.sh --help
```

Per-service logs: `deploy/logs/learn-<svc>.log` (pip output: `deploy/logs/pip-learn.log`).
PIDs are tracked in `deploy/run-learn/<svc>.pid`. `--stop` also frees the service
ports directly, so a service whose `run.sh` forked a child is still cleaned up.

## Configuration

All config is in `deploy/learn.env` (copy from `learn.env.example`). Keys:
`ASR_BACKEND`, `HOST`, the three ports (`STORAGE_PORT`, `DP_PORT`,
`RECORDING_PORT`), the inter-service `*_URL`s (`STORAGE_URL`, `DP_URL`,
`RECORDING_URL`), the smoke defaults (`SAMPLE_WAV`, `SAMPLE_SECONDS`,
`CHUNK_SECONDS`), `PYTHON_BIN`, `HEALTH_TIMEOUT`. Anything unset falls back to the
built-in defaults, so the script also runs with no `learn.env` at all.

## Platform ↔ service contract

`run_learn.sh` owns the venv and the installs; each sibling `run.sh` must:

- read **`HOST`** and **`PORT`** from the environment and bind uvicorn to them;
- expose **`GET /health`** returning HTTP 200 when ready to serve
  (data-processing's body advertises `{ok:true, asr_backend:...}`);
- use the **active venv** already on `PATH` (do not create a private venv);
- **data-processing** additionally reads `ASR_BACKEND`, `STORAGE_URL`;
- **recording** additionally reads `STORAGE_URL`, `DP_URL`.

If a service has no `run.sh` yet, `run_learn.sh` falls back to
`uvicorn app.main:app --host $HOST --port $PORT` so a conventional FastAPI layout
still boots.

## mock → real ASR (faster-whisper)

Only `mock` runs without the ASR stack. To run real ASR set
`ASR_BACKEND=faster_whisper` in `learn.env` and `bash run_learn.sh --restart`.
`faster_whisper` is lazy-imported by data-processing only when selected (mock
needs no torch/faster-whisper). CPU-capable but slow; GPU is an optimization.
Per the honesty rule, the `faster_whisper` path is scripted-but-unrun in this
workflow — only the `mock` loop is expected to run here.

## Self-test (control-plane smoke)

`deploy/selftest/run_selftest_learn.sh` exercises `run_learn.sh`'s orchestration
(skip-install, ordered start, `/health` gating, `--status`, `--smoke` →
sample-WAV generation + `/capture/run` + record_id extraction, `--stop`) against
**stdlib-only fake services** — no sibling code and no network needed. It proves
the bring-up glue independently of whether recording / data-processing / the
storage `/raw`+`/context` extension are built yet:

```bash
bash selftest/run_selftest_learn.sh
```

Last run here: **12 passed, 0 failed** (2026-07-09).

## Scope / not yet

Real CI/CD, observability sinks, secrets management, multi-node orchestration,
consent enforcement, and the HTTPS front door are **later platform work** (see the
service CHARTER M1–M4 + ARCHITECTURE §Observability). This deliverable is the thin
bring-up that makes the capture skeleton walk locally.
