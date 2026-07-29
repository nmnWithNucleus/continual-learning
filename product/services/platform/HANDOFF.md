# HANDOFF — Platform Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** MVP bring-up shipped + **run E2E by integrator** (`run_all.sh` built the venv, installed all four services, brought the mock loop up `/health`-gated, and drove a real turn 2026-07-09) · *Last updated:* 2026-07-09

**Last updated:** 2026-07-27 — see § Incoming below. This canvas was the oldest in the repo (review item **O-9**): it named neither the D9 observability backbone D15 assigned to platform, nor escalation *E-3(b)*, which names platform as an owner. Both are now recorded, so a cold start here no longer misses work already assigned to this service.

## Workstream index
| WS | What | Status | Working file | Owner session |
|---|---|---|---|---|
| WS-E | Serve-loop MVP bring-up (`deploy/run_all.sh` + env + README + self-test) | done (mock, self-tested) | [handoff/ws-platform-mvp.md](handoff/ws-platform-mvp.md) | platform WS-E |
| WS-E2 | Learn-loop capture bring-up (`deploy/run_learn.sh` + `learn.env` + `README-learn.md` + sample-WAV gen + self-test) | done (glue self-tested; real 3-svc loop is the integrator's) | (this canvas) | platform WS-E2 |
| — | *(charter M0–M4 workstreams open here as work begins)* | | | |

## Current state
- Charter written 2026-07-08.
- 2026-07-09 — WS-E: platform bring-up for the serve-loop MVP shipped under [`deploy/`](deploy/).
- `bash deploy/run_all.sh` builds one shared venv, installs each sibling's requirements, and
  starts **storage(8083) → inference(8010, mock) → output(8082) → input(8081)** in order,
  `/health`-gated, then prints `http://localhost:8081` + a checklist.
- `--stop` / `--status` / `--restart` supported; per-service logs in `deploy/logs/`. Control plane
  verified end-to-end by `deploy/selftest/run_selftest.sh` against stdlib fake services (10/10
  pass) — no sibling code or GPU needed.
- Real model path is a documented `MODEL_BACKEND=vllm` flip; scripted-but-unrun until the a3mega
  node.
- 2026-07-09 — WS-E2: **learn-loop capture bring-up** shipped under [`deploy/`](deploy/), parallel
  to (and non-breaking of) the serve-loop one.
- `bash deploy/run_learn.sh` builds a separate shared venv (`.venv-learn`), installs each
  sibling's requirements, and starts *storage(8083) → data-processing(8085, ASR_BACKEND=mock) →
  recording(8084)* in order, `/health`-gated, then prints a checklist.
- `--smoke` generates a synthetic sample WAV (`make_sample_wav.py`, stdlib only) and fires
  recording `/capture/run`, printing the returned record_ids (E2E assertion left to the
  integrator).
- `--stop`/`--status`/`--restart`/`--skip-install` supported; logs in
  `deploy/logs/learn-<svc>.log`. Config: `deploy/learn.env` (from `learn.env.example`); ports doc:
  `deploy/README-learn.md`. Control plane verified by `deploy/selftest/run_selftest_learn.sh`
  against stdlib fake siblings (*12/12 pass*) — ordered start, health gating, --status, --smoke
  (WAV gen + /capture/run + record_id parse), --stop.
- The *real 3-service loop is unrun here* (recording + data-processing + storage `/raw`+`/context`
  are parallel builds, charter-only at time of writing); the integrator wires + drives one chunk
  end to end.
- Serve-loop self-test still 10/10 (no regression).

## Incoming — assigned to platform but not yet started (recorded 2026-07-27, closing O-9)

Neither item below is new work invented here; both were assigned elsewhere and this canvas simply
never recorded them, which is exactly the cold-start failure [ORG.md](../../ORG.md) §Documentation
protocol exists to prevent.

| # | What | Source | State |
|---|---|---|---|
| **D9 backbone** | The **one shared Prometheus + Grafana** on node-7, scraping the `/metrics` every service now emits, provisioning each service's own dashboard JSON plus the standard node/dcgm/DB exporters. Both founders open one Grafana URL. | **D9** (2026-07-09) + *D15* (2026-07-19), which named it "the small parallel slice" | **not started.** The *emission* half shipped long ago (recording M6, DP M8), so this is the last hop before D9 closes end to end |
| **E-3(b)** | A **captioner VL endpoint distinct from the user-facing `:8000`**. DP's `VIDEO_VLM_URL` and inference's `VLLM_URL` default to the *same* Qwen3-VL-32B TP=8 instance, so DP's prefill bursts land in the same continuous batch as the assistant's decode steps. The failure mode is assistant TTFT, which no GPU-percent figure surfaces. | Escalation **E-3(b)**, [board](../../HANDOFF.md) §Escalations — owners *platform + inference* | **open founders' call.** It closes DP CHARTER OQ3 (GPU placement/contention), which this service's own [CHARTER.md](CHARTER.md) still lists as an unresolved *proposal* |

**Also true of this service today, and not previously written down:** the learn fleet runs under
`deploy/run_learn.sh` on node-7 (storage 8083 · data-processing 8085 · recording 8084), and **who
restarts a service is still an open ops question** — there is no supervisor config in-repo, which
data-processing's M7 review flagged and routed here. The 2026-07-27 fleet cutover was driven by hand
through `run_learn.sh --stop` / restart.

## Next
- Integrator: once the four sibling `run.sh` land, run `bash deploy/run_all.sh` for the real
  mock loop (browser turn → streamed base answer → persisted C4).
- Charter M0–M4 (allocation policy, security envelope, consent+deletion, observability+cost,
  CI/CD) remain the substantive platform build — WS-E is only the thin MVP bring-up glue.
