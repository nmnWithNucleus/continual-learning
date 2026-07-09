# HANDOFF — founders' working canvas (whole company)

> The single touch-point for the founders (CTO + AI co-founder) and the top of the
> escalation path. Read this first in any founders' session, then the aspect file you're
> working ([handoff/](handoff/)). Stable docs: [VISION.md](VISION.md) ·
> [ARCHITECTURE.md](ARCHITECTURE.md) · [ORG.md](ORG.md) · [PROMPTS.md](PROMPTS.md).
> Service-level state lives in each service's own HANDOFF.md — this board links, not restates.

**Last updated:** 2026-07-09 · maintained across founders' sessions.

---

## Service status board

| Service | Status | Lead session | Canvas |
|---|---|---|---|
| Recording | chartered — **C1 frozen; awaiting M0 kickoff** (capture slice) | — | [canvas](services/recording/HANDOFF.md) |
| Data Processing | chartered — **C1/C2 frozen; awaiting M0 kickoff** (capture slice) | — | [canvas](services/data-processing/HANDOFF.md) |
| Storage | **v0.0 built + mock loop runs** (integrated E2E 2026-07-09) · next: `/raw` + `/context` (capture slice) | serve-loop WS-D | [canvas](services/storage/HANDOFF.md) |
| Input | **v0.0 built + mock loop runs** (integrated E2E 2026-07-09) | serve-loop WS-A | [canvas](services/input/HANDOFF.md) |
| Inference | **v0.0 live on real Qwen3-VL-32B** (vLLM TP=8 on node-7, verified E2E 2026-07-09) | serve-loop WS-B | [canvas](services/inference/HANDOFF.md) |
| Output | **v0.0 built + mock loop runs** (integrated E2E 2026-07-09) | serve-loop WS-C | [canvas](services/output/HANDOFF.md) |
| Continuum | chartered — awaiting kickoff | — | [canvas](services/continuum/HANDOFF.md) |
| Platform | **v0.0 bring-up shipped + run E2E** (ratified 2026-07-09) | serve-loop WS-E | [canvas](services/platform/HANDOFF.md) |

## Founders' aspect threads

| Aspect | File | State |
|---|---|---|
| Engineering | [handoff/engineering.md](handoff/engineering.md) | active — serve-loop MVP (v0.0) live on real Qwen3-VL-32B; **learn-loop capture slice sliced + C1/C2 frozen (2026-07-09)**; next: capture M0 fan-out |
| Research | [handoff/research.md](handoff/research.md) | seeded — first agenda: POC→continuum bridge, research agenda v1 |
| Design / UX | [handoff/design.md](handoff/design.md) | seeded |
| Hiring / Ops | [handoff/hiring-ops.md](handoff/hiring-ops.md) | seeded |

## Escalations (open items needing a founders' decision)

*None open.* Resolved items move to the Decisions log below.

## Decisions log (founders)

| # | Decision | Date | Recorded in |
|---|---|---|---|
| D1 | **Platform is a ratified service** (ninth node: infra/CI/security/privacy/cost). CTO to read the internals in detail later; scope accepted as-is | 2026-07-09 | [ARCHITECTURE.md](ARCHITECTURE.md) component table; this board |
| D2 | **Single-markdown doc protocol** — one stable CHARTER + one volatile HANDOFF per node; no parallel human/AI copies | 2026-07-09 | [ORG.md](ORG.md) §Documentation protocol |
| D3 | **Serve-loop first** — build the thin end-to-end backbone (input → QueryBuilder → inference on base model → output), then grow capture/storage/continuum around it | 2026-07-09 | [ARCHITECTURE.md](ARCHITECTURE.md) §Decisions; [handoff/engineering.md](handoff/engineering.md) |
| D4 | **Wearable is camera + mic only (no speaker)** — market bodycams lack speakers; drop the speaker requirement from the hardware pick | 2026-07-09 | [ARCHITECTURE.md](ARCHITECTURE.md) §Ownership splits; recording + output charters |
| D5 | **Mobile app ships in v0** as an interaction surface **and** the default speech-output sink (mobile → Bluetooth headphones/earbuds). Only mobile *screen capture* stays deferred | 2026-07-09 | [ARCHITECTURE.md](ARCHITECTURE.md) §Ownership splits + §Decisions; input + output charters |
| D6 | **Base model = Qwen3-VL-32B** (re-verify OCR on our own screen-capture data before locking) | 2026-07-09 | [ARCHITECTURE.md](ARCHITECTURE.md) §Decisions |
| D7 | **POCs are reference, not source** — production code is written fresh; POCs inform contracts/learnings only, no lift-and-shift | 2026-07-09 | [ARCHITECTURE.md](ARCHITECTURE.md) §Decisions; [ORG.md](ORG.md) §Conventions |
| D8 | **OCR decoupled from the BWM** — a specialist OCR-strong VLM transcribes on-screen text (+ frame location) in the data-processing pipeline; the text is woven into the description target, so BWM OCR quality never gates the product (retires the D6 caveat) | 2026-07-09 | [ARCHITECTURE.md](ARCHITECTURE.md) §Decisions; [data-processing charter](services/data-processing/CHARTER.md) |
| D9 | **Centralized observability** — every service exposes `/metrics` + owns a Grafana dashboard JSON; **Platform runs ONE shared Prometheus + Grafana** + standard exporters (node/dcgm/DB) and provisions the per-service dashboards. Both founders open one Grafana URL. Node/CPU graphs are placeholders until multi-node; app-latency/error/GPU matter today | 2026-07-09 | [ARCHITECTURE.md](ARCHITECTURE.md) §Observability; [STACK.md](STACK.md); [platform charter](services/platform/CHARTER.md); all service charters |
| D10 | **Learn-loop skeleton = computer mic → ASR → `/context`.** The first capture path end-to-end is audio-only: ASR (transcript + segment timestamps), **no diarization / no enrichment / no vision**. Reuses POC Phase-1 (faster-whisper). C1 + C2 v0 frozen accordingly | 2026-07-09 | [ARCHITECTURE.md](ARCHITECTURE.md) §Contracts (learn-loop block) + [contracts/](contracts/); [handoff/engineering.md](handoff/engineering.md) |
| D11 | **C1 is two legs + push delivery.** Blob leg: recording `PUT`s raw bytes to storage `/raw` **first**, storage mints an opaque `blob_ref` (idempotent on `chunk_id`); pinned as prose, not a new C-number. Envelope leg: recording **pushes** the C1 envelope to data-processing, **at-least-once, dedup on `chunk_id`**, ordering + gap-detection via dense zero-based `(stream_id, sequence)`, blob-first write invariant. Resolves data-processing OQ1 + recording's ingest OQ | 2026-07-09 | [ARCHITECTURE.md](ARCHITECTURE.md) §Contracts; [contracts/c1_raw_stream_envelope.v0.json](contracts/c1_raw_stream_envelope.v0.json); recording + data-processing charters |

## Current state (terse)

- 2026-07-08: `product/` structure stood up — vision/architecture/org/prompts written,
  all 8 services chartered with seeded canvases, contracts **C1–C11** pinned in
  [ARCHITECTURE.md](ARCHITECTURE.md). A two-critic review pass (seam consistency + narrative
  coverage, 22 findings) drove: three new contracts minted (C9 response stream, C10
  training-window read, C11 recent-context read), an §Ownership splits section deciding the
  contested seams (wearable device, deletion, consent, BWM custody, people registry,
  same-day context, `/raw` custody), and per-charter amendments. No implementation started
  anywhere. POCs (`poc/live_stream_stability`, `poc/recursive_finetuning_stability`,
  `poc/live_video_chat`) continue as continuum/inference research feeders.

- 2026-07-09: all five founding escalations resolved (Decisions log D1–D8). Device/output
  narrative reworked for no-speaker wearable + mobile-as-speech-sink; mobile app pulled into
  v0 scope; build order locked to serve-loop-first; BWM set to Qwen3-VL-32B with OCR decoupled
  into a data-processing specialist pass (D8). Serve-loop MVP slice (v0.0) drafted in the
  engineering thread. `product/` tree committed to git.

- 2026-07-09 (later): interface-freeze done (C3/C9/C4/C6 v0 locked in
  [ARCHITECTURE.md](ARCHITECTURE.md) §Contracts + [contracts/](contracts/)); WS A–E built their
  services; **integrator wired them and ran the mock loop end to end.** A turn typed at the
  computer surface (`:8081`) streams a base-*mock* answer in the C9 format and the C4 turn is
  persisted + re-readable by `session_id`/`turn_id`; C6 resolves to base. All suites green
  (storage 10 · inference 6 · input 19 · output 46 = **81 passed**). Deltas: output's
  `c9_reader.js` wired into the input surface; inference `run.sh` honors `HOST`/`PORT`; storage
  test-DB gitignored. **Real Qwen3-VL-32B (`vllm`) is scripted-but-unrun** (needs the a3mega
  node). Full result: [handoff/engineering.md](handoff/engineering.md) "Serve-loop MVP — v0.0
  build result"; run guide: [services/README.md](services/README.md). Committed (`f6805d1`).

- 2026-07-09 (later still): **v0.0 CLOSED on the real base model.** Qwen3-VL-32B-Instruct
  launched on vLLM TP=8 on node-7 (driver 580 / CUDA-13, `vllm-vlm` env, model already cached);
  flipped `MODEL_BACKEND=vllm` and drove a real turn end to end — genuine Qwen answer streamed in
  the C9 format, C4 persisted with the real `model_id`. `serve_vllm.sh` updated to the verified
  recipe. Detail: [handoff/engineering.md](handoff/engineering.md) "REAL model — v0.0 closed".

- 2026-07-09 (capture slice): **learn-loop MVP sliced + C1/C2 frozen.** Founders' engineering
  session sliced the barebones capture path **computer mic → ASR → `/context`** (D10) and froze
  **C1** (raw-stream envelope + delivery: push/at-least-once/dedup-on-`chunk_id`/dense-`(stream_id,
  sequence)`/blob-first; D11) and **C2** (processed record + `/raw` blob-ref; `record_id`
  deterministic on `(chunk_id, pipeline_version)`). Shapes in [ARCHITECTURE.md](ARCHITECTURE.md)
  §Contracts (learn-loop block) + machine-readable in [contracts/](contracts/)
  (`c1_raw_stream_envelope.v0.json`, `c2_processed_record.v0.json`), **adversarially stress-tested
  by a 5-lens critic pass before freeze** (13 findings → 10 verified byte-changing → 2 blockers +
  7 fixes applied). data-processing OQ1 + recording's ingest OQ resolved. No service code built —
  this session produced the slice + the frozen contracts; the M0 builds come next. Slice:
  [handoff/engineering.md](handoff/engineering.md) "Learn-loop MVP slice".

## Next

- ~~**Pick the next slice**~~ **DONE** — capture slice picked + sliced + C1/C2 frozen (2026-07-09,
  see Current state + engineering thread). **Now: the capture M0 fan-out** — storage M0 (`/raw`
  blob write+read + `/context` C2 write, extending the running `:8083` service) lands ahead, then
  recording M0 (mic → `/raw` PUT → C1 emit) + data-processing M0 (C1 → ASR → C2) fan out in
  parallel against the frozen C1/C2, then an integrator wires + runs one chunk end to end.
- ~~Deferred follow-up: vLLM upgrade~~ **DONE 2026-07-09** — serving stack upgraded to
  **vLLM 0.24.0 / CUDA-13 (cu13) + flashinfer** (`vllm-cu13` env), validated E2E; 0.19.1 kept as
  fallback. Whole serving fleet now on the latest, CUDA-13-native. See [STACK.md](STACK.md).
- **Now unblocked:** the D6 OCR spot-check on real screen-capture data (model is serving).
- CTO to read the Platform charter internals when time allows (D1).
- **Live now:** vLLM is up on node-7 (8× H100 pinned) and the surface answers at
  `http://localhost:8081` — tear down with `run_all.sh --stop` + kill the vLLM PID to free GPUs.
