# HANDOFF — Platform Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** MVP bring-up shipped + **run E2E by integrator** (`run_all.sh` built the venv, installed all four services, brought the mock loop up `/health`-gated, and drove a real turn 2026-07-09) · **Last updated:** 2026-07-09

## Workstream index
| WS | What | Status | Working file | Owner session |
|---|---|---|---|---|
| WS-E | Serve-loop MVP bring-up (`deploy/run_all.sh` + env + README + self-test) | done (mock, self-tested) | [handoff/ws-platform-mvp.md](handoff/ws-platform-mvp.md) | platform WS-E |
| — | *(charter M0–M4 workstreams open here as work begins)* | | | |

## Current state
- Charter written 2026-07-08.
- 2026-07-09 — WS-E: platform bring-up for the serve-loop MVP shipped under
  [`deploy/`](deploy/). `bash deploy/run_all.sh` builds one shared venv, installs each
  sibling's requirements, and starts **storage(8083) → inference(8010, mock) → output(8082)
  → input(8081)** in order, `/health`-gated, then prints `http://localhost:8081` + a
  checklist. `--stop` / `--status` / `--restart` supported; per-service logs in `deploy/logs/`.
  Control plane verified end-to-end by `deploy/selftest/run_selftest.sh` against stdlib fake
  services (10/10 pass) — no sibling code or GPU needed. Real model path is a documented
  `MODEL_BACKEND=vllm` flip; scripted-but-unrun until the a3mega node.

## Next
- Integrator: once the four sibling `run.sh` land, run `bash deploy/run_all.sh` for the real
  mock loop (browser turn → streamed base answer → persisted C4).
- Charter M0–M4 (allocation policy, security envelope, consent+deletion, observability+cost,
  CI/CD) remain the substantive platform build — WS-E is only the thin MVP bring-up glue.
