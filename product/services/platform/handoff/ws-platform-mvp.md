# WS-E platform — serve-loop MVP bring-up (worklog)

House style: Goal / Done / In flight / Next / Gotchas. Working record for the thin
platform bring-up of the serve-loop MVP (v0.0). Charter: [../CHARTER.md](../CHARTER.md).

## Goal
The glue that runs the text-only walking skeleton on one box: build a shared venv, install
each sibling's deps, start **storage(8083) → inference(8010, MODEL_BACKEND=mock) →
output(8082) → input(8081)** in order (each `/health`-gated), print `http://localhost:8081`
+ a checklist, and support `--stop` / `--status`. Infra glue only — no app logic.

## Done
- `deploy/run_all.sh` — the bring-up. One shared venv at `deploy/.venv`; installs each
  service's `requirements.txt`; ordered `/health`-gated start; per-service logs to
  `deploy/logs/<svc>.log`; PIDs in `deploy/run/<svc>.pid`. Commands: default (up), `--stop`,
  `--status`, `--restart`, `--skip-install`, `--help`. Launches each sibling via its own
  `run.sh` (contract below); falls back to `uvicorn app.main:app` if a `run.sh` is absent.
- `deploy/.env.example` — `MODEL_BACKEND=mock`, `MODEL_ID`, `HOST`, the 4 ports + `VLLM_PORT`,
  the inter-service `*_URL`s, `PYTHON_BIN`, `HEALTH_TIMEOUT`. Documents the `mock→vllm` flip
  and the deferred cloudflared HTTPS story. Everything falls back to built-in defaults, so
  `run_all.sh` runs with no `.env`.
- `deploy/README.md` — one-screen bring-up: prerequisites, `bash run_all.sh`, the streaming
  test-turn curl, the platform↔service contract, `mock→real`, deferred HTTPS, self-test.
- `deploy/selftest/` — control-plane smoke: stdlib-only fake services (`fake/_server.py` +
  a `run.sh` per role) and `run_selftest.sh`. Proves ordered start, `/health` gating,
  `--status`, a streamed **C9** turn (text + `U+001E` + JSON end frame validated), and clean
  `--stop`. No sibling code, no network, no GPU.
- `deploy/.gitignore` — keeps `.env`, `.venv/`, `logs/`, `run/`, self-test scratch, pycache
  out of git.

## Verified (this box, 2026-07-09)
- `bash selftest/run_selftest.sh` → **10/10 PASS** from a clean state (fresh venv build path
  included). Confirms: up exits 0; all four `/health` up; storage healthy-before-input
  ordering; `--status` reports ≥4 up; the streamed turn parses as a valid C9 body; `--stop`
  brings all ports down.
- `--help`, and `--status` with nothing running (reports all four `down`) — both correct.
- `bash -n` clean on all shell scripts; `py_compile` clean on `_server.py`.
- Env available: Python 3.12.12 (no 3.11 on box — auto-detect handles it), venv, pip (with
  network), curl, fuser/lsof/ss. `run_all.sh` targets 3.11 but accepts any 3.11+.

## In flight
- Nothing. WS-E scope (thin MVP bring-up) is complete and self-tested.

## Next
- **Integrator:** when the four sibling `run.sh` land, `bash deploy/run_all.sh` should bring
  the real mock loop up (browser turn → streamed base answer → persisted C4). No changes to
  this glue expected if siblings honour the contract below.
- **mock→real:** on a3mega, run `services/inference/serve_vllm.sh`, set `MODEL_BACKEND=vllm`
  (and `VLLM_URL` if remote) in `.env`, `bash run_all.sh --restart`. Scripted-but-unrun until
  the node — per the honesty rule, no real-model run is claimed.
- **Substantive platform work (charter M0–M4), later:** real CI/CD + environments,
  observability/metric+log sinks + per-user cost, secrets management, consent+deletion
  orchestration, and multi-node SLURM serving-vs-training allocation. WS-E is only the MVP
  bring-up; none of that is in this slice.

## Platform ↔ service contract (what each sibling `run.sh` must honour)
- read **`HOST`** and **`PORT`** from env and bind uvicorn to them;
- expose **`GET /health`** → HTTP 200 when ready;
- use the **active venv** already on `PATH` (do not create a private venv — `run_all.sh` owns it);
- **inference** also reads `MODEL_BACKEND`, `MODEL_ID`, `VLLM_URL`, `STORAGE_URL`;
- **input/output** read `INFERENCE_URL`, `STORAGE_URL`, `OUTPUT_URL` as needed.
Exported to every child: `HOST`, `MODEL_BACKEND`, `MODEL_ID`, `STORAGE_URL`, `INFERENCE_URL`,
`OUTPUT_URL`, `INPUT_URL`, `VLLM_URL`, the five `*_PORT`s, and per-service `PORT`.

## Gotchas / decisions
- **Siblings not built yet.** At write time `services/{storage,inference,output,input}/` hold
  only CHARTER/HANDOFF — no `run.sh`. So `run_all.sh` was written against the documented
  contract and validated with fake services; it cannot run the *real* mock loop until the
  siblings land. This is called out honestly rather than faked.
- **Launch via `run.sh`, not re-implementing services.** `run_all.sh` owns the venv + installs
  and delegates process start to each sibling's `run.sh`. If a service ships without one, the
  `uvicorn app.main:app` fallback keeps it booting.
- **Stop is belt-and-braces.** `--stop` kills the tracked pid + its children *and* frees the
  service port (`fuser`/`lsof`), so a `run.sh` that forked a child (pid ≠ the server) is still
  cleaned up. Stops in reverse start order.
- **Python 3.11 target, 3.12 on box.** Auto-detect prefers `python3.11` then `python3.12` then
  `python3`; override with `PYTHON_BIN`. venv is 3.12 here — fine (3.11+).
- **Fractional `sleep` + timeout via `awk`** to stay dependency-light and portable in the
  health-wait loop (no `bc`, no bash-only float math).
- **`.env` wins over ambient shell** for the config keys (operator knob), with built-in
  defaults filling any gap — so the script runs with or without a `.env`.
- **Testability hooks:** `SERVICES_ROOT`, `LOG_DIR`, `RUN_DIR`, `VENV_DIR`, `ENV_FILE`,
  `PLATFORM_SKIP_INSTALL`, `HEALTH_TIMEOUT` are all env-overridable — that is how the self-test
  isolates itself onto a private port set with fake services and no installs.
