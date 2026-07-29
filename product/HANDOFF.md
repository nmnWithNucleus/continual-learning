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

**Stage: PROTOTYPE** (D19) · **Last updated:** 2026-07-27

---

## Where we are today

- **Both loops run end to end on real infrastructure.** The storage↔continuum cutover shipped
  2026-07-27.
- **The fleet is live on the cutover code and healthy.** The D19 wipe is done; stores were backed
  up and verified restorable first.
- **Proven on the real fleet, not in tests:** real capture → faster-whisper → `/context`; a C12
  profile set and a missing one 404'd; a nightly to `published` over HTTP; the watermark advancing
  only on `published`; exactly one active C5 row.
- **Suites:** storage 310 · continuum 262 · recording 144 · data-processing 788 (+21 skipped).
- **All eleven accuracy-review items are closed** and the review file is retired.
- **Down by choice:** the serve loop (vLLM + app services). Relaunch `run_all.sh` +
  `services/inference/serve_vllm.sh` when needed.
- **Learn fleet is up on node-7** (storage 8083 · recording 8084 · data-processing 8085);
  `INGEST_ASYNC` and `INGEST_ISOLATION` stay off by default.
- **Nothing is blocking.** Every item in §Next is a follow-up, not a gate.

---

## Service status board

| Service | Status | Lead session | Canvas |
|---|---|---|---|
| Recording | `built` — capture M1 + computer surfaces, alpha-complete | computer-capture → M6 emission merged 2026-07-19 | [↓](#recording) · [canvas](services/recording/HANDOFF.md) |
| Data Processing | `built` — v1, hardening and the screen-video clip path all merged | DP deep session · screen-video WS-VC | [↓](#data-processing) · [canvas](services/data-processing/HANDOFF.md) |
| Storage | `built` — the D18 expansion is live and owns the day-log | serve + learn; build slice next | [↓](#storage) · [canvas](services/storage/HANDOFF.md) |
| Input | `built` — v0.0 plus the mock loop, integrated E2E 2026-07-09 | serve-loop WS-A | [canvas](services/input/HANDOFF.md) |
| Inference | `built` — v0.0 live on real Qwen3-VL-32B, vLLM TP=8 on node-7 | serve-loop WS-B | [canvas](services/inference/HANDOFF.md) |
| Output | `built` — v0.0 plus the mock loop, integrated E2E 2026-07-09 | serve-loop WS-C | [canvas](services/output/HANDOFF.md) |
| Continuum | `built` — the learn loop is closed and cut over to storage | Morpheus + Phase-3 sessions | [↓](#continuum) · [canvas](services/continuum/HANDOFF.md) |
| Platform | `built` — serve and learn bring-up; the D9 backbone is `designed` | serve + learn | [↓](#platform) · [canvas](services/platform/HANDOFF.md) |

### Recording

> `built` · 144 tests · [canvas](services/recording/HANDOFF.md)

**In one line.** Three capture clients run on real hardware and every chunk is accounted for.

**What shipped**

- Gap-detection, VAD-cut chunking, and three capture clients — phone web, Chrome MV3 extension,
  mac CLI, all verified `clean` on real hardware.
- The async ingest seam ([D16](DECISIONS.md)) and [D9](DECISIONS.md) `/metrics` emission.
- M6 emission merged 2026-07-19.

**Watch out for**

- M6's exit criterion stays open until the D9 backbone exists — see §Next item 2.

### Data Processing

> `built` · 788 tests (+21 skipped) · [canvas](services/data-processing/HANDOFF.md)

**In one line.** v1, the hardening pass and the screen-video clip path are all merged.

**What shipped**

- A durable ingest journal, and a stage-graph pipeline where every step is a drop-in file.
- Opt-in subprocess isolation.
- Clip-level captioning behind `VIDEO_PIPELINE=clip`; the default `keyframe` is the shipped legacy
  path and is byte-identical.

**Watch out for**

- Cutover gates for the clip path remain: O-2 · O-8 · [E-3(b)](#e-3b--a-captioner-vl-endpoint).
  None of them blocks.

### Storage

> `built` · 310 tests · [canvas](services/storage/HANDOFF.md)

**In one line.** The D18 expansion is built and live: storage owns the day-log.

**What shipped**

- The day-log, the training-window ledger, and the sole `window_id` minter.
- Hosting for C12, C13 and C14.
- Day-log byte-identity against continuum, proven over two window origins including a misaligned
  one ([D20](DECISIONS.md)'s bar).

### Continuum

> `built` · 262 tests (+7 skipped) · [canvas](services/continuum/HANDOFF.md)

**In one line.** The learn loop is closed and runs on storage's HTTP surface.

**What shipped**

- HTTP clients for C10, C12, C13 and C14.
- `window_for()`, `closed_window_before()` and `Window.local_date` deleted; `--tz` retired in
  favour of the C12 profile read.
- A crash now leaves the window open, so a retry resumes it.
- `app/morpheus/` and `tests/parity/` are byte-unchanged.
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
| Engineering | [handoff/engineering.md](handoff/engineering.md) | **active** — D18/D19/D20 shipped; next acts are follow-ups (E-2 · D9 backbone · E-3(b)), not blockers |
| Research | [handoff/research.md](handoff/research.md) | seeded — first agenda: POC→continuum bridge, research agenda v1 |
| Design / UX | [handoff/design.md](handoff/design.md) | seeded |
| Hiring / Ops | [handoff/hiring-ops.md](handoff/hiring-ops.md) | seeded |

---

## Escalations (open items needing a founders' decision)

Opened 2026-07-24 by the data-processing screen-video design session (WS-VC). The build is done and
integrated (2026-07-25), so these are cutover gates and founders' calls, not build blockers. Full
write-ups, with the measured numbers behind each, in
[services/data-processing/handoff/ws-video-clip.md](services/data-processing/handoff/ws-video-clip.md)
§10.

| # | Ask | Owner(s) | Blocks | Founders' call? |
|---|---|---|---|---|
| **E-3(b)** | A captioner VL endpoint distinct from the user-facing `:8000` | platform + inference | scale-up, not the build | **yes** [↓](#e-3b--a-captioner-vl-endpoint) |
| **E-5** | The parked additive C2 edit — the ask is to *not* take it yet | founders → storage + data-processing | nothing | when triggered [↓](#e-5--the-parked-additive-c2-edit) |
| **E-2** | A kind-aware retraction primitive; demoted by D18 | storage | nothing | service-level [↓](#e-2--the-retraction-primitive) |
| **E-1 · E-4 · E-6** | Sibling-service asks with no contract surface | recording · continuum | cost figure · RWT granularity | no [↓](#e-1--e-4--e-6--sibling-service-asks) |

**Resolved during the build: E-3(a)** — the `--limit-mm-per-prompt` serving-flag ask. WS-A's probe
verified vLLM 0.24.0 defaults the image cap to 999 and clamps nothing at 768×480, so the
multi-image call validates on the *unmodified* `serve_vllm.sh`; the flags are determinism pins, not
a prerequisite.

### E-3(b) — a captioner VL endpoint

> open · platform + inference · founders' call

**In one line.** Give the captioner its own VL endpoint so its prefill bursts cannot land in the
assistant's continuous batch.

**The ask**

- A captioner VL endpoint distinct from the user-facing `:8000`. A 7B-class VL on 1–2 GPUs carries
  this load and isolates data-processing from both tenants.

**Why it's this way**

- Today DP's `VIDEO_VLM_URL` and inference's `VLLM_URL` both default to the *same* Qwen3-VL-32B
  TP=8 instance on node-7 at `gpu_memory_utilization=0.90`.
- DP's prefill bursts would share a continuous batch with the assistant's decode steps. The failure
  mode is assistant TTFT, which no GPU-percent figure surfaces.
- During a 4 h nightly training window DP would dead-letter ~240 chunks, **4 h of a user's screen
  life**, after paying full ffmpeg prep on each.

**Watch out for**

- It closes DP CHARTER OQ3 (GPU placement and contention), which `platform/CHARTER.md:73,83` still
  lists as an unresolved *proposal*.

### E-5 — the parked additive C2 edit

> open · founders' session → storage + data-processing · **the ask is to not take it yet**

**In one line.** The additive C2 fields are written up and deliberately not ratified, because
nothing would read them.

**The ask**

- `enrichments.text_regions[]` — OCR bbox geometry, CHARTER OQ14b.
- A root `quality{}` — the CHARTER risk row.

**Why it's this way**

- `grep -rn enrichments continuum/app/` returns exactly one hit, the synthetic-record generator, so
  both fields would have **zero readers** today.
- The exact diff, its four edit sites, and the asymmetric-mirror footgun are already written up, so
  the ratification session gets a decision rather than a project.
- Cash OQ14b and the quality risk together, in one additive commit, when the first real geometry or
  quality-gating consumer lands.

**Watch out for**

- When it is taken, edit [ARCHITECTURE.md](ARCHITECTURE.md) §Contracts first, per `ORG.md:44-45`.

### E-2 — the retraction primitive

> demoted by [D18](DECISIONS.md) — no longer blocks the cutover · storage

**In one line.** A kind-aware delete for `/context` records, now a privacy and space primitive
rather than a cutover gate.

**The ask**

- Storage: `DELETE /context/records?user_id=&from=&to=&pipeline_version=&kind=`.

**Why it's this way**

- D18 changed this row's premise. The WS-VC double-count is fixed by the **day-log materialization
  rule** — one dialect per record, latest `ingest_time` wins per `(chunk_id, content.kind,
  discriminator)`, not by a delete.
- It also **grows**: once storage materializes day-logs and hosts the reservoir, every deletion must
  cascade to both, because each is a second copy of user content.
- Shape and the widened M5 are recorded in the [storage charter](services/storage/CHARTER.md).

**Watch out for**

- **It must key on `content.kind`.** The Phase-3 replay proved captions and transcripts can share
  one `pipeline_version` (`injected_caption` declares no fragment), so a kind-blind delete would
  remove transcripts in order to remove captions.
- The original ask, for the record: `daylog.py` filters on neither `kind` nor `pipeline_version`,
  so any day re-consolidated across a cutover renders both dialects and double-counts.
- Right-to-be-forgotten and version-forward reprocess both already promise this primitive.

### E-1 · E-4 · E-6 — sibling-service asks

> open · recording · continuum · no founders' call — routed service→service per `ORG.md`

**In one line.** Three asks with no contract surface, noted here for visibility only.

**The ask**

- **Recording:** `--segment-seconds 10→60`. The single largest cost lever at 5.8×; it moves the
  audio leg too, so it is a joint call with DP-audio.
- **Continuum:** per-fragment local timestamps in `_render_block`, OCR dedup, renderer ordering,
  and a recipe fork.
- **Recording:** auto-retry of `failed` segments — a 503 is recoverable but becomes terminal in
  1.5 s.

**Why it's this way**

- **E-4 is resolved in premise by [D17](DECISIONS.md)** (2026-07-26); the remainder is a small
  continuum-only change.
- E-4 read "DP cannot do it, C1 carries no timezone". The timezone was never the blocker, and C1
  now carries one anyway.
- Each ASR fragment's own UTC timestamp is **already in the day-log**: `daylog.py:110,116` write
  `{"spk","text","t": sub["t_start"]}` per sub-span, and `_render_block` simply ignores `t`,
  rendering only the block-level `group[0].t_start`/`group[-1].t_end` span.
- The zone to render them in is now resolved per record (`_block_zone`), so per-fragment local
  timestamps are a renderer change with no contract, no DP work and no scheduling dependency.

**Watch out for**

- `seg.ocr` and `seg.caption` are bare strings with no `t`, so per-fragment times cover **ASR
  fragments only** until the day-log carries fragment times for the other kinds. Still
  continuum-side, still no contract.

---

## Next

Open items only. Anything finished moves to [handoff/engineering.md](handoff/engineering.md)
§Worklog in the same session — it does not stay here struck through.

| # | Item | Owner | Why it's open |
|---|---|---|---|
| 1 | **E-2 — the retraction primitive**, cascading to the day-log and the reservoir | storage | A re-wipe is currently the only way to retract rows [↓](#e-2--the-retraction-primitive) |
| 2 | **D9 observability backbone** — the shared Prometheus + Grafana | platform | Emission shipped; the backbone never got built, so no founder has a Grafana URL |
| 3 | **E-3(b)** — a captioner VL endpoint distinct from `:8000` | platform + inference | Founders' allocation call; closes DP CHARTER OQ3 [↓](#e-3b--a-captioner-vl-endpoint) |
| 4 | **C5 shape pin** — a three-value status enum, nullable `adapter_dir` + `base_model_hash`, C6 eligibility as a log replay | storage + continuum + inference | `model_directory` is still the trivial C6 row, so hosting C5 is a build, not a transport swap |
| 5 | `min_block_chars` — D19's min-data floor, `designed` and not built | continuum | It appears nowhere in the repo. The design stands; the build does not exist |
| 6 | **Beta hand-off ([D12](DECISIONS.md))** — standing `dev` branch, three capture clients | founders | Standing, not blocked. Tunnel URL rotates → `services/recording/var/tunnel_url.txt` |
| 7 | **CTO to read the Platform charter internals** ([D1](DECISIONS.md)) | CTO | Accepted as-is at ratification; the read was deferred |
