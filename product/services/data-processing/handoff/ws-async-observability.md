# WS — async /ingest + D9 observability (data-processing × recording)

> The ASYNC-INGEST + OBSERVABILITY slice (founders' pick 2026-07-19, after the audio/video
> modality slices merged + verified). Read [CHARTER.md](../CHARTER.md) §v0 deliverables
> (M7 + M8) + OQ13, [ARCHITECTURE.md](../../../ARCHITECTURE.md) §Contracts (C1/C2 FROZEN) +
> §Observability (D9), and [HANDOFF.md](../HANDOFF.md) (Processor seam + Current state) first.
> This is the volatile record for the async-ingest + metrics work across BOTH services.

**Status:** built + tested + adversarially reviewed; **reply shape RATIFIED by the founders
(D16, 2026-07-19)** — the re-drive condition satisfied in-slice. Async /ingest (ACK 202 + worker
pool, off by default) + D9 `/metrics` on both services + node-7 smoke of the real audio backends.
Suites: **DP 98** (72 baseline + 11 metrics + 10 async + 5 dedup) · **recording 120** (110 +
7 async-seam/redrive/migration + 3 metrics) · **storage 26** (untouched) · **Owner session:**
async-observability lead · **Last updated:** 2026-07-19

---

## 1. Async `/ingest` — charter M7 "arriving early" (OQ13)

DP processed chunks **inline** (pull → process → C2 → store inside the request handler). A
fully-loaded chunk (real ASR + diarization + VLM captions) can lawfully exceed recording's
delivery timeout, making recording retry into DP's in-flight lock — fleet-mitigated with
`RECORDING_HTTP_TIMEOUT=120`. The real fix: **ACK fast, process on a worker**, relying on
`chunk_id` dedup + `record_id` determinism for retry safety.

**`INGEST_ASYNC` (default `0` = inline)**, frozen once at startup (`app.state.ingest_async`).
Inline mode is **byte-identical** to the pre-slice behaviour — same status codes, same
`/context` writes, same dedup — proven by the unchanged 72 M0 tests. Flip `INGEST_ASYNC=1`
to enable the worker pool; that **retires the `RECORDING_HTTP_TIMEOUT=120` mitigation**
(revert recording's timeout to 30).

**Mechanism** (`app/ingest_core.py` + `app/ingest_queue.py` + `app/dedup.py`):
- `process_chunk()` is the **one** processing core shared by inline + worker (extracted from
  the M0 handler, byte-for-byte). It raises `ProcessingError(http_status, transient)` — the
  inline path maps it to the exact M0 HTTP status; the worker uses `transient` to decide
  retry-vs-dead-letter.
- **`DedupStore.claim_for_async`** atomically returns `done` (→ 200 record_ids) / `inflight`
  (→ 202 duplicate) / `claimed` (→ enqueue). The claim is released in a **`finally`** on every
  worker exit — success (`put`), dead-letter, terminal error, or **drain-cancel** — so a claim
  is never orphaned (an orphan would ACK every future redelivery as 202-duplicate forever).
- **`IngestQueue`**: a bounded `asyncio.Queue` + N workers, pinned to the running loop
  (lifespan). **Disjoint counters** `queued` vs `processing` (never `qsize+inflight`, which
  double-counts). `task_done()` runs in a `finally` so a mid-process cancel can't wedge
  `queue.join()`. Graceful drain = `wait_for(queue.join(), INGEST_DRAIN_TIMEOUT)` then cancel.
- **Retry taxonomy** (the load-bearing split): **transient** (blob 5xx/timeout, `/context`
  5xx/transport) → retry up to `INGEST_MAX_RETRIES` with backoff, then dead-letter;
  **terminal** (since-deleted blob 404/410, sha mismatch, no units, invalid C2) → dead-letter
  immediately (no futile head-of-line backoff on the pool).

**Config** (`app/config.py`): `INGEST_ASYNC=0`, `INGEST_WORKERS=4` (clamped ≥1 — zero
drainers would accept-forever/lose-all), `INGEST_QUEUE_MAX=256` (clamped ≥1; a finite queue
makes overload a visible 503, not an unbounded backlog an OOM-kill drops silently),
`INGEST_MAX_RETRIES=3`, `INGEST_RETRY_BACKOFF=0.5`, `INGEST_DRAIN_TIMEOUT=30`.

### Reply-shape — the inter-service wire (decided JOINTLY; recorded in BOTH canvases)

Per the OQ4 precedent, this is an inter-service wire (not a C-number): decided with the
recording side, recorded in both HANDOFFs.

| Case | Status | Body |
|---|---|---|
| Bad JSON | 400 | `{error}` — **synchronous, pre-claim** in both modes |
| Bad C1 (schema) | 422 | `{error, violations}` — **synchronous, pre-claim** |
| No processor for modality | 501 | `{error}` — **synchronous, pre-claim** |
| **New chunk accepted** (async) | **202** | `{ok:true, accepted:true, chunk_id}` — **no record_ids** |
| Dedup hit, DONE (either mode) | 200 | `{ok:true, record_ids:[…]}` — returned synchronously |
| Dedup hit, in-flight (async) | **202** | `{ok:true, accepted:true, chunk_id, duplicate:true}` |
| Queue full (async) | **503** | `{ok:false, error:"ingest queue full"}` — recording retries → visible `gaps` |
| Inline success (default) | 200 | `{ok:true, record_ids:[…]}` — unchanged M0 |

**Rule:** deterministic C1/modality rejections (400/422/501) resolve **synchronously before
the claim** in async mode too — never deferred into a silent dead-letter. Provenance
(`record_ids`) becomes **optional-at-accept**.

## 2. The seam invariant + recording's surgical change

**Naive async breaks "zero silent loss."** Recording's gap report reconciles DP's
`/continuity` against ledger ack receipts, trusting **`dp_acked=1` to mean "C2 exists."** If
async DP set `dp_acked=1` at accept and the chunk was later lost (dead-letter, drain-drop,
kill), the report would read **`clean`** — silent loss on the exact surface built to prevent
it. (Surfaced by the pre-implementation design review; would have shipped in the naive
"zero-recording-change" plan.)

**Fix — preserve `dp_acked=1 ⇔ C2 durably written`:**
- **DP `/continuity/{stream_id}`** gains two **additive** fields (C2 stays frozen):
  `processed` (`[lo,hi]` runs of sequences with a C2 written, set at `dedup.put` in BOTH
  modes) and `dead_lettered` (sequences that exhausted retries / hit a terminal error). Still
  `note()`d at accept, so the never-arrived-gap detector is unchanged.
- **Recording ledger** (`chunks.dp_state` column, additive-migrated): a 202 accept →
  `finalize_chunk(accepted=True)` → `dp_acked=0, dp_state='accepted'`. A 200 → unchanged
  (`dp_acked=1, dp_state='processed'`). `confirm_chunk` promotes an accepted chunk to
  confirmed (persisted) once DP reports it processed — so a later DP restart (volatile
  processed set) can't un-confirm it.
- **Recording gap report** reconciles: `acked` = `dp_acked=1` rows only; a chunk DP reports
  `processed` is lazily confirmed; a `dead_lettered` chunk → verdict **`gaps`**; an
  accepted-but-unconfirmed chunk → verdict **`recording`** (in-flight). `leg["dp"]` keeps its
  frozen 5-key shape; dead-letter/accepted surface as sibling leg fields.
- **`clients.py` / `capturer.py` unchanged** structurally — they already coerced
  `ack.get("record_ids") or []`, so ack-without-record_ids was tolerated; the emitter now also
  records WHICH state (accepted vs processed).

**Honest loss boundary — THIS slice guarantees *never falsely `clean`*:** every accepted
chunk is confirmed, or reads `recording` (in-flight / queued / lost-to-kill), or `gaps`
(dead-lettered). **All loss is visible.** NOT closed here (stays full M7): auto-recovery of a
volatile-queue drop past the drain timeout / a kill -9 — those read `recording` and are
re-drivable from recording's durable ledger + `/raw` blob, but not auto-re-driven. M7 closes
it with a durable DP pending-journal (mirroring recording's ledger).

**Re-drive path (D16 ratification condition — named + drilled in-slice).** A `recording`
verdict after queue loss has a documented way back to `clean` before M7:
`POST /capture/sessions/{id}/redrive` (and `emitter.redrive_accepted_chunks`, callable on
restart / periodically) re-pushes every `dp_state='accepted'` chunk's ORIGINAL C1 envelope
(rebuilt from the ledger; bytes already durable in `/raw`, so no re-upload). DP's `chunk_id`
dedup makes it idempotent + safe: a done chunk short-circuits to `200 {record_ids}` → recording
`confirm_chunk`s it (→ `clean`); an in-flight one re-ACKs `202` (stays accepted); a lost one is
re-claimed + reprocessed. Drilled by `test_redrive_confirms_accepted_chunks` +
`test_redrive_leaves_still_pending_accepted`. **Accepted caveat (D16):** `record_ids=[]` ledger
provenance for a 202-confirmed chunk — the ids stay derivable (deterministic on `(chunk_id,
pipeline_version[, discriminator])`); a re-drive that hits a done-claim also backfills them.

**Accepted caveat (review finding #6, deferred — fails SAFE):** a chunk DP *durably
processed* but that recording never confirmed before a **DP restart** (its in-memory
processed set is volatile) reads a **permanent `gaps`** on that stream — a *false-positive*
loss (over-reports, **never hides** loss, so the never-falsely-`clean` invariant holds). It
needs the narrow "processed → restart → before any recording `/report` poll" window. The
proper fix (subtract accepted-in-flight sequences from DP's post-restart amnesic `missing`,
or a durable DP processed-journal so a restart doesn't forget) rides the **M7** durable
pending-journal — land it before async `/ingest` is trusted for final archived verdicts
(async is off by default, so it isn't yet).

## 3. D9 observability — `/metrics` on both services (charter M8 + recording M6)

**Zero new deps** (`prometheus-fastapi-instrumentator`/`prometheus_client` are NOT in the
frozen requirements and the loop must stay headless-green): an in-house `app/metrics.py`
registry renders Prometheus text (0.0.4) — Counter/Gauge/Histogram, label escaping, cumulative
`_bucket`, and **pull-time gauge sources** for live state. A **pure-ASGI** middleware records
baseline request/latency/error metrics **without touching response bodies** (two exact-dict
body assertions depend on that). Route templatizers bound label cardinality (one series per
route, not per `stream_id`/`session_id`). `METRICS_ENABLED=1` default.

- **DP `/metrics`**: `dp_ingest_total{modality,result}` (accepted/processed/deduped/
  duplicate/rejected), `dp_ingest_queue_depth` + `dp_ingest_processing` (async queue),
  `dp_stage_seconds{modality,stage}` (blob_fetch/process/context_write — the C8 sync path
  lands here too), `dp_dedup_hits_total`, `dp_vad_empty_total`, `dp_dead_letter_total`,
  `dp_ingest_retries_total`, `dp_continuity_*` (missing/processed/dead_lettered/dups/conflicts,
  aggregated). Dashboard: `dashboards/data-processing.json`.
- **Recording `/metrics`**: `rec_segments{state}`, `rec_chunks{modality}`,
  `rec_chunks_dp_state{dp_state}`, `rec_sessions_total`/`_active`, `rec_client_missing_total`,
  `rec_client_duplicate_deliveries_total` (ledger-derived, pull-time), `rec_downstream_retries_total{service}`,
  `rec_segment_emit_latency_seconds` (received→emitted). Dashboard: `dashboards/recording.json`.

Platform owns the shared Prometheus/Grafana + provisioning — emission side only, per D9.

**Follow-up (M8 remainder):** finer intra-pipeline per-stage latency
(asr/diarize/translate/OCR/dense-caption) — the `dp_stage_seconds{stage}` family already
supports arbitrary stage labels; each modality plugin wires its own named stages (owned by the
modality session, additive, doesn't change output). The core delivers per-modality +
blob/process/context-write today.

## 4. Node-7 smoke of the real audio backends

ALL THREE unrun audio seams (pyannote / whisper-translate / AST) **ran green end-to-end on
node-7** against a real webm/opus speech chunk; the smoke found + fixed **two real pyannote
torch-2.x compat bugs** (weights_only default; webm decode). Full detail + caveats in
[ws-audio-pipeline.md](ws-audio-pipeline.md) (2026-07-19 node-7 entry). Reproducible harness:
`scripts/smoke_audio_backends.py`.

## 5. Open questions resolved

- **DP OQ13 (ingest processing mode) — RESOLVED** (2026-07-19): async `/ingest` = ACK 202 +
  worker pool, off by default, dedup/record_id keep at-least-once safe; visible-not-silent
  loss for accepted-then-lost chunks; full durability (dead-letter+backfill journal) stays M7.
  Recorded in CHARTER.md.
- **Recording OQ3 (codec/bitrate ladder) — informed** (joint recording × DP): with real
  pipelines + alpha data, DP states the fidelity it actually needs per modality. Recorded in
  recording/CHARTER.md (dated). Summary: audio → 16 kHz mono is ASR/diarization/AST-native
  (recording already demuxes to `audio/wav` 16 kHz mono s16le; no higher rate helps the
  models); video → keyframe VLM captioning is resolution-bound not bitrate-bound
  (`VIDEO_FRAME_MAX_WIDTH=768`), so container-copy (no re-encode) at capture quality is
  sufficient — the cost dial is keyframe cadence, not bitrate.

## Worklog
- 2026-07-19 — Pre-implementation design review (workflow, 6 agents): caught the
  `dp_acked`-at-accept silent-loss flaw → the recording seam change + `/continuity`
  processed/dead_lettered fields. Built DP async (`ingest_core`/`ingest_queue`/`dedup` claim/
  `continuity` processed-dead_lettered/`main` freeze+lifespan+reply-shape) + D9 metrics both
  services + dashboards. Recording seam (ledger `dp_state`+migration+confirm_chunk, emitter
  branch, report reconciliation). Node-7 smoke green (+2 pyannote fixes). Pre-fix suites: DP
  97 / recording 115 / storage 26 green.
- 2026-07-19 — **Adversarial review round** (workflow, 6 finders → per-finding skeptic verify
  → synthesis; 18 agents, 9 confirmed / 0 uncertain). **5 fix-before-merge, all fixed +
  regression-tested:** (1) pyannote cold-load raced the process-global `torch.load` swap +
  `_PIPELINE_CACHE` populate under the worker pool → a `threading.Lock` now serializes the
  whole load; (2) an unexpected error out of `processor.process` (model cold-load 503 / CUDA
  OOM / ffmpeg) was dead-lettered terminal, diverging from inline's retry-via-recording → now
  **transient** (retry-then-dead-letter), matching inline resilience; (3) a `/metrics` scrape
  re-ran the ledger snapshot 7× → memoized per-scrape (one DB pass); (4) the `dp_state`
  migration didn't backfill → pre-slice `dp_acked=1` rows now backfill to `'processed'`;
  (5) blob-fetch retry classification storming permanent 4xx → retry only 5xx/408/429. Plus
  the 3 requested coverage tests (migration+backfill, broken-source scrape isolation,
  inline-confirm no-op) + a transient-processor-retry test. **1 deferred (finding #6, fails
  SAFE — caveat above).** Suites after fixes: **DP 98 / recording 118 / storage 26** green.
- 2026-07-19 — **Founders ratified the reply shape (D16)** — same wire, and the deep session's
  design memo cleared + strengthened their bar. Satisfied the one ratification condition
  in-slice: the **re-drive path** for accepted-unconfirmed chunks (`POST /…/redrive` +
  `emitter.redrive_accepted_chunks`, idempotent via DP's done-claim short-circuit) + 2 drill
  tests. Recording **120** green.
