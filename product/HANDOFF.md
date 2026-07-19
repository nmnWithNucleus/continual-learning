# HANDOFF — founders' working canvas (whole company)

> The single touch-point for the founders (CTO + AI co-founder) and the top of the
> escalation path. Read this first in any founders' session, then the aspect file you're
> working ([handoff/](handoff/)). Stable docs: [VISION.md](VISION.md) ·
> [ARCHITECTURE.md](ARCHITECTURE.md) · [ORG.md](ORG.md) · [PROMPTS.md](PROMPTS.md).
> Service-level state lives in each service's own HANDOFF.md — this board links, not restates.

**Last updated:** 2026-07-19 · maintained across founders' sessions.

---

## Service status board

| Service | Status | Lead session | Canvas |
|---|---|---|---|
| Recording | **capture M1 + computer surfaces — ALPHA COMPLETE** (checked gap-detection + VAD-cut chunking + 3 capture clients: phone web / Chrome-MV3 extension / mac CLI, all verified `clean` on real hardware — 2026-07-19; 110 tests) | recording M1 → computer-capture | [canvas](services/recording/HANDOFF.md) |
| Data Processing | **real audio + video pipelines landed** (parallel modality fan-out 2026-07-19: diarize/translate/acoustic stages behind off-by-default switches; real ffmpeg keyframes + per-keyframe timing + OCR weave behind `VIDEO_BACKEND`, genuine Qwen3-VL-8B run; independently verified + 4 fixes; **72 tests**) | audio + video leads → integrator | [canvas](services/data-processing/HANDOFF.md) |
| Storage | **v0.0 + capture M0 built + integrated E2E** (serve loop + `/raw`/`/context` mock capture loop 2026-07-09) | serve + learn | [canvas](services/storage/HANDOFF.md) |
| Input | **v0.0 built + mock loop runs** (integrated E2E 2026-07-09) | serve-loop WS-A | [canvas](services/input/HANDOFF.md) |
| Inference | **v0.0 live on real Qwen3-VL-32B** (vLLM TP=8 on node-7, verified E2E 2026-07-09) | serve-loop WS-B | [canvas](services/inference/HANDOFF.md) |
| Output | **v0.0 built + mock loop runs** (integrated E2E 2026-07-09) | serve-loop WS-C | [canvas](services/output/HANDOFF.md) |
| Continuum | chartered — awaiting kickoff | — | [canvas](services/continuum/HANDOFF.md) |
| Platform | **v0.0 serve bring-up + learn-loop bring-up** (`run_all.sh` + `run_learn.sh`, both run E2E 2026-07-09) | serve + learn | [canvas](services/platform/HANDOFF.md) |

## Founders' aspect threads

| Aspect | File | State |
|---|---|---|
| Engineering | [handoff/engineering.md](handoff/engineering.md) | active — serve-loop v0.0 **closed on real Qwen3-VL-32B**; capture M0 + modality seams done; **recording-led capture M1 + computer capture surfaces DONE (alpha complete 2026-07-19)**; next: **metrics emission (D9)** |
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
| D12 | **Branching + beta model.** Service work happens on branches off `main`, merged once coded + tested at a decent revision. A standing **`dev` branch (forked from `main`) is the beta playground** handed to testers — it may carry beta-only conveniences, never contract changes. First beta hand-off: the two proven loops (serve + learn) to Gnandeep, who drives them against his externally-stabilized fine-tunable model; storage's `GET /context/records?user_id=&from=&to=` range read is his training-window feed until C10 lands | 2026-07-18 | this board; [handoff/engineering.md](handoff/engineering.md) worklog; root `README.md` §Branches |
| D13 | **Consent gate de-prioritized (back-burner).** Ship-fast posture: the capture surfaces + learn loop mature first; the consent/deletion layer (recording M2 + platform's consent store) lands **before any non-team pilot user**, not before beta (beta testers are consenting teammates). The M2 red-team exit bar is unchanged whenever it lands | 2026-07-18 | this board; recording charter §v0 deliverables |
| D14 | **Capture transport = segmented HTTP upload for ALL v0 surfaces** (phone / extension / mac CLI). Our capture path is the loss-intolerant, offline-resilient *archive/training* job (the Axon-bodycam pattern), not low-latency live-view (the Ring/Nest pattern — which runs both paths separately). **Continuous streaming ingest (WebSocket/RTSP/SRT → server segmenter) is a deferred ADDITIVE leg** terminating in the existing spool→demux→carve→emit machinery; C1/C2 unchanged (C1 begins after transport). Live-view is out of v0 scope | 2026-07-19 | recording canvas §Pinned decisions (D-M1-5); [ARCHITECTURE.md](ARCHITECTURE.md) capture path |

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

## Next

- ~~Recording-led capture M1~~ **DONE + ALPHA COMPLETE 2026-07-19** (see Current state above /
  the recording canvas). Gap-detection enforced, ASR pipeline standing, three capture surfaces
  verified `clean` on real hardware. Consent gate stayed back-burner per D13.
- **Now: metrics emission (D9)** — the founders' sequenced next, unblocked now that the capture
  surfaces are human-verified solid. Each service exposes `/metrics` (Prometheus text) + ships a
  Grafana dashboard JSON; **Platform runs the one shared Prometheus/Grafana**. The buildable-now
  piece is the *emission* side across **recording + data-processing** (the two services with the
  richest new signal: ingest/segment/chunk rates, per-leg gap/continuity counters, demux + ASR
  latency, VAD-empty rate); the shared backbone is Platform's slice. A fresh session picks up the
  emission side from the recording + DP canvases.
- **Beta hand-off (D12):** standing `dev` branch forked from `main` for Gnandeep — serve loop
  (mock or real backend) + learn loop (real faster-whisper ASR, `ASR_LANGUAGE=en`) both run today;
  storage's `/context` range read is his training-window feed for the black-box fine-tuning tests
  until C10 lands. The three capture clients (`/capture/*` wire, tunnel URL from
  `services/recording/var/tunnel_url.txt`) are the beta's data-collection front door.
- Alternatives off the skeleton (unchanged): continuum kickoff (nightly LoRA), or screen/OCR
  capture (data-processing M2). **Still open:** the D6 OCR spot-check on real screen-capture
  data (needs vLLM relaunched on node-7 first).
- CTO to read the Platform charter internals when time allows (D1).
- **Fleet status (2026-07-19):** the **learn loop is UP on node-7** — storage:8083 ·
  data-processing:8085 (`ASR_BACKEND=faster_whisper`, `ASR_LANGUAGE=en`) · recording:8084, plus
  the cloudflared tunnel for the capture clients (URL rotates per restart →
  `services/recording/var/tunnel_url.txt`); `run_learn.sh --status` checks it. The **serve loop
  (vLLM + app services) is down** — relaunch `run_all.sh` + `services/inference/serve_vllm.sh`
  when needed. The wider cluster runs Gnandeep's continuum-side experiments — product work keeps
  to **node-7**; allocate more nodes on demand.
