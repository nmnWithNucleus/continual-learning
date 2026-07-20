# WS — DP v1: durable ingest journal + stage-graph pipeline

> The two-layer architecture upgrade after the async /ingest + D9 slice landed: (A) make
> the async core DURABLE (finish M7's heart + close the deferred false-`gaps` caveat), and
> (B) turn every processing step into a DROP-IN stage file. Read [CHARTER.md](../CHARTER.md)
> §M7/M8, [ARCHITECTURE.md](../../../ARCHITECTURE.md) §Contracts (C1/C2 FROZEN), and
> [ws-async-observability.md](ws-async-observability.md) first. This is the volatile record
> for the journal + stage-graph work.

**Status:** built + tested + real-backend-validated on node-7 + adversarially reviewed.
Everything FROZEN stays frozen (C1/C2, the D16 reply wire, claim/dedup semantics, the
Processor registry, chunk-atomic idempotency). Inline mode byte-identical for default
configs; both modality ports byte-identical (only 2 vlm-client tests edited to a new
factory seam). Suites: **DP 127 · recording 120 · storage 26** green. **Owner session:**
async-observability lead (continued) · **Last updated:** 2026-07-20

---

## Layer A — durable ingest journal (`app/journal.py`)

Closes the honest loss boundary the async slice documented: the accepted-queue, dedup
done-map, and continuity `processed`/`dead_lettered` sets were all in-memory. Now a SQLite
journal (`$DP_VAR_DIR/dp.db`, WAL, connection-per-call, `BEGIN IMMEDIATE` — recording's
ledger pattern; **lazy** so module import touches no disk):

- **`pending`** — every async-ACCEPTED chunk's full C1, INSERTed inside the claim BEFORE
  the 202. Startup re-drives every `state='accepted'` row → **kill -9 auto-recovers** with
  no external re-drive. A dead-lettered chunk stays as `state='dead_letter'` (durable, ops
  visible); a redelivery resets it to `accepted`.
- **`processed`** — one row per chunk whose C2s are durably written (BOTH modes). Powers
  (a) **continuity rehydration** at boot and (b) the **durable dedup backstop**
  (`DedupStore(done_fallback=…)`): a redelivery after restart returns prior record_ids
  (200), never reprocesses — **unless** the modality's `pipeline_version` changed since, in
  which case the honest answer is a version-forward reprocess (`processed_record_ids`
  staleness check).

**Two safety mechanisms (from the design review), each with a unit drill:**
- **Epochs.** `accept` bumps a per-row `epoch`; terminal writes (`mark_processed`'s
  pending-delete, `mark_dead_letter`) are epoch-guarded, so a stale worker finishing AFTER
  a redelivery re-accepted the chunk **no-ops** instead of clobbering the fresh row. (The
  processed INSERT is deliberately un-guarded: if the C2s were written the receipt is true.)
- **Bounded re-drive.** `pending_for_redrive` durably increments `redrive_attempts` and
  flips over-cap rows (`DP_REDRIVE_MAX_ATTEMPTS`, default 5) to `dead_letter` in one
  transaction — a poison chunk that crash-loops the service breaks the loop VISIBLY.

**Continuity rehydration** (`ContinuityTracker.rehydrate`) merges THREE classes — processed
(seen+written), dead (seen+failed), and **accepted (SEEN-only — the keystone: a chunk merely
waiting to be re-driven is delivered coverage, never fabricated into a gap)**. Live state
wins, so a double lifespan (TestClient per `with`) never inflates counters. This closes the
**deferred false-`gaps` caveat**: a DP restart no longer forgets what it durably wrote, so
recording's gap report cannot mis-read intact history as loss.

**Wiring:** `main.py` lifespan runs `pending_for_redrive` → `rehydrate` → start workers →
background re-drive task (waiting `submit`, so a backlog larger than the queue bound drains
completely). Accept path: `accept` off the loop, epoch into the job, `unaccept` (restore
prior / delete fresh) on QueueFull. `process_chunk` writes the receipt journal-before-dedup
(both modes). Dead-letter order: journal → continuity → release-claim (release-last, so a
redelivery can only re-claim after the mark lands). New gauges: `dp_journal_pending`,
`dp_journal_dead_letter`.

**What THIS closes vs what stays:** kill/crash now **auto-recovers** (was: re-drivable only
from recording); restart amnesia **closed**. Remaining M7-proper: dead-letter *backfill*
tooling + reprocess-by-version at scale + `processed` compaction/retention.

## Layer B — the stage graph (`app/stagegraph/` + `app/stages/`)

Generalizes what audio half-invented (staged methods + a state blackboard + single-resolver
version tags) into the core, so **every processing step is one drop-in file**:

- **`Stage`** (one auto-discovered file, `@register_stage`): declares `kind`
  (`primary|mutate|sidecar`), `policy` (`required|best_effort`), `needs`/`provides` (the DAG
  + slots), `mutable_slots` (primary-only), `order`, `enabled`/`version_fragment`, and
  EXACTLY ONE of `run_sync` (always threadpooled — a CPU/GPU/subprocess stage can't freeze
  the loop by accident) or `run_async` (native IO: the VLM fan-out). Registration validates
  hard (mutate can't override `enabled`; primary/mutate can't be best_effort; unique
  name/order; needs closure).
- **`resolve`** (per call, cheap): exactly one enabled primary; a required stage needing a
  disabled one is an error; **no required/primary may sit downstream of a best_effort stage**
  (its promise would be hollow); a best_effort stage needing a disabled one auto-disables
  with a metric. `pipeline_version = base_fragment + ''.join(sorted(enabled fragments))` —
  reduces EXACTLY to the shipped dialects, and **a mutate stage's enabledness IS its
  `version_fragment`** so it physically cannot mutate without forking the dialect (the
  silent-overwrite bug class dies by construction).
- **`run_graph`** (readiness executor): one task per enabled stage in an `asyncio.TaskGroup`,
  each awaiting its needs' futures — independent stages run concurrently (acoustic ∥ asr;
  keyframe captions fan out). Required failure cancels + awaits siblings, then re-raises the
  **unwrapped leaf** (RuntimeError/ValueError/ProcessingError — the worker taxonomy + inline
  HTTP mapping + `raises()` tests all see the real exception). best_effort failure → SKIPPED
  future → cascade-skip dependents (counted). Slots commit **on success only**. Assembly is
  last + deterministic (primary's `assemble`, then sidecars by `(order, name)`). Two runtime
  guards: a mutable-slots fingerprint (a sidecar reaching into the primary's slots is caught)
  and discriminator-uniqueness (colliding record identities are terminal). Per-stage latency
  → `dp_graph_stage_seconds{modality,stage}`; failures/skips → `dp_graph_stage_failures_total`.
- **`GraphProcessor`**: registered via the EXISTING `@register` seam; `process_async` awaited
  on the loop by `ingest_core` (with the same `dp_stage_seconds{stage=process}` observation);
  sync `process` = `asyncio.run(process_async)` for loop-free callers.

**Ports (byte-identical, proven by the untouched suites):**
- **audio** → `asr` (primary), `diarize` (mutate, single-resolver), `translate` (sidecar,
  reads the immutable ASR result), `acoustic` (sidecar, `needs=()` → **now parallel to asr**).
  Unit order `[primary, translation, acoustic]` preserved. **Validated on node-7 through the
  graph with REAL backends:** `pipeline_version=asr-fw-v1+diar-pyannote-v1`, primary
  transcript diarized (spk_0), acoustic caption sidecar, translation correctly skipped
  (English source) — identical to the monolith.
- **video** → `keyframes` (prep sidecar; late-bound `video_proc.extract_keyframes` so the
  monkeypatch seam survives) + `captions` (primary; **now captions keyframes CONCURRENTLY**
  under vlm — one shared thread-safe httpx client fanned across the threadpool, order
  preserved — instead of a sequential per-chunk loop; assembles every unit exactly as before:
  weave, sub-spans, interleaved `:ocr`). VLM client behind a `vlm.make_client` factory (the 2
  wire tests patch it).

**Fairness:** `INGEST_MODALITY_LIMITS` (e.g. `video=2`) — a per-modality max-in-flight
semaphore held around each processing ATTEMPT (never across the retry backoff), so a video
burst can't starve audio latency. Default empty = today's flat pool.

## Drop-in demonstration — how a future step lands

| capability | as a stage file (zero core edits) |
|---|---|
| dedicated OCR pass | `app/stages/video/ocr.py`, `needs=('captions',)`, sidecar `kind='ocr'` records |
| known-speaker identity | `app/stages/audio/speaker_id.py`, `kind='enrich'`/mutate, `needs=('diarize',)`, declares a `version_fragment` → auto-forks |
| multi-level captions | `app/stages/video/summary.py`, `needs=('captions',)`, best_effort sidecar; batch across chunks later |
| bbox object augmentation | `app/stages/image/objects.py`, enrich `enrichments.objects` (+ `content.regions[]` when OQ14b freezes) |

## Decisions (ratified with the founders' D16-style bar)
Both layers; chunk-atomic unit of async work + intra-chunk DAG (no global per-stage queues);
strict failure policy with per-stage opt-in best_effort (mutate/primary can NEVER be
best_effort — enforced at registration); per-modality fairness semaphores.

## Deferred (noted, not built)
Durable dead-letter backfill tooling; `processed` retention/compaction; a shared/pooled
async VLM client (the threadpool fan-out is the v1 concurrency win; a pooled client is a
throughput refinement); C8 `interactive` profile (mechanism ready — a stage subset per
request — consumed when input builds C8); per-stage `resource` classes + timeouts (cut as
speculative at v0 scale per the design review).

## Review follow-ups (tracked, not merge blockers — from the 2026-07-20 review)
- **`INGEST_MODALITY_LIMITS` HOL-blocks (finding #3) — HARD PREREQUISITE before enabling.**
  The per-modality permit is acquired AFTER `queue.get()` on one shared FIFO, so a blocked
  worker holds a dequeued job and head-of-line-blocks the pool — *worse* than no limit.
  Byte-identical to today while the knob is empty (the default), and a startup WARNING fires
  if it's set. Correct fix needs a per-modality queue/worker partition or a
  permit-before-dequeue peek/skip. **Do not enable in production until that lands.**
- **Mutable-slots fingerprint guard is order-dependent (finding #6, LOW, defense-in-depth).**
  Snapshotted at cohort-done while sidecars run concurrently, so an illegal sidecar write
  landing *before* the last mutate finishes could be baked into the reference and missed. No
  shipping sidecar writes a primary mutable_slot (the static rule — sidecars can't declare
  `mutable_slots` — is the real guard). Harden later: a guarded slot proxy, or fingerprint
  each mutate synchronously on the loop before sidecars run.
- **Two concurrent mutate stages on an overlapping slot could race (finding #7, LOW, latent).**
  `resolve` gives mutates an implicit dep on the primary only, not on sibling mutates. Only
  one mutate (diarize) ships today. When a second lands, `resolve` must chain a deterministic
  order among mutates sharing a written slot (or reject an undeclared overlap).

## Worklog
- 2026-07-20 — Architecture atlas published; decisions locked. Pre-implementation design
  review (6 lenses → synthesis): shaped epochs, unaccept, bounded re-drive, accepted-class
  rehydration, the readiness executor, mutate=version_fragment, commit-on-success, the two
  runtime guards, the compat-shim-as-contract, and the cuts (async VLM pool, MODEL_WARMUP,
  `resource`/`timeout`). Built Layer A + Layer B; ported audio + video byte-identically;
  fairness semaphores. DP 127 / recording 120 / storage 26 green. Real audio backends
  validated through the graph on node-7 (`asr-fw-v1+diar-pyannote-v1`, diarized primary +
  acoustic sidecar).
- 2026-07-20 — **Adversarial review round** over the full v1 diff (6 finders → per-finding
  skeptic verify → synthesis; 18 agents, 9 confirmed / 0 uncertain). **2 fix-before-merge,
  both fixed + regression-tested:** (1) **[high]** the async accept path released the dedup
  claim only on the QueueFull / no-queue paths — a failed durable `journal.accept`/`unaccept`
  write (disk-full / lock-contention) orphaned the claim → every retry ACKed 202-duplicate
  forever (silent loss + lying ACK); now a `finally` frees the claim on any non-enqueue exit
  (`test_failed_journal_accept_releases_claim`). (2) **[med]** `pending_for_redrive` blanket
  -incremented the re-drive counter on ALL co-pending rows per RESTART, so one crash-loop
  poison chunk dead-lettered an innocent never-dequeued backlog; the count is now attributed
  **per actual processing attempt** (worker-side `note_redrive_attempt` for re-driven jobs)
  and startup dead-letters only rows with real attempt evidence
  (`test_redrive_cap_is_per_processing_attempt_not_per_restart`). Plus 2 cheap follow-ups
  fixed inline: a missing processor on re-drive now dead-letters (was: perpetual 'recording');
  the vlm caption fan-out `gather(return_exceptions=True)` so the shared client isn't closed
  under an in-flight sibling. 3 LOW findings tracked as follow-ups (§ above). Suites after
  fixes: **DP 128 / recording 120 / storage 26** green (stable across repeat runs).
