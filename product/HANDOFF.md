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

- **Both loops run end to end on real infrastructure.** Serve loop closed on real Qwen3-VL-32B;
  capture alpha-complete on three surfaces; the learn loop is proven E2E, and the
  **storage↔continuum cutover shipped 2026-07-27** — storage owns the day-log, the training-window
  ledger and the `window_id` minter, and continuum runs on its HTTP surface.
- **The fleet is live on the cutover code and healthy** (D19 wipe done, stores backed up and verified
  restorable first). Proven on the real fleet, not in tests: real capture → faster-whisper →
  `/context`; a C12 profile set and a missing one 404'd; a nightly to **published** over HTTP; the
  watermark advancing **only** on `published`; exactly one active C5 row.
- **Suites:** storage **310** · continuum **262** · recording **144** · data-processing **788** (+21
  skipped). All eleven accuracy-review items are closed and the review file is retired.
- **Down by choice:** the serve loop (vLLM + app services). Relaunch `run_all.sh` +
  `services/inference/serve_vllm.sh` when needed. Learn fleet is up on node-7 (storage 8083 ·
  recording 8084 · data-processing 8085); `INGEST_ASYNC` and `INGEST_ISOLATION` stay off by default.
- **Nothing is blocking.** Every item in §Next is a follow-up, not a gate.

---

## Service status board

| Service | Status | Lead session | Canvas |
|---|---|---|---|
| Recording | **capture M1 + computer surfaces — ALPHA COMPLETE.** Checked gap-detection, VAD-cut chunking, and 3 capture clients (phone web / Chrome-MV3 extension / mac CLI) all verified `clean` on real hardware. Async seam (D16) + D9 `/metrics` emission shipped. **144 tests** | computer-capture → **M6 emission DONE (merged 2026-07-19)** | [canvas](services/recording/HANDOFF.md) |
| Data Processing | **v1 + hardening + the screen-video clip path, all merged.** Durable ingest journal · stage-graph pipeline (every step a drop-in file) · opt-in subprocess isolation · clip-level captioning behind `VIDEO_PIPELINE=clip` (default `keyframe` = shipped legacy, byte-identical). **788 tests +21 skipped**. Cutover gates for the clip path remain (O-2 · O-8 · E-3(b)), none blocking | DP deep session → **merged; M7 substantially done** · screen-video WS-VC → **BUILT + integrated 2026-07-25** | [canvas](services/data-processing/HANDOFF.md) |
| Storage | **D18 expansion built and live.** Owns the day-log, the training-window ledger and the sole `window_id` minter; hosts C12/C13/C14. Day-log byte-identity vs continuum proven over two window origins incl. a misaligned one. **310 tests** | serve + learn → **storage/C10 board DONE; build slice next** | [canvas](services/storage/HANDOFF.md) |
| Input | **v0.0 built + mock loop runs** (integrated E2E 2026-07-09) | serve-loop WS-A | [canvas](services/input/HANDOFF.md) |
| Inference | **v0.0 live on real Qwen3-VL-32B** (vLLM TP=8 on node-7, verified E2E 2026-07-09) | serve-loop WS-B | [canvas](services/inference/HANDOFF.md) |
| Output | **v0.0 built + mock loop runs** (integrated E2E 2026-07-09) | serve-loop WS-C | [canvas](services/output/HANDOFF.md) |
| Continuum | ✅ **learn loop closed and cut over to storage.** HTTP clients for C10/C12/C13/C14; `window_for()`/`closed_window_before()`/`Window.local_date` deleted; `--tz` retired for the C12 profile read; a crash now leaves the window open so a retry resumes. `app/morpheus/` + `tests/parity/` byte-unchanged. Live two-process seam green (10 steps / 151 checks). **262 tests +7 skipped** | Morpheus + Phase-3 sessions | [canvas](services/continuum/HANDOFF.md) |
| Platform | **v0.0 serve bring-up + learn-loop bring-up** (`run_all.sh` + `run_learn.sh`, both run E2E 2026-07-09). **D9 shared backbone still unbuilt** — founders' §Next item 2 | serve + learn | [canvas](services/platform/HANDOFF.md) |

## Founders' aspect threads

Each thread carries its own reasoning and a newest-first worklog. The board does not restate them.

| Aspect | File | State |
|---|---|---|
| Engineering | [handoff/engineering.md](handoff/engineering.md) | **active** — D18/D19/D20 shipped; the storage expansion is built, continuum is cut over, the fleet runs on it. Next acts are follow-ups (E-2 · D9 backbone · E-3(b)), not blockers |
| Research | [handoff/research.md](handoff/research.md) | seeded — first agenda: POC→continuum bridge, research agenda v1 |
| Design / UX | [handoff/design.md](handoff/design.md) | seeded |
| Hiring / Ops | [handoff/hiring-ops.md](handoff/hiring-ops.md) | seeded |

---

## Escalations (open items needing a founders' decision)

**Opened 2026-07-24 by the data-processing screen-video design session (WS-VC); the build is now
DONE + integrated (2026-07-25), so these are the CUTOVER gates + founders' calls, not build
blockers.** Full write-ups, with the measured numbers behind each, in
[services/data-processing/handoff/ws-video-clip.md](services/data-processing/handoff/ws-video-clip.md)
§10. Two block the *cutover* (E-2 storage retraction; a real-VLM O-8 run needs E-3(b)); one is a
founders' allocation call (E-3(b)); one is a contract edit deliberately **not taken yet** (E-5).
**Resolved during the build: E-3(a)** (the `--limit-mm-per-prompt` serving-flag ask) — WS-A's probe
verified vLLM 0.24.0 defaults the image cap to 999 and clamps nothing at 768×480, so the multi-image
call validates on the *unmodified* `serve_vllm.sh`; the flags are determinism pins, not a
prerequisite.

| # | Ask | Owner(s) | Blocks | Founders' call? |
|---|---|---|---|---|
| **E-3(b)** | A **captioner VL endpoint distinct from the user-facing `:8000`**. Today DP's `VIDEO_VLM_URL` and inference's `VLLM_URL` both default to the *same* Qwen3-VL-32B TP=8 instance on node-7 at `gpu_memory_utilization=0.90`. DP's prefill bursts would land in the same continuous batch as the assistant's decode steps; the failure mode is assistant TTFT, which no GPU-percent figure surfaces. During a 4 h nightly training window DP would dead-letter ~240 chunks = **4 h of a user's screen life**, after paying full ffmpeg prep on each. A 7B-class VL on 1–2 GPUs carries this load and isolates DP from both tenants. | platform + inference | scale-up (not the build) | **YES** — it closes DP CHARTER OQ3 (GPU placement/contention), which `platform/CHARTER.md:73,83` still lists as an unresolved *proposal* |
| **E-5** | The **parked additive C2 edit**: `enrichments.text_regions[]` (OCR bbox geometry, CHARTER OQ14b) + a root `quality{}` (CHARTER risk row). **The ask today is to NOT take it.** `grep -rn enrichments continuum/app/` returns exactly one hit — the synthetic-record generator — so both fields would have **zero readers**. The exact diff, its four edit sites, and the asymmetric-mirror footgun are written up so that when the first real geometry or quality-gating consumer lands, the ratification session gets a decision, not a project. Cash OQ14b and the quality risk together, in one freeze-additive commit. | founders' session → then storage + data-processing rows | nothing | **YES, when triggered** — edit `ARCHITECTURE.md` §Contracts first, per `ORG.md:44-45` |
| **E-2** *(DEMOTED by D18 — no longer blocks the cutover)* | **D18 changed this row's premise.** The WS-VC double-count is fixed by the **day-log materialization rule** (one dialect per record, latest `ingest_time` wins per `(chunk_id, content.kind, discriminator)`), not by a delete — so E-2 reverts to being the retraction / privacy / space primitive it always should have been, and the cutover no longer waits on it. It also **grows**: once storage materializes day-logs and hosts the reservoir, every deletion must **cascade to both**, because each is a second copy of user content. Shape + the widened M5 are recorded in the [storage charter](services/storage/CHARTER.md). *Original ask, for the record:* Storage: a **kind-aware** `DELETE /context/records?user_id=&from=&to=&pipeline_version=&kind=` retraction primitive. `record_id` forks by design on a dialect bump, old records persist, and `daylog.py` filters on neither `kind` nor `pipeline_version` — so any day re-consolidated across a cutover renders **both** dialects and double-counts. **Must key on `content.kind`**: the Phase-3 replay proved captions and transcripts can share one `pipeline_version` (`injected_caption` declares no fragment), so a kind-blind delete would remove transcripts to remove captions. Also the primitive right-to-be-forgotten and version-forward reprocess both already promise. | storage | ~~the cutover~~ **nothing** (D18) | service-level; still wanted, no longer the gate |
| **E-1 · E-4 · E-6** | Sibling-service asks with no contract surface, routed service→service per `ORG.md`: **recording** `--segment-seconds 10→60` (the single largest cost lever, 5.8×; moves the audio leg too, so it is a joint call with DP-audio) · **continuum** per-fragment local timestamps in `_render_block` + OCR dedup + renderer ordering + a recipe fork · **recording** auto-retry of `failed` segments (a 503 is recoverable but becomes terminal in 1.5 s). **E-4 RESOLVED-IN-PREMISE by D17 (2026-07-26); the remainder is a small continuum-only change.** E-4 read "DP cannot do it, C1 carries no timezone" — the timezone was never the blocker, and C1 now carries one anyway. Each ASR fragment's own UTC timestamp is **already in the day-log** (`daylog.py:110,116` write `{"spk","text","t": sub["t_start"]}` per sub-span) and `_render_block` simply ignores `t`, rendering only the block-level `group[0].t_start`/`group[-1].t_end` span. The zone to render them in is now resolved per record (`_block_zone`), so per-fragment local timestamps are **a renderer change with no contract, no DP work, and no scheduling dependency**. One honest residual: `seg.ocr`/`seg.caption` are bare strings with no `t`, so per-fragment times cover **ASR fragments only** until the day-log carries fragment times for the other kinds — still continuum-side, still no contract. The travel case that would once have made this wrong is now handled. | recording · continuum | cost figure / the RWT-granularity goal | no — noted for visibility |

*Resolved items move to the Decisions log below. (The async `/ingest` reply shape was proposed +
ratified in-session 2026-07-19 → **D16**.)*

---

## Decisions

**D1–D20 live in [DECISIONS.md](DECISIONS.md)** — the numbered register, newest first. They are not
restated here or anywhere else; cite the number. Most recent:

| # | Decision | Status |
|---|---|---|
| **D20** | The exit bar for the storage↔continuum cutover, and a definition of "done" that can be met | MET + verified 2026-07-27 |
| **D19** | Stage: PROTOTYPE — nothing is set in stone, contracts included, and the docs must say so | adopted, standing |
| **D18** | Storage owns the day-log; the window becomes an ingest-time watermark; `window_id` goes opaque | BUILT 2026-07-27 |

A service owner who needs a decision changed **proposes it in §Escalations above**; a founders'
session ratifies it and gives it a number.

---

## Next

Open items only. Anything finished moves to [handoff/engineering.md](handoff/engineering.md)
§Worklog in the same session — it does not stay here struck through.

| # | Item | Owner | Why it's open |
|---|---|---|---|
| 1 | **E-2 — the retraction primitive.** Kind-aware `DELETE /context/records`, **cascading to the day-log and the reservoir** (each is a second copy of user content). | storage | The cutover hit its absence directly: a **re-wipe is currently the only way to retract rows**. Also the primitive right-to-be-forgotten already promises. Shape in [storage CHARTER](services/storage/CHARTER.md) M5 |
| 2 | **D9 observability backbone** — the ONE shared Prometheus + Grafana, provisioning the per-service dashboard JSONs + node/dcgm exporters. | platform | The **emission** half shipped (DP M8 + recording M6); the backbone was never built, so recording M6's exit criterion is still open and no founder has a Grafana URL |
| 3 | **E-3(b) — a captioner VL endpoint distinct from the user-facing `:8000`.** | platform + inference | Founders' allocation call — see §Escalations. Closes DP CHARTER OQ3 |
| 4 | **C5 freeze** — inherited constraints, not just docs: a **three**-value status enum, **nullable** `adapter_dir` + `base_model_hash`, and C6 eligibility as a **log replay** (not "latest row wins", or a gate-failed candidate becomes servable). | storage + continuum + inference | Storage's `model_directory` is still the trivial C6 row, so hosting C5 is a build, not a transport swap. Deferred by D19; free while C5 is unfrozen |
| 5 | **`min_block_chars` (D19's min-data floor)** — designed, **not built**; it appears nowhere in the repo. | continuum | A retracted claim, recorded rather than caveated. The design stands; the build does not exist |
| 6 | **Beta hand-off (D12)** — standing `dev` branch; the three capture clients are the data-collection front door (tunnel URL rotates → `services/recording/var/tunnel_url.txt`). | founders | Standing, not blocked |
| 7 | **CTO to read the Platform charter internals** (D1). | CTO | Accepted as-is at ratification; the read was deferred |
