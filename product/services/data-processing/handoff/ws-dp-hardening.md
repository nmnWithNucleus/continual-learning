# WS-H — DP hardening: slot ownership · fair dispatch · subprocess isolation

> The follow-up slice that closes ALL THREE tracked review findings from
> [ws-dp-stage-graph.md](ws-dp-stage-graph.md) (#3 fairness HOL-block, #6 order-dependent
> fingerprint guard, #7 mutate-overlap race) **plus** the two failure classes the design
> discussion surfaced beyond them: the poison-chunk service blast radius and the
> ghost-thread leak on cancel. Read [CHARTER.md](../CHARTER.md) §M7 and
> [ws-dp-stage-graph.md](ws-dp-stage-graph.md) first.

**Status:** built + tested + adversarially reviewed (workflow: 5 dimension reviewers →
2 refuters per finding; 19 confirmed findings → 9 fixed in code + 7 gap tests + 2
accepted-documented + 1 duplicate). Everything FROZEN stays frozen (C1/C2, D16 reply
wire, claim/dedup/epoch semantics, chunk-atomicity, the single-bounded-queue 503 story).
Inline mode + mock defaults byte-identical — **proven empirically**: sha256 of the full
C2 output (processed_at stripped) over audio/video/image/text fixtures is IDENTICAL
across main, this branch, and this branch under subprocess isolation, for both the
default and diarize-mock dialects. Suite: **DP 163 green** (was 128). **Owner session:**
DP deep session (continued) · **Last updated:** 2026-07-21

Branch: `svc/dp-hardening` (4 commits, one per workstream), from `main@86acb95`.

---

## 1. Slot ownership — findings #6 + #7 (stagegraph)

One model closes both: **who may touch a slot is declared, enforced by construction,
and encoded in the dialect.**

- **`SlotView` capability proxy** (`stage.py`): every stage's `run_*` receives a
  stage-scoped view of the slot blackboard, not the dict. A **sidecar is refused even a
  READ of the primary's `mutable_slots`** — you cannot scribble on an object you were
  never handed — and ALL direct writes are refused except a mutate's declared `writes`.
  Violations raise `SlotAccessError` (a `RuntimeError`) at the offending line,
  synchronously, order-independently. The old end-of-run fingerprint guard (which missed
  any illegal write landing before the last mutate finished — finding #6) is **deleted**,
  not just backstopped.
- **Declared commits:** `provides` is now AUTHORITATIVE, not documentation — a
  `StageResult.slots` key outside `provides` (∪ `mutable_slots` for the primary) fails
  the chunk loudly even for `best_effort` stages (a stage may skip, never scribble), and
  `provides` sets must be disjoint per modality (resolution error).
- **Mutate `writes` + overlap chaining** (finding #7): a mutate declares `writes`
  (⊆ primary `mutable_slots`; enforced at registration AND resolution). Two enabled
  mutates with intersecting `writes` get an implicit dep chain by `(order, name)` —
  overlapping mutates can never run concurrently, so the mutated record stays a
  deterministic function of config (C2 idempotency). An explicit `needs` contradicting
  the chain order is a loud cycle error, never a silent reorder. Disjoint mutates still
  run concurrently.
- **Dialect encodes the order:** `pipeline_version = base + mutate fragments in CHAIN
  order + sorted(other fragments)`. diarize→speaker_id and the reverse are different
  dialects — as they must be, their records genuinely differ. All shipped dialects
  (`asr-mock-v0`, `asr-mock-v0+diar-mock-v1`, `asr-fw-v1+diar-pyannote-v1`,
  `vidproc-*-v0`) unchanged byte-for-byte (sidecars contribute no fragment today).
- Shipping declarations added: `DiarizeStage.writes=('segments','enrichments')`,
  `CaptionsStage.provides=('captions',)`.

*Semantics note (composition over last-writer-wins):* the chain makes a future
`speaker_id` stage COMPOSE on diarize's output (reads the diarized turns, enriches
them), which is the intended model for shared enrichment slots.

## 2. Fair dispatch — finding #3 (`ingest_queue.py`, full rewrite)

- **Permit-at-dispatch:** a worker takes the modality permit ATOMICALLY (same event-loop
  tick) as it removes a job, and only removes a job whose permit is available — scanning
  PAST capped-modality jobs to the first eligible. A capped burst queues without
  occupying a worker; other modalities flow around it. (The old design dequeued THEN
  acquired, so one blocked worker HOL-blocked the pool — worse than no limit. The
  startup EXPERIMENTAL warning died with the flaw; `INGEST_MODALITY_LIMITS` is now
  production-safe.)
- **Backpressure unchanged:** ONE shared bound counts every queued job regardless of
  modality → the bounded-queue 503 story is exactly as before. Empty knob (default) →
  every job always eligible → pure FIFO, byte-identical to the unlimited pool.
- **Backoff releases the permit:** a chunk waiting out a transient-retry sleep frees its
  modality slot and re-acquires before the next attempt (held-flag prevents
  double-release under a drain cancel).
- **Wake-all-and-rescan discipline:** no permit/item is ever transferred through a
  future, so a waiter cancelled between wake and resume can never strand one —
  lost-wakeup-safe by construction. Drain barrier (`join`-equivalent), disjoint
  `queued`/`processing` counters, claim-release-in-finally semantics all preserved.

## 3. Subprocess isolation (`isolation.py`, `INGEST_ISOLATION=subprocess`, default off)

Closes the two failure classes the in-process pool structurally cannot contain:

- **Poison-chunk blast radius:** a segfault / native OOM / `os._exit` in model code
  kills ONE child, not the service. The parent applies the normal
  transient-retry-then-dead-letter taxonomy (each retry a fresh child) — a true poison
  chunk dead-letters visibly while every other chunk keeps flowing. Previously it
  crash-looped the entire service through the durable re-drive cap, redoing all
  co-pending work each lap.
- **Ghost computation on cancel:** a drain-timeout cancel SIGKILLs the child; the kernel
  reclaims CPU/GPU immediately. Previously the cancelled task's threadpool thread ran
  the blocking model call to completion (CPython threads cannot be killed).

Boundary = exactly the Processor step (child resolves the processor from the registry by
modality; blob fetch, sha verify, C2 assembly/validation/write, journal + dedup stay in
the parent). Chunk-atomicity and the parent-computed `pipeline_version` stamp unchanged.
Taxonomy crosses the boundary intact (`ProcessingError` detail/status/transient
preserved; generic error and died-without-reply → the same transient treatment as an
unexpected in-process error). Start methods `spawn` (default; CUDA/thread-safe) |
`fork` | `forkserver`.

**Documented costs when on:** per-chunk process start + model load (no warm child pool
this slice); per-graph-stage metrics inside the child not recorded (coarse
`stage="process"` timing remains); a hung child still holds its worker (wall-clock kill
knob = later slice); video `vlm` in-child builds its own HTTP client per chunk.

## 4. Adversarial review round (2026-07-21) — 47 agents, 19 confirmed / 2 refuted

Workflow: 5 dimension reviewers (queue concurrency, slot ownership, isolation,
contract preservation, test gaps) → every finding attacked by 2 independent refuters
(correctness + reproduce lenses); only findings surviving both were acted on.

**Fixed in code (9):**
1. **[high, queue]** Backoff retriers were STARVED unboundedly under a sustained
   capped-modality backlog: a finishing worker's same-tick rescan always stole the
   freed permit for a newer queued job before the parked retrier's wakeup ran
   (empirically reproduced: the retry ran only after a 30-job backlog fully drained).
   Fix: parked re-acquirers hold a **permit reservation** the dispatch scan must
   respect (`_reacquiring` counters — no permit transfer, so cancellation still can't
   strand one). Also closes the FIFO-inversion contract regression (same root cause).
2. **[high, slots]** A sidecar declaring `provides` on a primary MUTABLE slot passed
   resolution (the primary need not repeat mutable_slots in provides) and could
   blind-clobber the mutate cohort's output via a "declared" StageResult commit.
   Fix: `resolve()` seeds the ownership map with the primary's `mutable_slots`.
3. **[med, slots]** A mutate could in-place-mutate mutable slots OUTSIDE its declared
   `writes` through a read reference (aliasing bypasses `__setitem__`), evading the
   overlap chain. Fix: a mutate's `deny_read` = `mutable_slots − writes` — the
   reference itself is withheld (reading a slot other mutates write is also a race,
   so read access legitimately requires declaring it).
4. **[high, isolation]** Under `spawn`, `proc.start()` pickles the args (incl. the
   full chunk blob — MBs) and blocks writing them through the child's 64KB bootstrap
   pipe ON THE EVENT LOOP until the fresh interpreter boots and drains. Fix: spawn +
   collect now run in ONE executor-thread call (`_spawn_and_collect`, holder-based
   kill-on-cancel with a raced-cancel check around start).
5. **[med, isolation]** A child `ProcessingError` with an unpicklable `detail` blew up
   the send and exited cleanly → the parent misread a TERMINAL failure as transient
   "died (exitcode 0)". Fix: every child send has a string-only fallback preserving
   the transient/status flags (sanitized `repr` detail).
6. **[med, isolation]** `_collect` parked one **shared asyncio default-executor**
   thread per in-flight child (cap `min(32, cpus+4)`) — saturation wedged unrelated
   loop work (`getaddrinfo`). Fix: dedicated lazy `ThreadPoolExecutor(64)`.
7. **[low, slots]** `run_graph` accepted `units` from ANY stage kind. Fix: only
   sidecars may return units (primary emits via `assemble`, mutate edits in place) —
   loud `RuntimeError` otherwise.
8. **[low, slots]** `ctx.c1` was handed to stages by reference; a best_effort stage
   could corrupt chunk-identity fields (record_id/journal inputs) and then "skip".
   Fix: stages get a read-only `MappingProxyType` view of c1.
9. **[low, config]** `_choice` failed OPEN silently (`INGEST_ISOLATION=1` → isolation
   off, no signal). Fix: warn-once on unrecognized values. **`forkserver` removed
   entirely** — it freezes `os.environ` at server launch, breaking the
   child-inherits-parent-env premise (stale-config child under a fresh parent-stamped
   `pipeline_version`).

**Test gaps closed (7 new drills):** the starvation repro; worker generic-exception
backstop releases the permit (a leak there wedges the modality silently); drain/cancel
during backoff sleep + `_acquire_permit` park (chunks stay re-drivable); 1MiB child
reply (pins the load-bearing recv-before-join order — a join-first refactor deadlocks
here, not in production); async+spawn E2E (the production combination); fresh child
per retry attempt (pins the contract a warm-pool change must renegotiate); 3-writer
transitive mutate chain + 3-fragment version order.

**Accepted, documented (not fixed):**
- Interior mutation of NON-mutable committed slots (e.g. a sidecar scribbling on the
  `asr` result object it legitimately reads): the capability boundary is per-slot
  reference-scoped, not deep-frozen. Deep immutability of slot values (frozen result
  dataclasses) is a candidate follow-up; today it is the same trust level as any
  shared in-process object.
- `ctx.resources` stays unproxied (app-owned live handles — metrics, pools — are
  mutable by design).

**Refuted by the verify pass (2):** a claimed forever-zombie on non-EOF recv errors
(join runs regardless; the rewrite moved it into a `finally` anyway) and a claimed
unpinned drain property for capped queued jobs.

## Sync/inline-mode retirement — evaluated, RECOMMEND KEEP (decision needed to flip)

Asked: "moving at speed with async, can we retire the sync path?" Evaluation:

- **C8 needs it (charter M6):** the synchronous pipeline API for input's QueryBuilder is
  a charter deliverable, and the charter pins ONE code path for both profiles. The
  inline handler is that profile's precedent; `ingest_core.process_chunk` is already the
  single shared core — the "duplication" is ~40 lines of HTTP mapping in `_ingest_inline`.
  Deleting inline deletes the C8 skeleton.
- **The wire default is a joint decision:** recording's capturer speaks the inline reply
  (`INGEST_ASYNC` defaults off). Flipping the default is a D16-class inter-service
  decision with recording (their canvas records the reply wire jointly), not a DP-local
  cleanup — and the D16 condition (a re-drive drill) is still open.
- **Dev/smoke value:** inline is the loop every headless test + fixture drives with zero
  queue machinery; it is also the byte-identical baseline every port has been proven
  against (a real verification asset).

**Recommendation:** keep inline; propose flipping the production default to
`INGEST_ASYNC=1` in a founders' session once the D16 re-drive drill has run, and revisit
retiring the inline *handler* (never the shared core) only after C8 lands with its own
surface. No code deleted in this slice.

## Milestone evaluation (charter M0–M8, as of this slice)

| M | Deliverable | Status |
|---|---|---|
| M0 | Walking skeleton (C1→ASR→C2, idempotent) | **DONE** (proven live; alpha exercised) |
| M1 | Full audio pipeline | **BUILT, exit gate open**: diarize/ASR/VAD/translate/acoustic all real behind switches, node-7 green — but no denoise stage, and the WER/DER baseline on a labeled sample (the exit criterion) is unmeasured |
| M2 | Text normalization + image pipeline | **NOT DONE**: both are mock stubs (`textnorm-mock-v0`, image mock caption); OQ14b bbox field waits on the real OCR pass |
| M3 | Video pipeline | **DONE** (ffmpeg keyframes + VLM captions + OCR weave + per-keyframe sub-spans; real Qwen3-VL-8B E2E) |
| M4 | Cross-source time spine (skew) | **NOT STARTED** (absolute timestamps exist; skew handling/alignment tests don't) |
| M5 | World-data enrichment | **NOT STARTED** (`enrichments` carry only diarization speakers; faces/places/objects empty) |
| M6 | C8 synchronous API | **NOT STARTED** (mechanism ready: inline path + stage-subset profiles; blocked on input's C8 shape) |
| M7 | Production hardening | **SUBSTANTIALLY DONE after this slice**: backpressure ✓, dead-letter ✓, durable journal + kill-recovery ✓, epochs ✓, bounded re-drive ✓, fairness ✓ (now safe), poison/ghost isolation ✓ (opt-in). **Remaining:** dead-letter backfill tooling, reprocess-by-version drill at scale (a full pilot day), `processed` retention, warm child pool + wall-clock kill knob, and the ops story for WHO restarts DP (no supervisor config exists in-repo — platform owns the deploy layer; must be confirmed before the M7 exit box is checked) |
| M8 | Metrics + dashboard | **DONE** (`/metrics` + `dashboards/data-processing.json`; per-graph-stage latency landed with the stage graph) |

**Sequencing reality:** M0/M3/M7(core)/M8 done; M1 needs its exit measurement; the
next unstarted charter work in order is M2 (text/image), then M4/M5/M6 interleave.

## Deferred (this slice)
Warm/pooled child processes for real-backend throughput under isolation; per-chunk
wall-clock kill knob (`INGEST_SUBPROC_TIMEOUT`); per-graph-stage metrics relay from the
child; dead-letter backfill tooling; `processed` retention (all pre-existing M7-proper
items remain tracked in [ws-dp-stage-graph.md](ws-dp-stage-graph.md) §Deferred).

## Worklog
- 2026-07-21 — Findings #6+#7 closed structurally (SlotView + writes/chaining +
  chain-order dialect; fingerprint guard deleted); finding #3 closed
  (permit-at-dispatch rewrite; EXPERIMENTAL warning removed); subprocess isolation
  landed (poison blast radius → one chunk; drain cancel → SIGKILL, journal row stays
  re-drivable). 23 tests added (stagegraph 19→28, +6 fairness incl. the HOL regression
  drill, +5 isolation incl. poison/ghost drills). Suite 128 → 151 green.
- 2026-07-21 — **Adversarial review workflow** over the full diff (47 agents: 5
  dimension reviewers → 2 refuters per finding; 2.4M tokens): 19 confirmed / 2
  refuted → **9 code fixes + 7 gap drills** (§4), incl. one HIGH empirically-reproduced
  starvation in the new dispatch and one HIGH event-loop stall in spawn isolation.
  Byte-identity re-proven post-fix (identical output digests vs main across dialects
  AND under isolation). Suite **163 green** (stable across repeat runs).
