# HANDOFF — Data Processing Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** M0 + Processor seam + capture-M1 pair + **real audio pipeline beyond ASR**
(diarization · translation · acoustic-event captioning behind off-by-default backend switches;
now **all three SMOKE-TESTED GREEN on node-7**, +2 pyannote torch-2.x fixes) + **real VIDEO
pipeline (M3, WS-V):** keyframe extraction + captioning behind `VIDEO_BACKEND=mock|vlm` +
per-keyframe timing hook + OCR weave (genuine Qwen3-VL-8B run) + **ASYNC `/ingest` (M7-early)
behind `INGEST_ASYNC`, off by default = byte-identical inline** + **D9 `/metrics` + Grafana
dashboard (M8)** + **DP v1: DURABLE ingest journal (kill-recovery + restart-amnesia closed)
+ STAGE-GRAPH pipeline (every processing step a drop-in file; audio/video ported
byte-identically, real backends re-validated through the graph on node-7)** + **HARDENING
(WS-H): all 3 tracked review findings closed (SlotView slot ownership · mutate overlap
chaining · permit-at-dispatch fairness) + opt-in subprocess isolation (poison chunk kills
one chunk, not the service; drain cancel SIGKILLs the ghost)** — capture alpha still green
(3 real clients) — **+ SCREEN-VIDEO PATH REDESIGNED (WS-VC, design only, build not started):
clip-level captioning replaces per-keyframe calls, a dedicated OCR channel, a versioned prompt
pack, and the record-vs-mutation law — [handoff/ws-video-clip.md](handoff/ws-video-clip.md)** —
**173 tests** · **Last updated:** 2026-07-24 (screen-video design session, branch `svc/continuum-phase3-decomp`)

## Workstream index
| WS | What | Status | Working file | Owner session |
|---|---|---|---|---|
| B | M0 capture skeleton: C1 → ASR → C2 (`:8085`) | built, mock tests green | this dir (`app/`, `tests/`) | learn-loop M0 |
| B+ | Modality-agnostic **Processor seam** + image/video/text **stubs** | built, 24 tests green | `app/processing/`, `tests/test_processor_seam.py` | seam session |
| M1 | **Continuity detector** (`/continuity`) + **real ASR + VAD gate** + audio pipeline stubs | built + verified live 2026-07-18 | [handoff/ws-m1-continuity-asr.md](handoff/ws-m1-continuity-asr.md) | recording M1 lead |
| A | **Real audio pipeline beyond ASR**: diarization · translation · acoustic events (off-by-default `*_BACKEND` switches; mock headless + real pyannote/whisper/AST seams) | built, 57 tests green (38+19); real backends unrun seams | [handoff/ws-audio-pipeline.md](handoff/ws-audio-pipeline.md) | audio-pipeline lead |
| V | **Real VIDEO pipeline** (M3): ffmpeg keyframes → caption (`VIDEO_BACKEND=mock\|vlm`) + **per-keyframe timing hook** (OQ14a) + OCR weave (D8) | built + verified + reviewed; real **Qwen3-VL-8B** E2E; suite **68 green** (+11 video). **Its §3 D8 caption/OCR weave + the keyframe-per-record shape are SUPERSEDED for screen content by WS-VC** (kept as the shipped, gated `VIDEO_PIPELINE=keyframe` legacy path) | [handoff/ws-video-pipeline.md](handoff/ws-video-pipeline.md) | video-pipeline lead |
| AO | **Async `/ingest`** (M7-early, `INGEST_ASYNC` off by default) + **D9 `/metrics` + dashboard** (M8) + **node-7 smoke** of the 3 real audio backends | built + tested + reviewed; DP **98 green**; recording seam updated (120 green); +2 pyannote fixes | [handoff/ws-async-observability.md](handoff/ws-async-observability.md) | async-observability lead |
| SG | **DP v1**: **durable ingest journal** (`app/journal.py` — kill-recovery + restart-amnesia closed, epochs, bounded re-drive) + **stage-graph pipeline** (`app/stagegraph/` + `app/stages/` — drop-in stage files; audio+video ported byte-identical; per-modality fairness) | built + tested + reviewed; DP **127 green**; real backends re-validated through the graph on node-7 | [handoff/ws-dp-stage-graph.md](handoff/ws-dp-stage-graph.md) | async-observability lead (v1) |
| VC | **Screen-video path (mac-app) REDESIGN** — clip-level captioning (one multi-image VLM call/chunk, keyframes retired as record identity), a deterministic CPU **OCR channel** emitting its own `kind='ocr'` record, a **versioned prompt pack** whose content digest IS the dialect, and the **record-vs-mutation law**. **DESIGN ONLY — build not started**; 8 parallel workstreams specced, all startable day one | **design ratified in-session** (43-agent fan-out: 6 ground · 12 proposals · 4 judges · 20 adversarial verifiers · synthesis; lead re-verified vLLM/OCR/dep facts + corrected 2 figures) | [handoff/ws-video-clip.md](handoff/ws-video-clip.md) | screen-video design session |
| P3 | **Phase-3 DP dogfood support** (continuum-led): `app/stages/audio/injected_caption.py` — replay-injected description captions as an audio sidecar, off unless `INJECT_CAPTION_BACKEND=index` | landed via continuum's Phase-3 build (`388ae32`); DP default byte-identical | [../continuum/handoff/ws-phase3-dogfood.md](../continuum/handoff/ws-phase3-dogfood.md) | continuum Phase-3 session |
| H | **Hardening**: review findings #3/#6/#7 CLOSED (**SlotView** capability slot-ownership + mutate `writes`/overlap **chaining** with chain-order dialect; **permit-at-dispatch** fairness, HOL-block dead) + **`INGEST_ISOLATION=subprocess`** (killable per-chunk child: poison blast radius = 1 chunk; drain SIGKILL reclaims ghosts) + milestone eval + sync-retirement recommendation (KEEP inline for C8) | built + tested + workflow-reviewed; DP **163 green**; **MERGED to `main` (`5350f7a`) + pushed 2026-07-21** | [handoff/ws-dp-hardening.md](handoff/ws-dp-hardening.md) | DP hardening session |

## Reference material in this service
- **[readings/](readings/)** — founder-supplied research notes, cited by the WS-VC design:
  *Choosing Frame Rate and Resolution for Screen Recording of Desktop Use* (the 5 fps floor /
  10–15 fps balanced operating point for desktop activity, and the 1080p legibility argument) and
  *OCR processing — thoughts* (the event-driven, region-level, temporally-aggregated OCR design
  that [handoff/ws-video-clip.md](handoff/ws-video-clip.md) D-07 implements). Read both before
  touching sampling rates or the OCR gate.

## Processor seam — how to add a modality (READ THIS before owning image/video/text)
The core (`app/main.py` `POST /ingest` + `app/pipeline.py` `build_c2`) is **modality-agnostic**:
validate C1 → dedup on `chunk_id` (now caches `chunk_id → [record_id,…]`) → pull blob →
**dispatch by `envelope.modality` to a Processor** → for **each** returned unit assemble a C2 and
`POST` it to `/context` → return `{ok, record_ids:[…]}`.

- **A modality = ONE disjoint file** in `app/processing/processors/` that subclasses
  `processing.base.Processor`, sets `modality` + `content_kind`, implements `pipeline_version(settings)`
  + `process(c1, blob, settings, span_seconds) -> list[ProcessedUnit]`, and is decorated with
  `@register` (`processing.registry`). The registry **auto-imports** every module in that package, so
  **you never edit a shared-core file** (not even a registry line) — just drop the file + a fixture.
- **`process` returns a LIST (≥1)**: audio/image/text → 1 unit; **video → many** (one keyframe →
  one unit, `discriminator = keyframe index`). `discriminator=''` is the 1:1 case.
- **`ProcessedUnit`** = `{content{kind,text,language?,segments?}, enrichments{speakers,faces,places,objects}, discriminator}`.
  `content.kind` ∈ frozen C2 enum `transcript|caption|ocr|text`. Emit `segments` already in C2 shape
  (absolute RFC3339); the core assembles content verbatim.
- **`record_id = sha256(chunk_id \0 pipeline_version [\0 discriminator])`** (discriminator folded in
  only when non-empty, so audio's 1:1 id is **byte-identical to the pre-seam v0 id** → reprocess is
  an idempotent upsert). Deterministic + distinct per keyframe.
- Stubs today (`image`/`video`/`text`) are **mock transforms** — real VLM/OCR/normalizer models
  replace only the plugin body. `audio` is the real mock-ASR path moved behind the seam unchanged.
- **`/ingest` response is `{ok, record_ids:[…]}`** (was `{ok, record_id}`; recording's
  capturer was updated + regression-tested 2026-07-10 — resolved).

## Current state
- **M0 built (`:8085`).** `POST /ingest` receives a pushed **C1** envelope → schema-validates it
  (frozen `c1_raw_stream_envelope.v0.json`, 422 on bad) → dedups on `chunk_id` (in-flight lock +
  processed map) → pulls the blob from storage `GET /raw/blobs?ref=` → runs ASR → builds a **C2** →
  `POST`s it to storage `/context/records` → returns `{ok, record_id}`. `GET /health` →
  `{ok, asr_backend}`.
- **ASR backend switch** `ASR_BACKEND=mock|faster_whisper`, **default mock** (no GPU, no torch).
  `faster_whisper` is LAZY-IMPORTED only when selected. `pipeline_version` stamped
  (`asr-mock-v0` / `asr-fw-v0`); `record_id = sha256(chunk_id \0 pipeline_version)` (hex, URL-safe,
  deterministic → idempotent `/context` upsert; version bump forks a new record).
- C2 provenance (`device_id/stream_id/chunk_id/blob_ref/modality`) + `t_start/t_end` carried from C1;
  `content.kind="transcript"`; segment offsets mapped to absolute RFC3339, clamped into the chunk
  span; `enrichments` present-but-empty; `speaker` null (no diarization in v0).
- Blob integrity: `blob_sha256` verified against pulled bytes (502 on mismatch); a missing/deleted
  blob → 502 and NOT marked done, so an at-least-once retry can still reprocess.
- **Tests: 9 passed** (isolated `.venv`, `ASR_BACKEND=mock`, storage faked via httpx `MockTransport`,
  FastAPI `TestClient` in-process — no real port bound). Covers: C1 validate + bad-C1 422; emitted
  C2 schema-valid + provenance carried; `record_id` determinism + version sensitivity; dedup
  (storage POSTed at most once); segment times within span; blob integrity/missing.
- **Capture M1 (2026-07-18, [handoff/ws-m1-continuity-asr.md](handoff/ws-m1-continuity-asr.md)):**
  - **Continuity detector** (`app/continuity.py`): every schema-valid `/ingest` (incl. dedup
    hits) is noted per `(stream_id, sequence, chunk_id)`; `GET /continuity` +
    `GET /continuity/{stream_id}` report max_sequence, merged seen-intervals, **missing**
    (incl. leading gap), duplicate_deliveries, sequence_conflicts. In-memory single-process
    (DedupStore posture). Recording's gap report cross-checks it live — "zero silent loss" is
    now checked on the C1 leg, not assumed.
  - **faster-whisper is STANDING** (in requirements.txt, lazy-imported; `ASR_BACKEND=mock`
    stays default). **VAD gate** (`ASR_VAD`, default on): Silero `vad_filter` before ASR —
    all-silence chunk → honest empty transcript (kills Whisper silence-hallucination).
    `PIPELINE_VERSION` → **`asr-fw-v1`** (version-forward fork; mock dialect untouched).
    **`ASR_LANGUAGE`** pins the ASR language (beta fleet: `en`) — auto-detect hallucinated
    other scripts on faint room audio in the first real phone session (runtime knob, no
    version fork).
  - **Audio pipeline shape** behind the seam: explicit stages asr → diarize → translate →
    acoustic_events; the last three are documented no-op stubs pinning their future contracts
    (speaker fill, `translation` unit, `acoustic` caption unit). Output today byte-identical.
  - Verified live by the lead session: real transcripts (`asr-fw-v1`) from phone-path segments
    in `/context`; empty transcript on silence; continuity reports consistent through
    clean/loss/dup drills. Tests: **38 passed** (24 + 14 new).
  - **Exercised end-to-end through the capture alpha (2026-07-19):** all three real capture
    clients (phone / Chrome extension / mac CLI) drove real media through `/ingest` — e.g. the
    extension run produced 7 real ASR transcripts of a captured tab's audio, the phone run 4 of
    room-mic audio — with `/continuity` cross-checked clean by recording's gap report each time.
    DP itself needed **no change** for the two new clients (they speak recording's client wire,
    which demuxes to the same C1 the phone already used). Suite unregressed at 38.

## Next
- **BUILD THE SCREEN-VIDEO PATH (WS-VC) — [handoff/ws-video-clip.md](handoff/ws-video-clip.md).**
  Design is ratified and decomposed into **8 workstreams (WS-A…WS-H), all startable on day one**,
  with disjoint file ownership; only `WS-F` touches shared core (`main.py`, `ingest_core.py`,
  exclusively) and `WS-E2` (the registration-time law raise, `stagegraph/stage.py`) is deliberately
  sequenced last. Start with **WS-A (wire probe)** — it makes escalation E-3(a) precise and decides
  whether the default prompt pack is multi-image or the `screen-clip-single-v1` fallback.
  Headline shape: **exactly 2 C2 records per video chunk** (`caption` + `ocr`, fixed discriminators,
  C1 span verbatim) instead of today's 4–8 per-keyframe records; **≈6× fewer records, 2.4× cheaper
  at 10 s chunks / 5.8× at 60 s**; day-log dose **4.8× → 15.1×**. **Nothing in the build is blocked
  on an escalation** — see the four that gate the *cutover* and the cost figure, below.
- **ESCALATIONS opened by WS-VC (none block the build; two block the cutover).** Written up in
  [handoff/ws-video-clip.md](handoff/ws-video-clip.md) §10, summarized here so siblings see them:
  **E-1** recording — `--segment-seconds 10 → 60` (config, zero contract surface; the single largest
  cost lever, and it moves the audio leg too, so it is a joint recording × DP-audio call);
  **E-2** storage — a **kind-aware** `DELETE /context/records` retraction primitive (**blocks the
  cutover**, and must key on `content.kind` or it would delete transcripts to remove captions);
  **E-3** inference/platform — (a) serve with `--limit-mm-per-prompt` + explicit `max_pixels`, (b) a
  captioner endpoint **distinct from the user-facing `:8000`** (founders' call; closes CHARTER OQ3);
  **E-4** continuum — render per-fragment local timestamps in `_render_block` (**the goal of
  "at 13:04 the user was writing…" is structurally unreachable without it — DP cannot do this
  itself, C1 carries no timezone**), OCR-dedup, renderer ordering, and a recipe fork;
  **E-5** founders — the parked additive C2 edit (`enrichments.text_regions[]` + root `quality{}`),
  diff written, **not taken** (no consumer yet); **E-6** recording — auto-retry `failed` segments.
- **Hardening slice (2026-07-21, WS-H, branch `svc/dp-hardening`) —
  [handoff/ws-dp-hardening.md](handoff/ws-dp-hardening.md):** all 3 tracked stage-graph
  review findings closed (slot ownership by construction; mutate overlap chaining;
  permit-at-dispatch fairness — `INGEST_MODALITY_LIMITS` now production-safe) + opt-in
  `INGEST_ISOLATION=subprocess`. **MERGED to `main` (`5350f7a`, 2026-07-21); the D16
  re-drive drill is the remaining gate** (to flip the async production default); the ws
  file carries the full M0–M8 milestone evaluation (M1 exit needs WER/DER
  baseline; M2 text/image next unstarted; M7 remaining: backfill tooling,
  reprocess-by-version drill, retention, supervisor/deploy confirmation with platform)
  and the sync-retirement evaluation (**KEEP inline** — it is the C8 skeleton; flip the
  async default via a founders' decision after the re-drive drill).
- ~~Async `/ingest` (ACK 202 + worker queue)~~ **DONE (2026-07-19, WS-AO, M7-early) —
  [handoff/ws-async-observability.md](handoff/ws-async-observability.md).** Behind
  `INGEST_ASYNC` (default off = inline, byte-identical). Async = ACK `202
  {ok,accepted,chunk_id}` + a bounded worker pool (`ingest_queue.py`), one shared
  `process_chunk` core (`ingest_core.py`) for inline + worker, `DedupStore.claim_for_async`
  (finally-released, no orphan), graceful drain on shutdown, transient-retry-then-dead-letter.
  **Reply shape decided JOINTLY with recording (inter-service wire, OQ4 precedent — recorded
  in BOTH canvases):** provenance is optional-at-accept; DP `/continuity` additively reports
  `processed` + `dead_lettered` so recording keeps `dp_acked=1 ⇔ C2 written` and never reads a
  silent `clean` for a lost chunk. Flipping `INGEST_ASYNC=1` **retires the
  `RECORDING_HTTP_TIMEOUT=120` mitigation.**
- ~~Remaining for full M7: durable pending-journal (auto-recovery past a kill / drain-timeout)~~
  **DONE (2026-07-20, WS-SG) — [handoff/ws-dp-stage-graph.md](handoff/ws-dp-stage-graph.md).**
  `app/journal.py` (SQLite pending/processed, WAL): async accept is journaled before the 202,
  startup re-drives every `accepted` row (**kill -9 auto-recovers**), continuity **rehydrates**
  from the journal (**restart-amnesia / false-`gaps` caveat closed**), and the dedup done-map
  has a durable backstop. Epochs guard stale-worker writes; bounded re-drive caps a crash-loop.
  Still M7-proper: dead-letter *backfill* tooling + reprocess-by-version at scale + `processed`
  retention.
- **Stage-graph pipeline (2026-07-20, WS-SG):** the modality Processor seam evolved — a
  modality is now a thin `GraphProcessor` shim over drop-in **stage files** under
  `app/stages/<modality>/` (`app/stagegraph/` executor: readiness DAG, per-stage metrics,
  composed `pipeline_version`, best_effort policy, mutate=version_fragment safety). Audio +
  video ported byte-identically. **Adding OCR / speaker-identity / multi-level captions / bbox
  enrichment = one new stage file, zero core edits** (see the ws file's drop-in table). The
  "Processor seam" section below still describes the monolithic `process()` — that remains the
  public seam (image/text stubs still use it); GraphProcessor is the richer path behind it.
- ~~D9 `/metrics` + dashboard~~ **DONE (2026-07-19, WS-AO, M8).** `/metrics` (Prometheus text,
  zero new deps) + `dashboards/data-processing.json`: ingest rate, async queue depth, per-stage
  + per-modality latency, dedup hits, VAD-empty rate, continuity missing/dup/dead-letter. C8
  sync latency lands in the same `dp_stage_seconds`/HTTP families. Follow-up: finer
  intra-pipeline per-stage latency (asr/diarize/…) — the `stage` label already supports it;
  owned by each modality plugin (additive).
- **Real audio pipeline stages — BUILT** (WS A, [handoff/ws-audio-pipeline.md](handoff/ws-audio-pipeline.md)):
  diarization / translation / acoustic-event captioning now fill their stubs behind
  off-by-default `DIARIZE_BACKEND` / `TRANSLATE_BACKEND`+`TRANSLATE_TARGET` / `ACOUSTIC_BACKEND`
  switches (`app/audio/`). Default output byte-identical (mock dialect untouched, 38-baseline
  green). Diarization forks the audio `pipeline_version` (`+diar-*`); translation + acoustic are
  additive `discriminator`-tagged sidecar records. **Node-7 smoke DONE (2026-07-19, WS-AO): all
  three real backends ran GREEN end-to-end on a real webm/opus speech chunk (pyannote diarize,
  whisper-translate, AST acoustic); the smoke found + fixed two real pyannote torch-2.x compat
  bugs (`weights_only` default; webm decode via ffmpeg pre-decode).** See
  [handoff/ws-audio-pipeline.md](handoff/ws-audio-pipeline.md). Residual: whisper-translate on a
  genuine non-English source still unproven; pyannote pin is 3.1.1, the smoke ran 3.3.2. VAD-cut
  chunk boundaries mean chunks arrive pause-aligned — revisit cross-chunk stitching after
  real-data experience.
- Continuity tracker durability (survives restart) + `sequence_conflicts` surfacing beyond the
  warning log, when multi-replica/serious-scale arrives.
- ~~Real video processor~~ **DONE (2026-07-19, WS-V)** — `processors/video.py` now runs a real
  keyframe pipeline (ffmpeg scene-change selection) behind `VIDEO_BACKEND=mock|vlm`; each
  keyframe gets its own C2 sub-span via the additive `ProcessedUnit.t_start/t_end` hook (OQ14a,
  honored in `build_c2`, no C2 schema change); OCR woven into the caption (D8). Mock stays the
  headless default; the `vlm` backend (httpx → OpenAI-compatible VL endpoint) was exercised
  genuinely against a locally-served Qwen3-VL-8B. See [handoff/ws-video-pipeline.md](handoff/ws-video-pipeline.md).
  **Independent verification round (2026-07-19, integrator session):** the headline claims of
  BOTH slices held under adversarial checking (audio default proven byte-identical by hash vs
  the pre-slice tree; video record_id stability, sub-span math, webm/mp4 decode all confirmed
  empirically); 4 confirmed video-side defects fixed + regression-tested (**DP suite 72**):
  vlm placeholder-emission on undecodable chunks now raises for redelivery, partition-invariant
  head/tail pinning, lenient vision-config numerics. Detail + accepted caveats in both ws
  worklogs; live E2E re-verified on the restarted fleet.
- Real **image** processor is still a mock stub (image build owns it, incl. the OQ14b bbox
  `content.regions[]` C2-additive field the video OCR pass will also want).
- text/image real pipelines per CHARTER M-order (video landed).
- ~~**D9 (2026-07-09) ratified — centralized observability**~~ **DONE (2026-07-19, WS-AO, M8):** `/metrics` (Prometheus text, zero new deps) + `dashboards/data-processing.json` — request rate/latency/errors + ingest rate + async queue depth + per-stage/modality latency + dedup/VAD-empty/continuity counts. Emission side only (platform scrapes/provisions). Finer intra-pipeline per-stage latency is the one documented follow-up (additive, per modality plugin).
