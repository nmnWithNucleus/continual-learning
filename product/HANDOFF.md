# HANDOFF — founders' working canvas (whole company)

> The single touch-point for the founders (CTO + AI co-founder) and the top of the
> escalation path. Read this first in any founders' session, then the aspect file you're
> working ([handoff/](handoff/)). Stable docs: [VISION.md](VISION.md) ·
> [ARCHITECTURE.md](ARCHITECTURE.md) · [ORG.md](ORG.md) · [PROMPTS.md](PROMPTS.md).
> Service-level state lives in each service's own HANDOFF.md — this board links, not restates.

**Last updated:** 2026-07-26 · maintained across founders' sessions.

---

## Service status board

| Service | Status | Lead session | Canvas |
|---|---|---|---|
| Recording | **capture M1 + computer surfaces — ALPHA COMPLETE** (checked gap-detection + VAD-cut chunking + 3 capture clients: phone web / Chrome-MV3 extension / mac CLI, all verified `clean` on real hardware — 2026-07-19; 110 tests) **+ async seam (D16: `dp_state` ledger + `/redrive`) + D9 `/metrics`+dashboard (M6 emission) — 120 tests** | computer-capture → **M6 emission DONE (merged 2026-07-19)** | [canvas](services/recording/HANDOFF.md) |
| Data Processing | **v1 + HARDENING done: durable ingest journal (kill-recovery; restart-amnesia/false-`gaps` CLOSED) · stage-graph pipeline (every step a drop-in file) · all 3 v1 review findings closed by construction (SlotView slot-ownership · mutate-overlap chaining · permit-at-dispatch fairness) · opt-in subprocess isolation (poison chunk → 1 chunk, not the service)** — on async `/ingest` (D16 wire, off-by-default) + D9 `/metrics`; audio/video byte-identical, real backends re-validated on node-7 (merged `5350f7a`, pushed 2026-07-21; suites re-verified by founders) **+ SCREEN-VIDEO CLIP PATH BUILT + INTEGRATED (WS-VC, 2026-07-25): 8 workstreams landed + merged to trunk — clip-level captioning (one multi-image VLM call/chunk) replaces per-keyframe calls; a dedicated CPU **OCR channel** (`kind='ocr'` record); a **versioned prompt pack whose digest IS the dialect**; the **record-vs-mutation law** enforced in CI + at registration; an offline eval harness that cannot write `/context` by construction — behind `VIDEO_PIPELINE=clip` (default `keyframe` = shipped legacy, byte-identical). DP suite **765**; lead-verified each WS (mutation-tested the law; caught + returned a masked order/registration bug before merge). Cutover gates (O-2 real-frame OCR bar · O-8 blind-vs-injected A/B vs a real VLM · E-2 or fresh user_id · E-3(b)) + follow-ups remain, none blocking** | DP deep session → **merged; M7 substantially done** · screen-video WS-VC → **BUILT + integrated 2026-07-25** | [canvas](services/data-processing/HANDOFF.md) |
| Storage | **D18 EXPANSION BUILT (2026-07-27) — 310 tests:** C12 profile · training-window ledger + the sole `window_id` minter · day-log materialization (C10 evolved) · C13 registry · C14 reservoir. Day-log byte-identity vs continuum proven over **two window origins incl. a misaligned one**. Earlier: **v0.0 + capture M0 built + integrated E2E** (serve loop + `/raw`/`/context` mock capture loop 2026-07-09; 32 tests post-D17) **+ SCOPE EXPANSION RATIFIED (D18, 2026-07-26) — DECIDED, NOT BUILT:** day-log materialization + training-window ledger (**C10 evolved**) · per-user profile (**C12**, schema minted) · recipe registry (**C13**) · reservoir custody (**C14**). Four new build items, none started | serve + learn → **storage/C10 board DONE; build slice next** | [canvas](services/storage/HANDOFF.md) |
| Input | **v0.0 built + mock loop runs** (integrated E2E 2026-07-09) | serve-loop WS-A | [canvas](services/input/HANDOFF.md) |
| Inference | **v0.0 live on real Qwen3-VL-32B** (vLLM TP=8 on node-7, verified E2E 2026-07-09) | serve-loop WS-B | [canvas](services/inference/HANDOFF.md) |
| Output | **v0.0 built + mock loop runs** (integrated E2E 2026-07-09) | serve-loop WS-C | [canvas](services/output/HANDOFF.md) |
| Continuum | ✅ **LEARN-LOOP INTEGRATION PROVEN END-TO-END (2026-07-25).** **CUT OVER TO STORAGE 2026-07-27 — 251 tests:** HTTP clients for C10/C12/C13/C14; `window_for()`/`closed_window_before()`/`Window.local_date` deleted; `--tz` retired for the C12 profile read; a crash now leaves the window open so a retry resumes. `app/morpheus/` + `tests/parity/` byte-unchanged. Live two-process seam green (10 steps/151 checks). *(D18 as originally ratified —* the day-log build leaves for storage (`daylog.py`/`window.py`; the parity-locked `Profile.render_block` **stays**), `window_for()` is deleted for the watermark window, and `nightly.py --tz` is replaced by the C12 profile read. Kickoff → **Morpheus** nightly-consolidation core reimplemented from the research line (`b3c58e1`), parity-proven; **M0** — a 32B life adapter our own pipeline trained → gate → C5 → **served in vLLM**; lean 5-verb loop over storage client seams; **Phase-3 DP dogfood: real Speed data through recording→DP→storage→continuum reproduces the baseline separation (PIPELINE SOUND).** Gate policy **v1.1** adopted. Now-pending (board): storage charter expansion + C10 evolution (below) | Morpheus + Phase-3 sessions | [canvas](services/continuum/HANDOFF.md) |
| Platform | **v0.0 serve bring-up + learn-loop bring-up** (`run_all.sh` + `run_learn.sh`, both run E2E 2026-07-09) | serve + learn | [canvas](services/platform/HANDOFF.md) |

## Founders' aspect threads

| Aspect | File | State |
|---|---|---|
| Engineering | [handoff/engineering.md](handoff/engineering.md) | active — **D18 (2026-07-26): storage/C10 board ratified** — day-log move + watermark window + `window_id` reformat + C12/C13/C14 minted, all **DECIDED, NOT BUILT**; next founder act is the **storage build slice** (C12 first — day-log materialization depends on it). Earlier: serve-loop v0.0 **closed on real Qwen3-VL-32B**; capture M1 + computer surfaces DONE; DP v1 + HARDENING merged; **D15 continuum kickoff → LEARN LOOP CLOSED (2026-07-25): Morpheus port parity-proven, M0 (32B adapter → C5 → vLLM), Phase-3 dogfood proves the real recording→DP→storage→continuum path carries it (PIPELINE SOUND).** Next founder acts: **storage/C10 board session** (expansion + C10 evolution — now carrying a fourth expansion row, the per-user profile owning `home_tz`, per **D17** 2026-07-26: timezone ownership decided, review item O-1 closed, C1/C2 untouched) |
| Research | [handoff/research.md](handoff/research.md) | seeded — first agenda: POC→continuum bridge, research agenda v1 |
| Design / UX | [handoff/design.md](handoff/design.md) | seeded |
| Hiring / Ops | [handoff/hiring-ops.md](handoff/hiring-ops.md) | seeded |

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
| D12 | **Branching + beta model.** Service work happens on branches off `main`, merged once coded + tested at a decent revision. A standing **`dev` branch (forked from `main`) is the beta playground** handed to testers — it may carry beta-only conveniences, never contract changes. First beta hand-off: the two proven loops (serve + learn) to Gnandeep, who drives them against his externally-stabilized fine-tunable model; storage's `GET /context/records?user_id=&from=&to=` range read is his training-window feed until C10 lands | 2026-07-18 | this board; [handoff/engineering.md](handoff/engineering.md) worklog; root `README.md` §Branches |
| D13 | **Consent gate de-prioritized (back-burner).** Ship-fast posture: the capture surfaces + learn loop mature first; the consent/deletion layer (recording M2 + platform's consent store) lands **before any non-team pilot user**, not before beta (beta testers are consenting teammates). The M2 red-team exit bar is unchanged whenever it lands | 2026-07-18 | this board; recording charter §v0 deliverables |
| D14 | **Capture transport = segmented HTTP upload for ALL v0 surfaces** (phone / extension / mac CLI). Our capture path is the loss-intolerant, offline-resilient *archive/training* job (the Axon-bodycam pattern), not low-latency live-view (the Ring/Nest pattern — which runs both paths separately). **Continuous streaming ingest (WebSocket/RTSP/SRT → server segmenter) is a deferred ADDITIVE leg** terminating in the existing spool→demux→carve→emit machinery; C1/C2 unchanged (C1 begins after transport). Live-view is out of v0 scope | 2026-07-19 | recording canvas §Pinned decisions (D-M1-5); [ARCHITECTURE.md](ARCHITECTURE.md) capture path |
| D15 | **Post-deep-session build order: continuum kickoff is the next founders-led slice**, gated on a **C10 v0 interface freeze** (storage × continuum propose, founders ratify; frozen against the beta-proven `/context` range read). **Platform's D9 backbone** (the one shared Prometheus + Grafana) runs as the small parallel slice. **DP image/text pipelines (M2) deferred until a producing surface exists** — no `image`/`text` C1 stream exists on the fleet today; screen text already flows via the video-keyframe OCR weave (D8); the OQ14b bbox additive waits with it. Mobile+C8 and a standalone C10 freeze considered + passed (rationale in the engineering thread) | 2026-07-19 | [handoff/engineering.md](handoff/engineering.md) §Post-capture-alpha sequencing; continuum canvas; this board |
| D16 | **Async `/ingest` reply shape RATIFIED** (inter-service wire, prose-pinned in the DP canvas at merge — not a C-number; C1/C2 untouched). `INGEST_ASYNC` off-by-default, inline byte-unchanged. Async: **202** `{ok,accepted,chunk_id}` (+`duplicate:true` on in-flight dedup hit) · **200+record_ids** on done-dedup-hit · 400/422/501 resolve synchronously pre-claim · **503** bounded-queue backpressure. `/continuity` gains additive `processed`+`dead_lettered`. **Invariant preserved: `dp_acked` == "C2 durably written"** — recording moves in-slice (`dp_state='accepted'` + gap-report reconciliation; `clean` = every chunk confirmed; accepted-unconfirmed → `recording`, dead-lettered → `gaps`). Guarantee: **never falsely `clean`**; auto-recovery = M7 durable journal. **Condition:** accepted-unconfirmed re-drive path named + drilled in-slice. **Accepted caveat:** `record_ids=[]` ledger provenance on 202-path chunks (ids derivable) | 2026-07-19 | [handoff/engineering.md](handoff/engineering.md) ratification block; DP canvas (pinned prose at merge); recording canvas (verdict semantics) |
| D17 | **Timezone: the DEVICE owns the fact, storage owns the policy — and they are different things.** Conflating them was the original bug. **(1) The FACT** (where the user physically was at a moment) is reported **per chunk by the capturing device**: `device_tz` (IANA) + `device_utc_offset_minutes`, **additive-optional on C1**, carried **verbatim** by DP into **C2 `source{}`** (DP does *no* timezone logic — it is provenance passthrough like `device_id`, so the emission law's T2 does not gate it), persisted by storage as promoted columns beside the UTC instant, and read by continuum's renderer. This is the only design that is **correct under travel**, and the device already knew it — every capture client computed the local instant and discarded the zone converting to UTC. **(2) The POLICY** (when is this user's night?) is storage's per-user profile **`home_tz`**, whose only jobs are **scheduling** the nightly fire and **fallback** when a record carries no zone. **Timestamps stay UTC-canonical**: UTC is the sole ordering/range axis, C10 is a pure duration query needing no zone. **Never** store a derived local wall-clock (two sources of truth), **never** accept abbreviations (`PST` is ambiguous + DST-sensitive; rejected 400 at the capture edge). **Supersedes the first draft of D17** (same day), which had storage own a per-user tz *only*, banned tz from C1/C2, and deferred travel — wrong because it applied T2 as a veto to a field whose consumer this very slice builds, and because it defended a `window_id` total-order problem the watermark window dissolves. — **STATUS, split deliberately (O-12, pass 2): the timezone split above is BUILT + verified end to end this session** (C1→C2→storage→renderer; `--tz` required, no default anywhere; suites storage 32 · continuum 189 · recording 144 · DP 770+21; node-7 migrated + smoked). **The companion window-semantics clause is DECIDED, NOT BUILT:** the cycle window *should become* the watermark range `[last_trained_t, now)` — what ARCHITECTURE's C10 row and the storage charter always said, and which retires the local-date pathologies (23 h/25 h days, a repeated local date colliding `window_id`) — but `window_for()` / `closed_window_before()` are still local-date and `nightly.py` still calls them. Nothing is broken; local-date windows work. **Open question the builder must answer first: does `window_id` survive the move?** It is today the local start date and it keys the day-log, the cycle journal, C5's `training_window`, and publish's active-alias monotonicity. Under a watermark window the natural key is the window's END INSTANT (monotone per user by construction, no dateline case) — but that changes the `w2026-07-21` format and therefore forks adapter lineage, which is a board call, not a refactor. Belongs to the storage/C10 board session with the day-log move | 2026-07-26 | [ARCHITECTURE.md](ARCHITECTURE.md) §Contracts C1+C2 blocks, §Ownership splits *User timezone*, C10 row; [contracts/](contracts/) c1+c2 schemas; [storage](services/storage/CHARTER.md) · [continuum](services/continuum/CHARTER.md) · [data-processing](services/data-processing/CHARTER.md) · [recording](services/recording/CHARTER.md) charters; [handoff/engineering.md](handoff/engineering.md) |
| D18 | **Storage owns the day-log; the training window becomes a watermark over OUR OWN ingest clock; `window_id` stops meaning anything.** Ratifies the 2026-07-25 storage-charter expansion and the C10 evolution, and completes D17's three deferred clauses. **STATUS: ALL FIVE PARTS ARE *DECIDED, NOT BUILT*.** Nothing below ships until it is built, and the day-log's byte-equality is *proven* rather than asserted. **(0) `window_id`** — an **opaque, path-safe, lexicographically-ordered per-user token**, `w<YYYYMMDD>T<HHMMSS>Z` (e.g. `w20260721T110000Z`), minted **once** from the window's **end instant** by storage and **parsed by nobody**. Seconds, not minutes, because a truncating id can silently collide two windows and an id collision corrupts the journal, the reservoir and C5 lineage at once. The format change is **not a cost of the watermark window — it is a consequence of it**: under `[last_trained_t, now−δ)` there is no local date to name (a window can span 23 h, 25 h, or 47 h after a missed night), so keeping `w<local-date>` would mean synthesising a local date purely to name a window, reintroducing the timezone we just proved the query never needed and making the id lie about the window's extent. *Re-keys, in full:* filesystem paths (orphaned, not corrupted — no migration needed for correctness); the **four** string comparisons (`publish.py:83` **and `:106`, the alias-monotonicity guard itself**, `cycle.py:106,115`, `reservoir.py:105`) — which stay correct only if one user's history uses one format, since mixed formats order correctly by ASCII accident alone; the **training seed** (`cycle.py:147`), so a night re-run across the change is **not apples-to-apples** — accepted deliberately rather than re-pinning the seed on a value that is itself changing, and `tests/parity/` is unaffected because it seeds from its own harness; `seg_id`/`block_id` (`daylog.py:88,206`); and **C5 `training_window` lineage forks** (small today). *Verdict on `w-day5`:* **a mess, not a precedent** — `scripts/m0_smoke.py:133` writes it without ever calling `window_for`, and it breaks the total order twice (`w-day10` < `w-day5`; all `w-day*` sort below all `w2026-*`). The durable fix is therefore **one minter + one validator**, not a format preference. **(1) C12 — per-user profile** (`contracts/c12_user_profile.v0.json`, the one schema minted today): a **profile, not a settings blob** — values the *system* reads to decide its own behaviour (scheduling, fallbacks), never user-facing identity, which is input's. v0 carries **`home_tz` only**; `boundary_local_time` is named as the expected second field and deliberately **not** minted until it has a consumer (E-5 precedent). **404 on absence, no server-side default anywhere** (D17), so a user without `home_tz` is *not schedulable* — an operational alert, never a silent skip. **Auto-seeded from the first device-reported `device_tz`, never auto-updated**: a traveller's night boundary must not chase their device, which is the whole point of the FACT/POLICY split. **(2) The day-log moves to storage** — decisively because of **replay**, not tidiness: replay re-reads *prior* day-logs nightly, so a continuum-side builder re-pulls every prior day's raw records every night, O(days²) across the wire to rebuild what storage could have kept. **A premise of the launch prompt was wrong and is corrected here: there are two `render_block`s and only one moves.** The parity-locked surface is **`Profile.render_block`** (`morpheus/profiles/speed.py:89`, 1427/1427 against research goldens over 5-min description dicts) — it is *recipe-coupled* and **stays with the amplifier**; what moves is `daylog.py:183 _render_block`, the product labeled-lines renderer over C2 records, which **has never had a research golden** because the research line never materialized the 10 s/2 min schema. `morpheus/blocks.py:5-7` had already drawn this line. So the move **cannot** break research parity; the bar it must clear instead is a **differential byte-equality** against our own current output for a real window, script and result committed (DP byte-identity precedent), with continuum's local path **not deleted until that diff is green**. **(3) Watermark semantics — the session's real design work.** The window watermarks on **`ingest_time`**, not event time: this *dissolves* the late-data question rather than answering it, because `ingest_time` is assigned by storage at write, so a record can never land below a closed boundary — **late data cannot exist on this axis** (`δ`, default 60 s, covers in-flight writes racing the boundary). Content stays event-time-correct because blocks form by temporal adjacency and carry their own anchors, so a week-old backlog forms its own blocks. **`last_trained_t` advances iff the cycle reaches `published` or `skipped_no_data`** — `gate_failed`/`frozen`/crash leave it, making the next window a strict superset and turning the design-of-record's **failed-day merge structural instead of bookkeeping**. **Reprocessed records: one dialect per record, latest `ingest_time` wins**, keyed `(chunk_id, content.kind, discriminator)` — on `ingest_time` because `pipeline_version` is a *composed* string and not orderable, and on `kind` because Phase-3 proved captions and transcripts share one `pipeline_version`. **A `pipeline_version` bump is a forward-only correction**: it never repairs past weights (irreducible on an append-only chain), and the accepted named cost is that one lived moment can train twice in two dialects — suppressing that would equally suppress the correction, and the correction wins. **(4) E-2 stays a separate storage build item, but is DEMOTED from cutover blocker**: the one-dialect materialization rule is what actually fixes the double-count, so E-2 is the retraction/privacy/space primitive it always should have been. Shape recorded in the storage charter M5. **New obligation this creates:** the day-log and the reservoir are **second copies of user content**, so **M5's deletion primitives must cascade to both** — a retraction that clears `/context` and leaves a day-log standing has deleted nothing. **Blocking sub-item named for the builder, do not hand-wave it:** the within-chunk discriminator is folded into the `record_id` hash and is **not independently readable from C2**, so the build must either surface it as an additive-optional C2 field (ARCHITECTURE → schema → **both** `extra="forbid"` pydantic mirrors — the exact D17 trap) or prove `(chunk_id, kind, t_start)` unique per dialect | 2026-07-26 | [ARCHITECTURE.md](ARCHITECTURE.md) §Contracts C10/C12/C13/C14 rows + §*C10 evolved* detail block + §Ownership splits *Day-log + training-window custody*; [contracts/c12_user_profile.v0.json](contracts/c12_user_profile.v0.json) + [contracts/README.md](contracts/README.md); [storage CHARTER](services/storage/CHARTER.md) §Scope/§Time index/§OQ6-9/M5+M8+M9 · [continuum CHARTER](services/continuum/CHARTER.md) §Scope/§Contracts/§OQ9-10; [handoff/engineering.md](handoff/engineering.md) |
| D19 | **STAGE: PROTOTYPE. Nothing is set in stone — contracts included — and the docs must say so.** The founders' read is that every canvas in this repo is written in a production voice, so an agent or a new teammate reads it and builds for durability we have not earned yet. The correction is a standing posture, announced at global *and* local level (§Stage in [ARCHITECTURE.md](ARCHITECTURE.md) + [ORG.md](ORG.md), and a banner on **every** service CHARTER). **What the posture licenses:** re-cutting a contract instead of versioning it; **wiping and re-collecting** stored data instead of migrating it; deferring durability work with the reason written down. **What it does NOT license** — and this is the half that keeps it honest: skipping [ORG.md](ORG.md)'s contract-edit order, undocumented decisions, silent breakage, or "prototype" as an excuse for a thing we know is wrong. Seven calls taken under it: **(1) RETENTION = KEEP EVERYTHING**, and the *mechanism* ships even though the *policy* does not — a versioned per-store retention document that storage reads and logs, every store set to `keep_forever`, **no sweeper built**. Retention rules mark *eligibility*; a separate explicit sweep acts and writes a manifest, so a config edit can never silently delete data. This is what makes the dev/prod retention decision a config change rather than an archaeology project. **(2) STORAGE TECH: local now** (SQLite + filesystem), **Postgres + GCS later, option (c)** — metadata in Postgres, day-logs/corpora in GCS. The migration is kept cheap by one rule, not by foresight: every new store goes behind a **narrow interface** in storage from day one, exactly as continuum already did on the client side, so the swap is a backend change. **(3) The C2 `discriminator` is SURFACED** (additive-optional, `contracts/c2_processed_record.v0.json`) rather than inferred from `(chunk_id, kind, t_start)` uniqueness — the option that cannot rot, taken because contracts are not frozen in this stage. It adds no new promise: DP already rejects duplicate discriminators within a chunk (`stagegraph/executor.py:396-401`), and `record_id` is unchanged, so nothing re-keys. **(4) EXISTING STATE IS WIPED, NOT MIGRATED** — the fleet's stores are experiment output, not user data. *(Scope corrected 2026-07-27 after measuring: the five continuum `var_dir` sub-directories named in this row **do not exist**, so that half is a no-op, while `continuum/var/` does hold **66 GB of Phase-1/2/3 research evidence** which must NOT be deleted. The wipe is the three fleet SQLite DBs only — and it is cleanliness, not correctness, since the migrations are additive.)* Lineage restarts from base. This is what makes D18's `window_id` reformat free: no mixed-format ordering to defend, no seed-discontinuity to reconcile, and the two `w-day5` C5 rows disappear rather than needing a rule. **(5) CYCLE TRIGGER: a per-user cron at their `home_tz` boundary, interval configurable in the service** — today's human-run CLI is the prototype stand-in, not the design. **Materialization is ON DEMAND at fetch**, deliberately buying a slow first fetch to delete an entire scheduler and its failure modes. The **min-data floor** becomes a recipe knob (`min_block_chars`, in *characters of eligible block text* — Phase-3 showed recall tracks retellings per unit of text, not block count), **default 0** = today's behaviour; D18's advance-only-on-publish rule makes a below-floor night carry forward for free. **(6) C5 FREEZE DEFERRED** — its only consumer is inference (C6 resolve), which we are not building; continuum's local `entries.jsonl` carries the lifecycle meanwhile. Cost is zero *because* C5 is unfrozen, so `training_window`'s D18 format change costs nothing now. **One standing note for whoever freezes it: `training_window` must be frozen as an OPAQUE token, never as a date**, or the parsing D18 just deleted grows back. **(7) `home_tz` IS DECLARED, NOT INFERRED** — this **overturns D18's own first draft**, which had storage auto-seed it from the first device-reported `device_tz`. The user sets it; a client may *suggest* the device zone in a UI, but a guess is never stored as though it were an answer. It follows that **`home_tz` does not move when the user travels** — a week in Tokyo changes every record's `device_tz` and changes nothing here, so the night boundary stays put instead of jumping 9 h and producing a 15 h night followed by a 33 h one. That is precisely the FACT/POLICY split of D17 doing its job | 2026-07-27 | [ARCHITECTURE.md](ARCHITECTURE.md) §Stage + §Contracts (C2 `discriminator`, C12); [ORG.md](ORG.md) §Stage; every `services/*/CHARTER.md` banner; [storage CHARTER](services/storage/CHARTER.md) §Retention + §Scope · [continuum CHARTER](services/continuum/CHARTER.md) §OQ10; [handoff/engineering.md](handoff/engineering.md) |
| D20 | **The exit bar for the storage↔continuum cutover — and a definition of "done" that can actually be met.** Two parts. **(a) M9's parity bar NARROWED, after the first run failed it.** The bar as first written contradicted D18's own materialization rule and no code could satisfy both: continuum's `seg_id` is `floor((t − window_start)/segment_seconds)` over an EVENT-time origin, while D18 deletes the window origin from storage's grid and puts the window on the INGEST axis, where a backlog record yields a negative index. The narrowed bar has three tiers — **byte-identical** (block `text`, ordering, `block_id`, `anchors`, `quality`, segment payloads: the artifact that trains the model) · **proven-equivalent** (`seg_id`: an order-preserving bijection with per-block membership preserved, *measured* not assumed — it is written to `segments.jsonl` and read by nothing, since the trainer consumes `blocks.jsonl`) · **excluded** (`content_fingerprint`, which hashes `seg_id` and is a cache key compared only to itself; forcing it to match would make the cache lie). The general rule this instantiates, now pinned in §Ownership splits: **storage owns the day-log's REPRESENTATION outright; its CONTENT is a contract neither service may move alone** — *if the trainer can see it, it is contract; if only storage can see it, it is storage's*. **(b) "Golden" DEFINED**, because the founders' wipe gate ("no defects, no artifacts") is unfalsifiable as written and would justify reviewing forever: **all four suites green · the M9 proof green over a REAL, misaligned window origin · the live two-process seam green with ZERO blockers · one adversarial round returning nothing high-severity.** Hit that and we wipe and take the first clean run; anything found afterwards is a follow-up slice, not a hold on the cutover — because past that point the honest way to find the remaining bugs is to run the thing on real data, not to review it a fourth time | 2026-07-27 | [ARCHITECTURE.md](ARCHITECTURE.md) §Ownership splits *Day-log: representation vs. content* + §Contracts *C10 evolved*; [storage CHARTER](services/storage/CHARTER.md) M9; `services/storage/scripts/daylog_parity_diff.py` · `services/continuum/scripts/seam_check.py`; [handoff/engineering.md](handoff/engineering.md) |

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

- 2026-07-09 (capture M0 built): **learn-loop capture M0 built, integrated & independently
  verified.** A 4-workstream fan-out (storage/data-processing/recording/platform) built M0 against
  the frozen C1/C2; an integrator wired them and drove one continuous-capture chunk **end to end on
  live ports** (carve WAV → `/raw` blob-first → C1 push → `/ingest` → mock ASR → C2 → `/context`),
  and an adversarial verifier re-ran the suites + re-drove the loop. **62 tests pass** (storage 26 ·
  data-processing 9 · recording 27); idempotency proven on both legs (same `chunk_id` → no dup
  blob/record); C1+C2 schema-valid E2E; the optional **real faster-whisper** leg genuinely ran once
  (restored to mock). **Zero seam fixes** — the frozen wire interoperated first try. Committed by
  this founders' session (no agent commits). Honest residuals feed capture M1: **gap-detection is
  emit-side only (not enforced)**, no consent gate, mock+file-source (no real mic). Detail:
  [handoff/engineering.md](handoff/engineering.md) "Learn-loop capture M0 — build result".

- 2026-07-10 (modality seam): **data-processing made modality-agnostic** so parallel sessions can
  each own a modality. DP refactored to a core + `Processor` plugin seam (self-registering,
  **one file to add a modality, zero core edits**; `process()` returns a **list** so one chunk → many
  records is native); audio moved behind the seam unchanged (`record_id` byte-identical);
  image/video/text **stub** processors + fixtures; recording carver generalized to a `ChunkSource`
  seam. **All 4 `content.kind`s proven E2E to `/context`** (incl. video's 3-keyframe fan-out),
  verified live + adversarially (**84 tests**: storage 26 · DP 24 · recording 34). The verifier
  caught a real live regression — DP's `/ingest` reshape (`record_id`→`record_ids[]`) 500'd
  recording's `/capture/run`, masked by stale test fakes — **fixed + re-verified 200 live**. Two
  C2-additive gaps surfaced (video per-keyframe timing, image OCR bbox) — **both deferred to the
  modality sessions, no version bump; frozen C2 untouched.** Detail + seam handoff:
  [handoff/engineering.md](handoff/engineering.md) "Modality seam".

- 2026-07-18 (return sync): **repos pushed + docs trued up** after the 2026-07-10→07-17 gap (no
  repo changes during it; the cluster ran Gnandeep's continuum-side model-stabilization
  experiments throughout — no conflict, product work keeps to node-7). Pushed: umbrella `main`;
  `live_stream_stability` (June Phase-3.1/3.2 work committed: replay-mixture tooling, eval
  harness, frozen holdout, Day-0 baseline rows, `phase_N` dir renames); `recursive_finetuning_
  stability` (`phase-3-recursive-loop` — 20 commits, Phases 1–3 + the running V4 matrix —
  pushed and fast-forwarded into `main`). `poc/live_video_chat` brought under umbrella tracking
  (+ post-V0 addendum in its HANDOFF); `start.md` committed; root `.gitignore` + rewritten root
  `README.md` added. Stale service canvases synced to reality (inference real-model closure;
  storage/recording integration + seam state; ARCHITECTURE/ORG ratification remnants). Serve
  fleet on node-7 verified **down** — the week-old "Live now" note was stale; nothing to tear
  down. **D12** (branching + beta model) recorded; next slice pinned: **recording-led capture
  M1** (gap-detection + ASR pipeline priority).

- 2026-07-18→19 (recording-led capture M1 + computer surfaces): **the recording service is
  wrapped to the alpha bar.** M1 built the checked "zero silent loss" guarantee (SQLite
  continuity ledger + a DP-side break/dup detector on `/ingest` + a two-leg gap report with a
  `clean|gaps|recording` verdict), the **fuller ASR pipeline** (faster-whisper standing with a
  VAD gate that turns silence into an honest empty transcript; diarize/translate/acoustic-event
  stubs behind the Processor seam), and **VAD-cut variable chunking** (OQ4 → D-M1-2). Then
  **three real capture clients** landed behind the same `/capture/*` wire (client wire renamed
  from `/ingest/*` so `/ingest` is uniquely DP's C1 receiver): **phone web** (mic+camera),
  **Chrome-MV3 extension** (passive active-tab capture — pivoted to `tabCapture` per D-E7 after
  the desktop picker proved fragile on real browsers), **mac CLI** (ffmpeg avfoundation). Each
  demuxes to per-modality C1 streams; **zero server changes for the two new clients** (the wire
  is client-agnostic, by design). **ALPHA COMPLETE 2026-07-19** — all three verified `clean`
  end-to-end on real hardware (blobs sha256+ffprobe-checked in storage, real ASR transcripts in
  `/context`). Multiple adversarial review rounds + a fresh-eyes runbook-accuracy pass hardened
  it (110 recording tests). **D14** (segmented-HTTP transport; streaming ingest deferred additive)
  recorded. Detail: [services/recording/HANDOFF.md](services/recording/HANDOFF.md) +
  [alpha-runbook](services/recording/handoff/alpha-runbook.md).

- 2026-07-19 (post-alpha sequencing): **DP-led deep session launched in parallel** (branch
  `svc/dp-async-observability`, worktree `~/nmn/cl-dp-async`) to execute **async `/ingest`**
  (DP charter M7 arriving early), **D9 metrics emission** (DP M8 + recording M6 — emission
  half; Platform's backbone follows), **node-7 smokes of the real audio backends**
  (pyannote/whisper-translate/AST), and the OQs the work answers (headline: recording OQ3
  codec ladder, joint; DP OQ13 resolved by the slice). The founders' session pinned the
  **ratification bar for the async `/ingest` reply shape** (inter-service wire, not a
  C-number; escalation row open above) and recorded **D15**: continuum kickoff next (C10 v0
  freeze as its gate) + Platform D9 backbone as the small parallel slice; DP image/text
  deferred until a producing surface exists. Learn fleet re-verified healthy on node-7.
  *Later same session:* the deep session's FINAL async-`/ingest` design memo arrived
  (five-reviewer verified; code claims spot-checked) and was **RATIFIED → D16** — the memo
  strengthened the bar's headline clause into the non-negotiable `dp_acked`-invariant fix;
  one condition (re-drive drill) + one accepted caveat (202-path provenance) recorded.
  *Later still:* **the slice landed + merged (`0ce4941`; `dev` fast-forwarded with it).**
  Founders' merge review re-ran all three suites independently (**98/120/26 green**) and
  verified the D16 condition + OQ3/OQ13 records in the diff. D15 is now the active sequence.

- 2026-07-20 (DP v1): **the DP team shipped v1 — durable ingest journal + stage-graph
  pipeline** (`86acb95`, single clean commit, pushed; `main`=`dev`=origin verified). Layer A
  journals async accepts BEFORE the 202 (kill -9 auto-recovers at startup; continuity
  rehydrates from the journal → **the D16-era deferred false-`gaps` caveat is CLOSED**;
  durable dedup backstop with a `pipeline_version` staleness check — receipts written in
  BOTH modes, so inline gains restart-safe dedup too; epochs guard stale workers; bounded
  per-attempt re-drive breaks crash-loops visibly). Layer B turns every processing step into
  a **drop-in stage file** (readiness DAG runs independent stages concurrently; composed
  `pipeline_version` where a mutate stage's enabledness IS its version fragment — the
  silent-overwrite class dies by construction); audio+video ported byte-identically, real
  backends re-validated through the graph on node-7. Two adversarial rounds (9 confirmed →
  2 fix-before-merge fixed). Founders re-verified: **DP 128 · recording 120 · storage 26
  green**, refs + attribution-free commit + off-by-default knobs + the fairness-knob startup
  warning all checked in code. (The 3 tracked v1 follow-ups — `INGEST_MODALITY_LIMITS`
  HOL-block, mutate-overlap race, order-dependent fingerprint guard — were then **closed by
  the hardening slice below**, so the v1 caveat drill was overtaken by that work rather than
  held separately.)

- 2026-07-21 (DP hardening): **the DP deep session shipped a hardening slice + merged it**
  (`5350f7a`, conflict-free merge carrying the founders' `aaebd88` board-sync; `dev` at the
  raw tip `13bad86`; pushed; DP trees identical between `main` and `dev`). It **closes all 3
  v1 review findings by construction, not by patch**: (1) a **SlotView capability proxy** —
  a sidecar can't even READ the primary's mutable slots, illegal writes raise synchronously
  at the offending line (the order-dependent end-of-run fingerprint guard is *deleted*);
  (2) mutate **`writes` + deterministic overlap chaining**, with the chain order folded into
  `pipeline_version` (a future second mutate like speaker-ID composes on diarize, can't race
  it); (3) a **permit-at-dispatch** queue rewrite — the modality-fairness knob no longer
  head-of-line-blocks, so `INGEST_MODALITY_LIMITS` is production-safe and the EXPERIMENTAL
  warning is gone. Plus a **new containment layer**: opt-in `INGEST_ISOLATION=subprocess`
  runs each chunk's Processor in a killable child (a segfault/native-OOM/`os._exit` in model
  code kills ONE chunk, not the service; a drain-cancel SIGKILLs the ghost compute a
  threadpool can't). A **47-agent adversarial round** (5 dimensions → 2 refuters/finding)
  confirmed 19 / refuted 2 → 9 code fixes + 7 gap drills, catching two HIGH bugs *in the new
  code* (a reproduced retry-starvation in the new dispatch; an event-loop stall in spawn
  isolation). Byte-identity re-proven empirically (identical C2 digests vs `main` across
  dialects AND under isolation). Founders re-verified independently: **DP 163 · recording
  120 · storage 26 green**; merge topology + attribution-free commits + off-by-default knobs
  checked. The ws file also carries a full **M0–M8 milestone eval** (M0/M3/M7-core/M8 done;
  **M1 exit open** — no denoise stage + WER/DER baseline unmeasured; **M2 text/image is the
  next unstarted charter work**; M4/M5/M6 not started) and a **sync-path decision: KEEP
  inline** — it's the C8/M6 skeleton and the byte-identical verification baseline; flipping
  the async production default stays a founders' call after the **D16 re-drive drill** (still
  the one open gate). Detail: [ws-dp-hardening](services/data-processing/handoff/ws-dp-hardening.md).

- 2026-07-25 (continuum — **THE LEARN LOOP IS CLOSED**): the D15 continuum kickoff ran to
  completion. The nightly-consolidation core (**"Morpheus"**, our nomenclature; methods
  reimplemented cleanly from the research consolidation line `b3c58e1`, parity-proven by a
  differential harness — `render_block` byte-identical, LoRA targets 252/252, judge exact,
  ensemble indistinguishable at n=8/10, p=0.82) sits behind a `TRAINER_BACKEND` seam (mock
  default). **M0 met:** a 32B life adapter *our* pipeline trained → publish-gate v1.1 → C5 →
  **served in vLLM** (32B training needs ≥2 GPUs — a measured hard limit). Continuum was slimmed
  to a lean **5-verb loop** (fetch recipe · fetch day-log · amplify · finetune · gate · publish)
  over three storage **client seams** (local now, HTTP-to-storage later). Then the **Phase-3 DP
  dogfood** routed real Speed data (209.7 h of audio, 629 chunks) through the **actual
  recording→DP→storage→continuum services** — a replay `ChunkSource` + an injected-caption DP
  sidecar (~2 net-new files, no contract changes; test-type = config profile + `replay-speed`
  naming, NOT a contract field). The 1-min rule-bend collapsed recall on **dose** (fixed 48
  retellings now spread over 4.1× the block text); the **decomposition run with parity block
  content reproduced the baseline separation** (0.137 vs 0.179, permutation p=0.148 — same
  distribution; p=0.018 above the no-consolidation control). **Verdict: PIPELINE SOUND — our real
  services carry the learn loop without losing learnability.** Suites green: continuum 185 ·
  storage 26 · recording 133 · DP 173. Detail: continuum canvas + [ws-morpheus-port](services/continuum/handoff/ws-morpheus-port.md) · [ws-phase3-dogfood](services/continuum/handoff/ws-phase3-dogfood.md).
  **Two founder-level follow-ups, neither an integration defect:** (a) a **recipe/dose finding for
  Gnandeep** — amplification dose is fixed *per block* but recall depends on retellings *per unit
  of text*, so at our native cadence dose must scale with block-text volume (cofounder to raise);
  (b) a **storage/C10 board session** — ratify the storage charter expansion (day-log
  materialization + recipe registry + reservoir custody) and the **C10 evolution** from a raw
  range-read to a **day-log fetch, random-access by `(user, window_id)`** (six cross-service
  friction notes captured in the Phase-3 report; new recipe-registry + reservoir contract IDs
  minted at ratification). **Gate policy v1.1** (traps ≥0.15/0.25, heldout exact-test vs each run's
  own base control, `min_probes` 148) was split from the training recipe so a threshold change
  never forks `recipe_id`.

- 2026-07-26 (founders' engineering session — **timezone: decided AND built, D17**): the
  2026-07-26 accuracy review's open item **O-1** ("timezone ownership is unowned") is **CLOSED**,
  and the fix shipped in the same session. Verified first: `context_records` really had no tz
  column (`storage/app/db.py`), the wearer's tz really was a CLI flag defaulting to `"UTC"`
  (`continuum/app/nightly.py:27`), and — the fact that decided the design — **the capture clients
  already computed the local instant and threw the zone away on the same line**
  (`clients/web/app.js:262`, `clients/extension/uploader.js:110`: `new Date(...).toISOString()`).
  C1 also already collected `device_location`, and C2 **dropped it on the floor**; both
  `device_location` and `device_clock` were declared in recording's and DP's `models.py` and read
  by **neither**.

  **Decision (D17 above): the device owns the fact, storage owns the policy.** The first draft of
  D17, taken earlier the same day, was **wrong and is superseded**: it gave storage a per-user tz
  *only*, banned tz from C1/C2, and deferred the travel case — applying the emission law's T2 as a
  *veto* on a field whose consumer this very slice builds, when T2's own text calls it "a gate on
  *when*, not a veto". The correction came from the CTO's read: the capturing device is the only
  thing that can know where the user was, and it already knows.

  **BUILT end to end, all four services + three clients** (contracts first, per ORG.md:44-45):
  C1 gains `device_tz` + `device_utc_offset_minutes` (**additive-optional; `required` untouched on
  both schemas — re-validated**); the three capture clients emit them (`Intl…resolvedOptions()
  .timeZone` in the browser clients, an `/etc/localtime` symlink read in the stdlib-only mac CLI),
  with the **offset evaluated at each chunk's own instant so a DST flip is carried honestly**;
  recording validates at the edge (**an abbreviation like `PST` is a 400** — ambiguous and
  DST-sensitive), persists to the ledger, and carries it into C1 **including on the `/redrive`
  path**; DP passes all three fields (incl. the long-dropped `device_location`) **verbatim** into
  C2 `source{}` with zero timezone logic; storage promotes them to columns beside the UTC instant
  **and still serves the C2 back byte-verbatim**; continuum's `_block_zone` renders each block in
  the **device's** zone, falling back to the window's `home_tz`, degrading (never raising) on a bad
  id. Both SQLite stores got **additive ALTER migrations with deliberately NO backfill** — a record
  captured before clients reported a zone genuinely has none, and inventing one is the exact
  failure this slice removes.

  **Cross-service E2E verified:** a Tokyo-captured chunk driven through
  recording → DP → storage → continuum with the operator's fallback set to UTC renders
  **"around 15:00 local time"**. The same run pre-D17 rendered **"06:00"** — a UTC clock reading
  *labelled* local, with no error and no metric.

  **Suites all green, +26 tests, zero regressions:** storage **32** (was 26) · continuum **189**
  (was 185) · recording **144** (was 133) · DP **770 + 21 skipped** (was 765) · extension deno
  **11** (was 10). New coverage includes the travel case (two zones in one window rendering
  independently, with different local *dates*), both migrations, edge rejection of abbreviations,
  and a test pinning that **civil-time context does not change `record_id`** — provenance must not
  fork the dialect, or every existing record would re-key on upgrade.

  **Also taken:** `nightly.py --tz` is now **required** (no default timezone anywhere).
  **Open questions closed by this work:** storage **OQ3** (clock skew), DP **OQ4** (device clock
  discipline), DP **OQ9** substantially (envelope time is enough — and now *auditable*), continuum
  **OQ10**'s timezone half, and **E-4**'s premise. **Specified but deliberately NOT built** (they
  are the storage/C10 board session's own agenda, not something to slip in unreviewed): moving
  day-log materialization from continuum to storage, and switching the cycle window to the
  watermark range `[last_trained_t, now)` — both now written into the ARCHITECTURE C10 row.

- 2026-07-26 (founders' storage/C10 board — **D18: the day-log moves, the window becomes a
  watermark, `window_id` stops meaning anything**): the two items queued twice — by the 2026-07-25
  learn-loop close-out and by D17 — were ratified together, and **all of it is DECIDED, NOT BUILT**
  (the D17/O-12 discipline, applied from the start rather than corrected into the row afterwards).

  **Verified before deciding, because the gate demanded it:** all five `window_id` claims hold —
  it is a path component under `ids.py:8`'s regex (which a raw RFC3339 instant fails, on colons),
  a string-compared total order, the training seed (`cycle.py:147`), embedded in `seg_id`/`block_id`,
  and C5 lineage. **Three more the launch prompt did not list:** `publish.py:106` is a **fourth**
  string comparison — the alias-monotonicity guard itself, not just `active_before` — and
  `window.py:44` + `reservoir.py:65-69` **parse the id back into a date**, which `cycle.py:217` then
  uses to rebuild *prior* windows under *tonight's* timezone.

  **One premise of the launch prompt was wrong.** Its HARD CONSTRAINT ("`render_block` must stay
  byte-parity with the research line; moving the renderer must not break it") conflates two
  different functions. `tests/parity/test_render_block.py` locks **`Profile.render_block`**
  (`morpheus/profiles/speed.py:89`) over 5-min description dicts — recipe-coupled, and it **does not
  move**. What moves is `daylog.py:183 _render_block`, the product renderer over C2 records, which
  **never had a research golden** (the research line never materialized the 10 s/2 min schema).
  `morpheus/blocks.py:5-7` had already drawn the boundary. The move therefore cannot break research
  parity — so the bar became a **differential byte-equality** against our own current output, which
  is a stronger and more falsifiable test than the one that was asked for.

  **Decided (full text in D18 above):** `window_id` → `w<YYYYMMDD>T<HHMMSS>Z`, minted once from the
  window's **end instant**, parsed by nobody, with one minter + one validator (because `w-day5` in
  `m0_smoke.py:133` proves the ordering invariant is only as strong as the discipline that mints
  ids) · **C12/C13/C14 minted**, C12's schema written · the day-log moves to storage, decisively
  because **replay** would otherwise re-pull every prior day's raw records every night (O(days²)) ·
  the window becomes `[last_trained_t, now−δ)` on **`ingest_time`**, which dissolves late data
  instead of handling it, and `last_trained_t` advances **only** on `published`/`skipped_no_data`,
  making the failed-day merge structural · **E-2 demoted** from cutover blocker, because the
  one-dialect materialization rule is what actually fixes the double-count.

  **Two new obligations this session created rather than closed:** deletion must now **cascade to
  the day-log and the reservoir** (each is a second copy of user content — M5 widened), and the
  **within-chunk discriminator is not readable from C2**, which the build slice must resolve before
  the materializer starts. Also corrected: "storage OQ8" (blob-by-reference) does not exist — that
  is **recording's** OQ8, and the mislabel had propagated from `ws-phase3-dogfood.md:55` into this
  session's own launch prompt.

  **No code changed. Suites unchanged and unrun this session** (baselines stand: storage 32 ·
  continuum 189 · recording 144 · DP 770 +21 skipped · extension deno 11).

## Next

- ~~**NEXT SESSION IS PREPPED:** the storage/C10 board launch prompt~~ **RUN 2026-07-26 → D18.**
  All four decisions taken plus E-2's disposition; the prompt's `window_id` gate was verified (and
  three further consumers found), and one of its premises — the `render_block` parity constraint —
  was corrected. Prompt kept at [handoff/next-session-storage-c10.md](handoff/next-session-storage-c10.md)
  for provenance.
- ~~**NEXT FOUNDER ACT — the storage build slice**~~ **BUILT 2026-07-27** (`a5a48fb` storage ·
  `1757efb` continuum · `2698b63` data-processing). D20's bar met and verified by the founders'
  session, not relayed: storage **310** · continuum **251**+7 · recording **144** · DP **788**+21 ·
  M9 parity **PASS over 2 origins incl. misaligned** · live seam **PASS, 151 checks, 0 blockers**.
  Four adversarial rounds ran and every one found something real — the sharpest being that the seam
  showed 148 green checks *while the shipped default trained on nothing*, because the harnesses
  proved the paths they exercised and the default was not one of them.
- **THE WIPE (D19) is the remaining act**, gated on D20 and on a final adversarial round. Continuum's
  `var_dir` and node-7's learn-fleet DBs are experiment output, not user data; lineage restarts from
  base, which is what makes D18's `window_id` reformat free. Back up first via SQLite's
  online-backup API (D17 precedent). **Expect, do not debug:** after the wipe a user has no
  `home_tz`, and D19 removed auto-seed — so the first run needs it set explicitly or that user does
  not consolidate. Visible by design.
- *Superseded — the original build-order note:* Order is forced
  by dependencies, not preference: **(1) C12 profile** — day-log materialization reads it, so it
  lands first; **(2) resolve the discriminator sub-item** (additive C2 field vs `(chunk_id, kind,
  t_start)` uniqueness) before any materializer code; **(3) the window ledger + `window_id` minter**
  (one minter, one validator, `m0_smoke.py` moved onto it); **(4) day-log materialization**, whose
  exit bar is the **differential byte-equality diff** against continuum's current output — and
  continuum's local path is not deleted until that diff is green; **(5) C13/C14**, then continuum's
  cutover (`window_for` deleted, `--tz` replaced by the C12 read, `LocalDayLogClient` retired).
  E-2 rides M5 whenever it is scheduled — it no longer gates the WS-VC cutover.
- **D17 follow-through** (the tz path itself is BUILT + verified; these are the pieces that
  belong to the board, not to a single session): at the **storage/C10 board session** — (1) mint
  the **per-user profile** contract (`home_tz`) alongside the recipe-registry + reservoir IDs;
  (2) move **day-log materialization** continuum → storage; (3) switch the cycle window to the
  **watermark range `[last_trained_t, now)`**, retiring `window_for`'s local-date arithmetic — all
  three are specified in the ARCHITECTURE C10 row + the storage charter. Then at the **C5 freeze**,
  stamp the resolved tz into the run report + C5 entry (today `training_window:"w-day5"` records
  nothing about the zone it was derived under, so a wrong-tz adapter is unfalsifiable after the
  fact).
- **C5 freeze — inherited constraint (from review item O-2, 2026-07-26; C5 deliberately NOT frozen
  that session).** C5's four descriptions are now correct and explicitly labelled *"as built, not
  frozen"* — nine fields, and `status` ∈ `active | gate_failed | rolled_back`. The freeze session
  inherits one thing that is **more than documentation**: `gate_failed` (the audit row for a
  candidate the gate blocked) constrains the storage build, because storage's `model_directory` is
  still only the trivial C6 row (`user_id, model_id, adapter, adapter_path`) — no entries log, no
  status column, so hosting C5 is a build, not a transport swap. Three constraints, written into
  the [storage charter](services/storage/CHARTER.md) model-directory row: a **three**-value status
  enum (or `record_gate_failure()` has nowhere to land); **nullable** `adapter_dir` +
  `base_model_hash` (gate-failed rows carry NULLs there); and C6 eligibility as a **log replay**,
  not "latest row wins" — else a gate-failed candidate becomes servable, the exact ungated swap the
  gate prevents. Smaller + unblocked: **E-4**'s per-fragment local timestamps are now a pure continuum
  renderer change; and DP **OQ9**'s residual — a `/metrics` counter on `unsynced` chunks +
  `|ingest_time − t_end|` outliers, so clock skew is *measured*, not just detectable.
- ~~**Fleet note (D17):** node-7's DBs predate the new columns~~ **DONE 2026-07-26 — the learn
  fleet was restarted onto the new code and the migrations are applied.** Both live DBs were
  backed up first via SQLite's online-backup API (`/home/ubuntu/nmn/backups/pre-d17-20260726-211912/`).
  Verified after restart: `context_records` gained `device_tz` + `device_utc_offset_minutes` with
  **125/125 rows intact and all NULL** (no backfill, as designed — a record captured before clients
  reported a zone genuinely has none); `segments` gained both columns with **40/40 rows intact**.
  The long-pending **`dp_state` migration also ran**, correctly backfilling all **68 chunks** to
  `processed` — the running fleet had predated the D16/v1/hardening merges since 2026-07-19, so
  this restart collected those too. Live checks: the capture wire **accepts** `device_tz=Asia/Tokyo`
  and persists it, and **rejects `PST` with 400** ("must be an IANA zone id"); a real `--smoke`
  capture run carved 3 chunks through faster-whisper ASR → C2 → `/context` (headless `/capture/run`
  has no reporting device, so those records correctly **omit** the fields rather than nulling them).
  Verification rows were removed; ledger is back to its 40-segment baseline. All three services
  healthy (storage 8083 · DP 8085 · recording 8084). **Still off by default:** `INGEST_ASYNC`,
  `INGEST_ISOLATION`. The serve loop (vLLM + app services) remains down.
- ~~Recording-led capture M1~~ **DONE + ALPHA COMPLETE 2026-07-19** (see Current state above /
  the recording canvas). Gap-detection enforced, ASR pipeline standing, three capture surfaces
  verified `clean` on real hardware. Consent gate stayed back-burner per D13.
- ~~DP-led deep session~~ **DONE + MERGED 2026-07-19 (`0ce4941`, founders' review passed):**
  async `/ingest` behind `INGEST_ASYNC` (off = inline byte-identical; **D16 wire implemented
  verbatim incl. the re-drive condition** — `/capture/sessions/{id}/redrive` + emitter re-push
  + 2 drill tests) · D9 emission on BOTH services (`/metrics` + dashboard JSONs, zero new
  deps) · all 3 real audio backends smoke-tested GREEN on node-7 (+2 real pyannote fixes) ·
  OQ13 resolved + **OQ3 answered per-modality** (no ladder: 16 kHz mono audio is model-native;
  video container-copy — resolution-bound not bitrate-bound, ~2560 px only for OCR-heavy
  screens; cost dial = keyframe cadence). Suites re-verified independently by the founders'
  session: **DP 98 · recording 120 · storage 26**. Honest residuals (ws file): DP-restart
  false-`gaps` window fails SAFE (**now CLOSED by the v1 durable journal, 2026-07-20**);
  whisper-translate unproven on a genuine non-English source; pyannote pinned 3.1.1, smoked
  3.3.2. *(This slice was superseded by DP v1 + hardening — see the current-state entries
  above; residuals tracked there.)* **Fleet note:** node-7
  still runs pre-merge code — restart `run_learn.sh` at convenience to start emitting
  `/metrics` (async stays off by default; flipping `INGEST_ASYNC=1` retires
  `RECORDING_HTTP_TIMEOUT=120`).
- **Now (D15):** (1) **continuum kickoff** — the next founders-led slice; first act:
  storage × continuum propose the **C10 v0 freeze** (founders ratify), then a charter-M0 plan +
  workstreams. Kickoff deliberately forces the cluster-split (nightly window) and DP
  reprocess-policy (OQ5) conversations; the parked **D6 OCR spot-check** rides the vLLM
  relaunch continuum-era eval needs anyway. (2) **Platform D9 backbone** as the small parallel
  slice — the one shared Prometheus + Grafana scraping the new `/metrics`, provisioning both
  dashboards + node/dcgm exporters, closing D9 end-to-end. Image/text DP pipelines stay
  **deferred until a producing surface exists** (D15).
- **Beta hand-off (D12):** standing `dev` branch forked from `main` for Gnandeep — serve loop
  (mock or real backend) + learn loop (real faster-whisper ASR, `ASR_LANGUAGE=en`) both run today;
  storage's `/context` range read is his training-window feed for the black-box fine-tuning tests
  until C10 lands. The three capture clients (`/capture/*` wire, tunnel URL from
  `services/recording/var/tunnel_url.txt`) are the beta's data-collection front door.
- CTO to read the Platform charter internals when time allows (D1).
- **Fleet status (2026-07-19):** the **learn loop is UP on node-7** — storage:8083 ·
  data-processing:8085 (`ASR_BACKEND=faster_whisper`, `ASR_LANGUAGE=en`) · recording:8084, plus
  the cloudflared tunnel for the capture clients (URL rotates per restart →
  `services/recording/var/tunnel_url.txt`); `run_learn.sh --status` checks it. The **serve loop
  (vLLM + app services) is down** — relaunch `run_all.sh` + `services/inference/serve_vllm.sh`
  when needed. The wider cluster runs Gnandeep's continuum-side experiments — product work keeps
  to **node-7**; allocate more nodes on demand. *Learn loop re-verified up by the 2026-07-19
  sequencing session. Post-merge: the running fleet predates all three DP merges (`0ce4941`
  async, `86acb95` v1, `5350f7a` hardening) — restart to start emitting `/metrics` + gain the
  durable journal + isolation knob (behavior otherwise unchanged; `INGEST_ASYNC` +
  `INGEST_ISOLATION` both off by default). WHO restarts DP (supervisor/deploy) is an open M7
  ops item with platform.*
