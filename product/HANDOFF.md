# HANDOFF — founders' board (whole company)

> **The board, not a log.** Where every service stands, what is escalated to the founders, and what
> happens next. It is **rewritten in place** every session — nothing is appended here, and no history
> accumulates ([ORG.md](ORG.md) §Documentation protocol).
>
> | Looking for | Go to |
> |---|---|
> | *What did we decide, and why?* | [DECISIONS.md](DECISIONS.md) — the numbered register |
> | *How did we get here?* | [handoff/&lt;aspect&gt;.md](handoff/) §Worklog — newest first |
> | *What is this system?* | [VISION.md](VISION.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [STACK.md](STACK.md) · [ORG.md](ORG.md) |
>
> Read this first in any founders' session, then the aspect file you're working. Service-level state
> lives in each service's own `HANDOFF.md` — this board links, it does not restate.

**Stage: PROTOTYPE** (D19) · **Last updated:** 2026-08-07

---

## Where we are today

- **Both loops run end to end on real infrastructure**, on real capture rather than in tests: real
  audio → faster-whisper → `/context`; a C12 profile set and a missing one 404'd; a nightly to
  `published` over HTTP; the watermark advancing only on `published`; exactly one active C5 row.
- **The learn fleet is live on node-7** — storage `:8083` · recording `:8084` · data-processing
  `:8085` · captioner `:8161` · eight model servers `:8121–8152`. `INGEST_ASYNC=1` is the operating
  default ([D16](DECISIONS.md)).
- **Data-processing writes one C2 record per chunk**, built from slots, with models running as
  supervised servers and storage serving the C10 v2 day-log.
- **Suites:** storage 354 · continuum 264 (+7 skipped) · recording 144 · data-processing 569
  (+4 skipped) · the four model-server suites 50.
- **Down by choice:** the serve loop (vLLM + app services). Relaunch `run_all.sh` +
  `services/inference/serve_vllm.sh` when needed.
- **Nothing is blocking.** The next phase is **client live-stream testing** — a real captured day
  end to end on real hardware. Every open §Next item is a follow-up, not a gate.

---

## Service status board

| Service | Status | Lead session | Canvas |
|---|---|---|---|
| Recording | `built` — capture M1 + computer surfaces, alpha-complete | computer-capture → M6 | [↓](#recording) · [canvas](services/recording/HANDOFF.md) |
| Data Processing | `built` — one record per chunk from slots, supervised model servers, async ingest | data-processing | [↓](#data-processing) · [canvas](services/data-processing/HANDOFF.md) |
| Storage | `built` — day-log custody, `created_at`/`updated_at`, C10 v2 | serve + learn; build slice next | [↓](#storage) · [canvas](services/storage/HANDOFF.md) |
| Input | `built` — v0.0 plus the mock loop, integrated E2E 2026-07-09 | serve-loop build | [canvas](services/input/HANDOFF.md) |
| Inference | `built` — v0.0 live on real Qwen3-VL-32B, vLLM TP=8 on node-7 | serve-loop build | [canvas](services/inference/HANDOFF.md) |
| Output | `built` — v0.0 plus the mock loop, integrated E2E 2026-07-09 | serve-loop build | [canvas](services/output/HANDOFF.md) |
| Continuum | `built` — the learn loop is closed, training under `consolidation-v2.0` | Morpheus + Phase-3 sessions | [↓](#continuum) · [canvas](services/continuum/HANDOFF.md) |
| Platform | `built` — serve and learn bring-up; the D9 backbone is `designed` | serve + learn | [↓](#platform) · [canvas](services/platform/HANDOFF.md) |

### Recording

> `built` · 144 tests · [canvas](services/recording/HANDOFF.md)

**In one line.** Three capture clients run on real hardware and every chunk is accounted for.

**What shipped**

- Gap-detection, VAD-cut chunking, and three capture clients — phone web, Chrome MV3 extension,
  mac CLI, all verified `clean` on real hardware.
- The async ingest seam ([D16](DECISIONS.md)) and [D9](DECISIONS.md) `/metrics` emission.

**Watch out for**

- M6's exit criterion stays open until the D9 backbone exists — see §Next item 2.

### Data Processing

> `built` · 569 tests (+4 skipped) · [canvas](services/data-processing/HANDOFF.md)

**In one line.** One C2 record per chunk, built from slots, on a machinery/bureaucracy split.

**What shipped**

- The Slot Law ([D23](DECISIONS.md)) in running code: one record per `(chunk_id,
  pipeline_version)`, `content.slots` with one producer per slot, identity from two components,
  no output-affecting env knobs (L4).
- Models as supervised long-lived servers (`servers/whisper|pyannote|ast|ocr` plus the Qwen3-VL
  captioner on `:8161`); DP is the thin async orchestrator.
- Async ingest with a durable journal (kill/restart re-drive), heal-on-redrive, and
  zero-silent-loss `/continuity` accounting — all drilled against the live fleet.

**Watch out for**

- The supervisor runs **inside** the DP process, so DP is the parent of all eight replicas. A
  graceful stop takes them with it; a `kill -9` leaves eight orphans holding ports and GPU memory.
- Follow-ups, none blocking: the `/raw`-replay backfill tool (owed), per-modality ingest fairness
  (unset), and the pending captioner `vlm.v1→v2` deploy (next restart).

### Storage

> `built` · 354 tests · [canvas](services/storage/HANDOFF.md)

**In one line.** Storage owns the day-log ([D18](DECISIONS.md)).

**What shipped**

- The day-log, the training-window ledger, and the sole `window_id` minter.
- Hosting for C12, C13 and C14.
- Day-log byte-identity against continuum, proven over two window origins including a misaligned
  one ([D20](DECISIONS.md)'s bar).

### Continuum

> `built` · 264 tests (+7 skipped) · [canvas](services/continuum/HANDOFF.md)

**In one line.** The learn loop is closed and runs on storage's HTTP surface.

**What shipped**

- HTTP clients for C10, C12, C13 and C14.
- The window comes from storage and the zone from the C12 profile read; continuum derives neither.
- A crash now leaves the window open, so a retry resumes it.
- The live two-process seam is green: 10 steps, 151 checks.

### Platform

> `built` (bring-up) · [canvas](services/platform/HANDOFF.md)

**In one line.** Both loops have a one-command bring-up; the shared observability backbone does not.

**What shipped**

- `run_all.sh` (serve) and `run_learn.sh` (learn), both run E2E 2026-07-09.

**Watch out for**

- The [D9](DECISIONS.md) shared backbone is `designed`, not built. It is §Next item 2, and it is
  what keeps recording's M6 exit criterion open.

## Founders' aspect threads

Each thread carries its own reasoning and a newest-first worklog. The board does not restate them.

| Aspect | File | State |
|---|---|---|
| Engineering | [handoff/engineering.md](handoff/engineering.md) | seeded — cross-service sequencing, integration, infra calls |
| Research | [handoff/research.md](handoff/research.md) | seeded — first agenda: POC→continuum bridge, research agenda v1 |
| Design / UX | [handoff/design.md](handoff/design.md) | seeded |
| Hiring / Ops | [handoff/hiring-ops.md](handoff/hiring-ops.md) | seeded |

---

## Escalations (open items needing a founders' decision)

Asks that cross a service boundary, or that need a founders' call before anyone builds.

| # | Ask | Owner(s) | Blocks | Founders' call? |
|---|---|---|---|---|
| **E-5** | The parked additive C2 edit — the ask is to *not* take it yet | founders → storage + data-processing | nothing | when triggered [↓](#e-5--the-parked-additive-c2-edit) |
| **E-2** | Retraction: the remaining orchestration and cascade legs | storage + platform | nothing | service-level [↓](#e-2--the-retraction-primitive) |
| **E-1 · E-4 · E-6** | Sibling-service asks with no contract surface | recording · continuum | cost figure · RWT granularity | no [↓](#e-1--e-4--e-6--sibling-service-asks) |

### E-5 — the parked additive C2 edit

> open · founders' session → storage + data-processing · **the ask is to not take it yet**

**In one line.** Two additive C2 fields are written up and deliberately not ratified, because
nothing would read them.

**The ask**

- An additive slot or field carrying OCR bbox geometry (DP charter OQ14b).
- A root `quality{}` block — the CHARTER risk row.

**Why it's this way**

- Neither field has a consumer today, so both would ship with zero readers.
- The exact diff, its edit sites, and the asymmetric-mirror footgun are already written up, so the
  ratification session gets a decision rather than a project.
- Cash OQ14b and the quality risk together, in one additive commit, when the first real geometry or
  quality-gating consumer lands.

**Watch out for**

- When it is taken, edit [ARCHITECTURE.md](ARCHITECTURE.md) §Contracts first, per ORG's
  contract-edit order.

### E-2 — the retraction primitive

> whole-record retraction is **built and live** ([D28](DECISIONS.md)) · remaining legs: storage + platform

**In one line.** Deleting a record is a privacy and space primitive, and the whole-record shape of
it is already running.

**The ask**

- Remaining: Platform M2 orchestration, and the reservoir cascade leg.

**Why it's this way**

- Retraction is whole-record, not kind-granular: one record per chunk ([D24](DECISIONS.md)) means
  there is no finer thing to delete. `DELETE /context/records` keys on `record_id` / `chunk_id` /
  `pipeline_version`, with a dry-run manifest.
- It **grows**: once storage materializes day-logs and hosts the reservoir, every deletion must
  cascade to both, because each is a second copy of user content.
- Shape and the widened M5 are recorded in the [storage charter](services/storage/CHARTER.md).

**Watch out for**

- Right-to-be-forgotten and version-forward reprocess both already promise this primitive.

### E-1 · E-4 · E-6 — sibling-service asks

> open · recording · continuum · no founders' call — routed service→service per [ORG.md](ORG.md)

**In one line.** Three asks with no contract surface, noted here for visibility only.

**The ask**

- **Recording (E-1):** `--segment-seconds 10→60`. The single largest cost lever at 5.8×; it moves
  the audio leg too, so it is a joint call with DP-audio.
- **Continuum (E-4):** per-fragment local timestamps in `_render_block`, OCR dedup, renderer
  ordering, and a recipe fork.
- **Recording (E-6):** auto-retry of `failed` segments — a 503 is recoverable but becomes terminal
  in 1.5 s.

**Why it's this way**

- **E-4 is resolved in premise by [D17](DECISIONS.md)**: the timezone was never the blocker, and C1
  carries one. The remainder is a small continuum-only change.
- Each ASR fragment's own UTC timestamp is already in the day-log, per sub-span; the renderer
  currently shows only the block-level span.
- The zone is resolved per record, so per-fragment local timestamps are a renderer change with no
  contract, no DP work and no scheduling dependency.

**Watch out for**

- OCR and caption lines carry no per-fragment time, so this covers **ASR fragments only** until the
  day-log carries fragment times for the others. Still continuum-side, still no contract.

---

## Next

Open items only. Anything finished moves to [handoff/engineering.md](handoff/engineering.md)
§Worklog in the same session — it does not stay here struck through.

| # | Item | Owner | Why it's open |
|---|---|---|---|
| 0 | **Client live-stream testing — the next phase.** A real captured day flowing recording → DP → storage → continuum on real hardware. | recording + DP + storage + continuum | gated on real capture beginning, a lifestyle gate rather than an engineering one |
| 1 | **E-2's remaining legs** — Platform M2 orchestration + the reservoir cascade | storage + platform | whole-record retraction is live; these two legs are not [↓](#e-2--the-retraction-primitive) |
| 2 | **D9 observability backbone** — the shared Prometheus + Grafana | platform | Emission shipped; the backbone never got built, so no founder has a Grafana URL |
| 3 | **C5 shape pin** — a three-value status enum, nullable `adapter_dir` + `base_model_hash`, C6 eligibility as a log replay | storage + continuum + inference | `model_directory` is still the trivial C6 row, so hosting C5 is a build, not a transport swap |
| 4 | `min_block_chars` — D19's min-data floor, `designed` and not built | continuum | It appears nowhere in the repo. The design stands; the build does not exist |
| 5 | **Beta hand-off ([D12](DECISIONS.md))** — standing `dev` branch, three capture clients | founders | Standing, not blocked. Tunnel URL rotates → `services/recording/var/tunnel_url.txt` |
| 6 | **CTO to read the Platform charter internals** ([D1](DECISIONS.md)) | CTO | Accepted as-is at ratification; the read was deferred |
