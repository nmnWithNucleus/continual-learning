# Nucleus v0 — Stack Registry

> The index of what we run on. **Exact version pins are NOT here** — they live once in each
> service's dependency manifest (the source of truth). This doc records the *shared* runtime
> decisions every service inherits, the serving stack, the cluster baseline, and where each
> service's lockfile lives. "One fact, one home": pins in the lockfile, decisions + index here.

**Last updated:** 2026-07-09 · Owner: Platform (backbone) + each service (its own manifest)

---

## Policy

- **Every service pins its dependencies** in `services/<key>/requirements.txt` (and, as we
  harden, a lockfile — `requirements.lock` / `uv.lock`). That file is authoritative for that
  service; this registry never restates its pins, only links them.
- **Shared baseline changes route through Platform** and get recorded in the table below, so a
  bump (e.g. FastAPI major) is a visible, single decision rather than silent drift across 8 repos.
- **This file is updated whenever the serving stack or a shared-baseline row changes.** A stale
  row here is a bug — fix it in the same change.

## Shared application baseline (all backend services)

| Concern | Choice | Notes |
|---|---|---|
| Language | **Python 3.11/3.12** | services target 3.11; the on-node envs are 3.12 |
| Web framework | **FastAPI + uvicorn** | one per backend service |
| HTTP client | **httpx** | inter-service calls |
| Models/validation | **pydantic** + **jsonschema** | pydantic models mirror the frozen `contracts/*.json`; jsonschema validates against them in tests |
| Tests | **pytest** | each service ships its own suite |
| Surfaces | static HTML/CSS/JS, **no build step** (v0) | served by input; a build step arrives with the real frontends |

## Model-serving stack (inference)

| Concern | Choice | Notes |
|---|---|---|
| Base model (BWM) | **Qwen/Qwen3-VL-32B-Instruct** (dense) | cached in the HF hub cache on node-7 (~63 GB) |
| Server | **vLLM**, OpenAI-compatible, TP=8 | see the per-env rows below |
| Serving env (**primary**) | conda **`vllm-cu13`** — vLLM **0.24.0**, torch **2.11.0**, transformers **5.13.0**, **CUDA-13 (cu13) wheels + flashinfer** | validated E2E 2026-07-09 on node-7 (driver 580); the current serving stack |
| Serving env (fallback) | conda **`vllm-vlm`** — vLLM **0.19.1**, torch 2.10/cu128, transformers 5.12.1 | the POC-proven stack that first closed v0.0; kept intact as the known-good fallback |
| Launch recipe | [`services/inference/serve_vllm.sh`](services/inference/serve_vllm.sh) | defaults to `vllm-cu13`; `VLLM_BIN=…/vllm-vlm/bin/vllm` to fall back |

## Observability endpoints & ports (pinned)

Decided 2026-07-09 (D9). See [ARCHITECTURE.md §Observability](ARCHITECTURE.md). Every service
exposes `/metrics`; Platform hosts the shared stack. Ports are the pinned convention (localhost
dev; the same names carry to real hosts later).

| Thing | Port / path | Owner |
|---|---|---|
| Grafana (the one dashboard UI both founders open) | **:3000** | Platform |
| Prometheus (scrape + store) | **:9090** | Platform |
| node_exporter (CPU/host) | :9100 | Platform |
| dcgm-exporter (GPU — for inference) | :9400 | Platform |
| Per-service metrics | `http://<service>:<its port>/metrics` | each service |
| Per-service dashboard JSON | `services/<key>/dashboards/*.json` | each service |

App service ports (from the serve-loop MVP): input 8081 · inference 8010 · output 8082 ·
storage 8083 · vLLM 8000. Each also serves `/metrics` on that same port.

## Cluster / hardware baseline

| Concern | Choice | Notes |
|---|---|---|
| Node | `nucla3m-a3meganodeset-7` (a3mega) | 8× H100 80 GB, SLURM partition `a3mega` |
| Driver / CUDA | **580.159.03 / CUDA-13** | upgraded cluster-wide; unblocks vLLM ≥0.20 (cu13 wheels) |
| Shared FS | `/home` NFS (source of truth for code) · `/mnt/localssd` per-node | HF cache currently on NFS |
| Env manager | conda (`/home/ubuntu/miniconda3`) | see `conda env list` for the full set |

## Per-service manifests (source of truth for pins)

| Service | Manifest | Runs |
|---|---|---|
| storage | [services/storage/requirements.txt](services/storage/requirements.txt) | FastAPI + SQLite (dev) |
| inference | [services/inference/requirements.txt](services/inference/requirements.txt) | FastAPI; vLLM client (env above) |
| input | [services/input/requirements.txt](services/input/requirements.txt) | FastAPI + static surface |
| output | [services/output/requirements.txt](services/output/requirements.txt) | FastAPI relay + browser C9 client |
| platform | [services/platform/deploy/](services/platform/deploy/) | bring-up scripts, venv, `.env.example` |
| recording · data-processing · continuum | *(not yet built — manifest lands with the service)* | — |

## Known hygiene follow-ups
- **Pin/lock exact versions** in each service's `requirements.txt` (they are currently loose
  ranges). Add a lockfile per service so a fresh venv is reproducible.
- **Consolidate the venv story**: `run_all.sh` builds one shared dev venv; when services become
  truly independent, each gets its own locked env (tracked with the microservice split).
