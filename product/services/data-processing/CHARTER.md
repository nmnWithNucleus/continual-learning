# Data Processing Service — Charter

> The normalization layer of Nucleus v0: raw captured streams in, structured / timestamped /
> enriched records out. Stable doc — working state lives in [HANDOFF.md](HANDOFF.md);
> system-wide architecture + contracts in [../../ARCHITECTURE.md](../../ARCHITECTURE.md).
>
> **New to the service?** Start with the onboarding field guide —
> [onboarding/field-guide.html](onboarding/field-guide.html), a derived teaching view (D22)
> rewritten for the v1 world at Stage G — then come back here. The repo wins wherever the two
> disagree.

> ### ⚠️ STAGE: PROTOTYPE (pre-dev, pre-production) — D19, 2026-07-27
> This charter is written in a production voice. **It is aspirational, not a commitment.** We are
> building one end-to-end product that genuinely works, as fast as we can honestly get there.
> **Licensed:** re-cutting contracts rather than versioning them (a pinned shape is stable enough
> to build against today, never immutable — which is why we no longer call one *frozen*); wiping
> and re-collecting stored data rather than migrating it; deferring durability work with the
> reason written down.
> **Not licensed:** skipping [ORG.md](../../ORG.md)'s contract-edit order, leaving a decision
> unrecorded, silent breakage, or calling a thing BUILT when it is only ratified.
> Full posture + what changes at dev/prod: [ARCHITECTURE.md](../../ARCHITECTURE.md) §Stage.


**Status:** chartered · **Last updated:** 2026-08-07 (rebuild EXECUTED — Stages A–G on
`main`; the Slot Law is the running law, C2 v1 the live wire, `record-emission-law.md`
retired into §Condensed history; §On C2 / §Position / OQ10-14 flipped to the new world) ·
[D23](../../DECISIONS.md) / [D24](../../DECISIONS.md) / [D18](../../DECISIONS.md) /
[D19](../../DECISIONS.md) / [D20](../../DECISIONS.md)

- **2026-08-07** — rebuild EXECUTED (Stage G). The Slot Law stopped being "the statement
  of a law that becomes executable at Stage C" and became the law the running service
  enforces; C2 v1 is the live wire after the Stage F cutover; `docs/record-emission-law.md`
  is retired, its reasoning condensed into §Condensed history below; §On C2, §Position's
  C2 row, and OQ10/12/13/14 flipped from the v0 world to the ratified one.
- **2026-08-06** — §Slot Law L8's heal clause corrected at the Stage D close-out: the
  re-POST carries whatever the re-run produced; the ledger carries hole truth;
  convergence, not monotonicity, is the guarantee; non-green heals charge (was
  "replaces holey with fuller").
- **2026-08-06** — §Slot Law L5's as-emitted budget clause: the all-caps emphasis replaced
  with STYLE-rule-5-compliant italics (rebuild Stage D WP-D0 nit; wording only, no rule
  change).
- **2026-08-06** — the Slot Law ratified ([D23](../../DECISIONS.md)); the rebuild's contract
  rows D24–D28 ratified with it. Rebuild Stage A is complete.
- **2026-08-05** — §Slot Law drafted in place of §Record-vs-mutation law, on branch
  `dp-rebuild-v1` (rebuild Stage A).
- **2026-07-27** — C2 gains an additive-optional `discriminator`, surfaced so storage's day-log
  materialization can keep one dialect per record. The §Stage banner was added.
- **2026-07-25** — the WS-VC screen-video clip path was ratified, and OQ10 / OQ13 / OQ14 rewritten.

---

## Mission

Turn the raw, continuous capture of a pilot user's life — wearable body-cam A/V plus computer
screen/mic/browser capture, into structured, timestamped, enriched records ready for storage
and training. Every token the per-user model is ever fine-tuned on, and every interactive
request QueryBuilder assembles, passes through this service's pipelines: the quality ceiling of
the whole product is set here. One code path normalizes both the life stream (batch, C1→C2) and
interactive requests (synchronous, C8), so the model always sees data in one dialect.

---

## Scope — v0

### In scope
| Area | v0 treatment |
|---|---|
| Audio pipeline | denoise → **VAD (voice-activity gate)** → speaker diarization → ASR → translation → *acoustic-event captioning* (non-speech audio: ambient sound → tags/caption) |
| Text pipeline | normalization (encoding, whitespace, structure) |
| Image pipeline | ImgProc → **OCR-specialist pass** (legible text + where it sits in the frame) → dense captioning (OCR woven into the description) → world-data injection |
| Video pipeline | VidProc (chunking/windows) → **OCR-specialist pass** (per keyframe: legible text + location) → dense captioning (OCR woven in) → world-data injection |
| Timestamp injection | wall-clock timestamps woven into every record, **all modalities** — the cross-source time spine; concurrent activities from different devices must be alignable |
| World-data enrichment | geolocation, known-faces/people registry lookups, place/object tagging |
| /context writes | emit processed records to storage per **C2** |
| Sync pipeline API | expose the whole pipeline synchronously to QueryBuilder per **C8** |
| Observability | expose `/metrics` (Prometheus text) and own a Grafana dashboard JSON; the shared backbone is Platform's ([↓](#observability)) |

#### Observability
> `built` (M8) · [D9](../../DECISIONS.md)

**In one line.** We expose a `/metrics` endpoint in Prometheus text format and own a Grafana
dashboard JSON (`dashboards/*.json`); Platform owns the shared backbone.

**Rules**

- Emit the baseline request rate, latency and error rate.
- **Plus** pipeline throughput and queue depth per modality, per-stage latency (denoise, diarize,
  ASR, translate, the OCR pass, dense-caption, world-data injection), C8 sync-request latency, and
  enrichment counts.
- Shape: [../../ARCHITECTURE.md](../../ARCHITECTURE.md) §Observability.

### Out of scope (owning sibling)
| Area | Owner |
|---|---|
| Capture, device endpoints, upload/streaming to backend | Recording Service (`recording`) |
| Storage engine; the /raw, /context, /sessions stores and the model directory | Storage Service (`storage`) |
| UserPrompt assembly, chat templating (input *calls* our pipeline via C8) | Input Service (`input`) |
| Model serving, agentic harness, mentor protocol | Inference Service (`inference`) |
| Response delivery to devices | Output Service (`output`) |
| Fine-tuning cadence, adapters (entries published to the model directory via C5) | Continuum Service (`continuum`) |
| Shared infra: SLURM, GCS, CI/CD, observability | Platform Service (`platform`) |

---

## Position in the system

```
recording ──C1──▶ DATA PROCESSING ──C2──▶ storage /context ──▶ continuum (nightly fine-tune)
                        ▲
input QueryBuilder ─────C8 (synchronous; SAME code path as the stream)
```

Contract payloads are owned by [../../ARCHITECTURE.md](../../ARCHITECTURE.md) § Contracts —
referenced here by ID only, never redefined.

| Contract | Direction | Our role |
|---|---|---|
| **C1** | recording → us | **v0 pinned (D11).** Our sole ingest: the pushed raw-stream envelope ([↓](#on-c1)) |
| **C2** | us → storage | **v1 live (D24), the running wire since the Stage F cutover.** Our sole output for stream data: one slot-built processed record per chunk ([↓](#on-c2)) |
| **C8** | input ↔ us | serve the pipeline as a synchronous API, so interactive requests are normalized by the same code |

##### On C1

The envelope carries device, `stream_id`, `sequence`, `chunk_id`, modality, codec, wall-clock,
`blob_ref`, and optional location and clock fields. Delivery is at-least-once; we dedup on
`chunk_id`, order via `(stream_id, sequence)`, and pull bytes by `blob_ref`. Capture semantics
belong to `recording`.

##### On C2

`record_id` is deterministic on `(chunk_id, pipeline_version)` — NUL-joined, hex, a blind
`/context` upsert — matching the v1 schema
([../../contracts/c2_processed_record.v1.json](../../contracts/c2_processed_record.v1.json)).
There is exactly **one record per chunk** (Slot Law L2), and no discriminator: dropping it is
safe precisely because one-record-per-chunk is structural. The record carries source
provenance (verbatim from C1, minus modality), the C1 span `t_start`/`t_end`, `pipeline_version`,
and `content.slots` — a map keyed by slot name, one producing stage per slot, each written once
and never edited. Storage assigns `created_at`/`updated_at`; no wall-clock lives inside the
record (D27). The v0 shape — `content{kind,text,segments}`, a present-but-empty `enrichments`,
the within-chunk discriminator, `processed_at` — is gone; its reasoning is in §Condensed history.

Indirect consumers (no direct contract with us): `continuum` reads /context + /sessions via
**C10** (storage → continuum) — the day-log renderer walks `content.slots` (C10 v2) — which
changes nothing about our C2 obligations; `input` builds prompts from what C8 returns.

---

## v0 deliverables

| M | Deliverable | Exit criterion |
|---|---|---|
| **M0** | Walking skeleton: C1 fixture → pull blob by `blob_ref` → audio ASR → C2 record in /context, `pipeline_version` stamped and idempotent | an end-to-end integration test green against a storage dev target; the record validates against the C2 schema; a re-pushed `chunk_id` yields no duplicate record |
| **M1** | Full audio pipeline: denoise → diarize → ASR → translate; timestamps injected from the C1 envelope | Pilot body-cam + computer-mic sample processed; WER/DER measured on a labeled sample and published as baseline |
| **M2** | Text normalization + image pipeline (ImgProc → OCR-specialist pass → dense caption → world-data injection) | Screenshots/webcam frames from pilot computer capture land in /context with captions **and transcribed on-screen text (with frame location)**; spot-check review pass, incl. an OCR-heavy screen |
| **M3** | Video pipeline: VidProc chunking + dense describe, ported from POC Phase-2/3 machinery | Body-cam and screen-recording chunks described end-to-end; reviewed via an explorer-style spot-check tool |
| **M4** | Cross-source time spine: per-device skew handling, one per-user timeline | Two-device concurrent test capture aligns within a documented skew bound; alignment test in CI |
| **M5** | World-data enrichment: known-faces/people registry, geolocation, place/object tags | Registry-known faces tagged in pilot streams; geo/place tags from the C1 optional device-location field where captured, content-inferred otherwise |
| **M6** | C8 synchronous API — same pipeline code, interactive profile | `input` round-trips a multimodal request through C8; p95 latency within the budget agreed in ARCHITECTURE.md |
| **M7** | Production hardening: backpressure, dead-letter + backfill, reprocess-by-version | Kill/restart mid-stream loses zero records; a `pipeline_version` bump cleanly reprocesses one full pilot day |
| **M8** | Metrics + dashboard (D9): `/metrics` endpoint + Grafana dashboard JSON, per [../../ARCHITECTURE.md](../../ARCHITECTURE.md) §Observability | Service `/metrics` scraped by the shared Prometheus; dashboard shows request rate / latency / errors + pipeline throughput/queue depth per modality, per-stage + C8 latency, enrichment counts |

Order is strict M0→M3 (modality coverage first); M4–M7 may interleave after M3. M0's idempotence
is what makes redelivery safe: an envelope `chunk_id` yields a deterministic `record_id`, so a
reprocess is an upsert and not a duplicate.

---

## Slot Law (governance — the twelve laws of the rebuilt pipeline)

> **Ratified 2026-08-06 as [D23](../../DECISIONS.md); EXECUTED and running since the Stage F
> cutover.** It replaced §Record-vs-mutation law, whose subject matter (multi-record fan-out,
> in-place mutation) the rebuild deleted; that law's long-form reasoning is now condensed into
> §Condensed history below, and `docs/record-emission-law.md` is retired. **This law is
> executable, not aspirational:** the test spine T-1…T-6 (`tests/`) enforces it in CI, the
> executor structurally emits one record per chunk, and the schema states it — a violation is
> a red test, not a review note. Design + migration plan (now the historical record):
> [docs/refactor_dp_service.md](docs/refactor_dp_service.md).

- **L1 — Chunk purity.** A stage output is a pure function of this chunk's bytes plus code.
  Cross-chunk work belongs to continuum. No cross-chunk state in DP, ever.
- **L2 — One record per chunk, schema-hard.** Exactly one C2 record per
  `(chunk_id, pipeline_version)`: the executor structurally emits one, a test asserts it, the
  contract states it. Adding a second record per chunk requires forking the contract (C2 v2).
- **L3 — Identity.** `record_id = sha256(chunk_id ␀ pipeline_version)` — NUL-joined, hex, a
  blind `/context` upsert. No discriminator; L2 is why dropping it is safe.
- **L4 — Version law.** `pipeline_version` is the `+`-joined *sorted* list of every enabled
  stage's string `<stage>.v<S>-<backend>.v<B>[.exp-<code>]`, resolved before any stage runs.
  Plain string, never hashed; it states the *attempted* dialect.
  - Stage version `vS` bumps on contract changes: `needs`, slot name/shape, emit semantics,
    budget. Backend version `vB` bumps on implementation changes: model, weights, prompts,
    thresholds, any behavior.
  - **No output-affecting env knobs exist.** Every env var is operational-only (endpoints,
    replicas, timeouts, log levels). CI enforces this with a determinism matrix: fixed
    version strings + fixed bytes ⇒ byte-identical record across the whole settings matrix.
  - Experiments fork the dialect: an in-code A/B surfaces its treatment as `.exp-<code>` in
    the string. Invisible-to-identity experimentation is forbidden.
- **L5 — Slots.** `content.slots` is a map keyed by slot name: one enabled producer per slot
  (a resolve-time hard error), no stage edits another stage's slot, and every produced slot is
  emitted into the record.
  - A derived view (a speaker-aligned transcript) is a *new slot* from an ordinary stage whose
    `needs` name the inputs.
  - Slot values are JSON text/structure only, each under a byte budget declared in the stage
    file (`len(utf8(json(value)))`). The measured bytes are the slot as *emitted* — including
    the executor-stamped `version` key, i.e. exactly what the record carries. Raising a
    budget is a stage-version bump; exceeding it at assembly is a stage failure, never
    silent truncation.
  - Binary artifacts ride refs: bytes go to blob storage, the slot carries
    `{blob_ref, meta}`. Within a run the blackboard carries `{ref, bytes}` and the executor
    frees `bytes` when the last consumer finishes. Re-derivable heavy data (deterministic
    decode of `/raw` bytes) defaults to not-persisted.
- **L6 — One POST.** The blackboard fills in memory; exactly one atomic `POST /context` per
  completed graph attempt; ACK only after storage confirms (`dp_acked=1 ⇔ C2 durably
  written`, unchanged). Partial-record writes to storage are forbidden.
- **L7 — Required / optional.** Each stage declares `required: bool`, and the two failure
  paths differ by construction.
  - Required failure ⇒ no record: the chunk attempt fails ⇒ worker retry
    (`INGEST_MAX_RETRIES`) ⇒ durable dead-letter ⇒ visible in `/continuity.dead_lettered`, so
    recording's verdict reads `gaps`, never falsely clean.
  - Optional failure ⇒ the slot is absent (a *hole*), the downstream cone is cancelled,
    statuses are recorded, the record ships.
- **L8 — Done-ledger & healing.** The journal's done-row is
  `chunk_id → {pipeline_version, record_id, stage_status{name→ok|failed|cancelled}, heal_attempts}`.
  On (re)delivery: no row → process; version differs → version-forward reprocess, the new
  record landing *beside* the old; version matches all-green → skip (200 + `record_id`);
  version matches with holes and budget left → heal.
  - A heal is a full graph re-run re-POSTing the same `record_id`; the redelivery body
    carries the C1 envelope, so no stored envelope is needed.
  - The re-POST carries whatever the re-run produced — the ledger, not the record,
    carries hole truth, and convergence is the guarantee, not monotonicity.
  - Any non-green completed heal ⇒ `heal_attempts++` (a green heal never charges; a
    failed re-run charges via the failure path); at budget ⇒ holes permanent, row
    done-final, metric fires.
  - Heal recompute policy is recompute-all now. The done-row schema includes a nullable
    `cached_slots` column, specified and unpopulated, so run-only-the-failed-cone can be
    added later without a schema break.
- **L9 — Machinery / bureaucracy.** Every model (ASR, diarization, acoustic, OCR, any local
  VLM) is a long-lived model-server process — replicated, GPU-pinned via supervisor manifest,
  loaded once, health-checked, restart-on-crash.
  - Stages are thin clients (prepare request → call server/cloud → post-process into slot)
    with per-call timeouts and bounded transient retries against other replicas; ffmpeg
    remains a self-isolating subprocess.
  - *No model loads inside the DP process*, and no per-chunk child processes exist. The
    supervisor lives in DP for now; `isolation.py` is deleted with condensed history at
    Stage G.
- **L10 — Consumer & budget rule.** A slot ships with a named consumer-today, or an explicit
  `speculative` marker plus its byte budget. Every slot names its rough
  chars-per-second-of-life cost. CI screams at unbudgeted or megabyte slots.
- **L11 — Honesty.** Reading a record: stage name in `pipeline_version` + slot *absent* =
  attempted and failed (a hole); slot *present with empty value* = honest empty claim (OCR
  ran, the screen had no text); stage name *not in the dialect* = never attempted. Consumers
  never infer a negative from an absent slot under a dialect that did not attempt it.
  - Provenance corollary: a slot derived from another slot (OCR text injected into the
    caption prompt) is one witness on two channels — agreement is not corroboration.
- **L12 — Sub-array stability.** Arrays inside slots (keyframe-like structures) key their
  elements on a grid derived from the declared C1 span — never on model output, survivor
  ordinals, or decoder frame indices.

**Dead concepts, deleted with their subject matter:** primary/mutate/sidecar, `best_effort`
policy machinery, discriminator, `writes`/`mutable_slots`, SlotView, mutate chain edges, the
R1 fork rider + `R1_EXEMPT_SIDECARS`, T3/T5, `DP_DIALECT_FREEZE`, `INGEST_ISOLATION`,
per-unit `assemble()`, the `enrichments` present-but-empty block, `ProcessedUnit` fan-out.

---

## Condensed history — the v0 governance the Slot Law replaced

> The rebuild deleted a whole vocabulary along with the capabilities that needed it. This is the
> tombstone: one paragraph per retired concept — why it existed, and what killed it. The full
> retired text lived in `docs/record-emission-law.md` (Stage A → F); it is retired here at Stage G.
> This is the six-paragraph fold [refactor_dp_service.md](refactor_dp_service.md) §10 promised.

- **The record-emission law** (five ordered tests, five riders) existed to *govern* a model where
  one chunk could become many records and a record could be mutated in place. Governance is what
  you need when the shape permits abuse: the tests decided when a signal earned its own record and
  the riders policed forks, independence and honesty. The rebuild deleted both capabilities —
  one record per chunk (L2), slots written once and never edited (L5) — so there is nothing left
  to govern. The invariants the law argued for now hold by construction; the law is a tombstone.

- **Discriminators** existed to give sibling identity under fan-out: when one chunk emitted several
  records (video keyframes; an `ocr` beside a `caption`), `record_id = sha256(chunk_id ␀
  pipeline_version ␀ discriminator)` told them apart. With exactly one record per `(chunk_id,
  pipeline_version)` (L2/L3) there are no siblings to distinguish, so the third hash component is
  dropped — safely, because one-record-per-chunk is *why* it is safe. A caption and its OCR are
  now two slots in one record, not two records.

- **SlotView** existed for mutation-by-reference: when models ran *in-process* and stages shared
  Python memory, a later stage could edit an earlier stage's structure through a capability-proxy
  view. Once models became long-lived server processes (L9) and stages became thin clients
  passing JSON slots, there is no shared mutable object to proxy — each slot is written once by
  one stage and assembled into the record. SlotView, `writes`/`mutable_slots` and the mutate chain
  edges died with in-process mutation.

- **`INGEST_ISOLATION`** existed because models lived *inside* the DP process: a poison chunk that
  crashed a model took the service down, so an opt-in ran each chunk in a killable subprocess to
  bound the blast radius. When the machinery/bureaucracy split moved every model out to a supervised
  server (L9), the DP process holds no model to crash — a bad input fails one stage client, the
  supervisor restarts a dead replica, and `isolation.py` had nothing left to contain. Deleted (D26).

- **`DP_DIALECT_FREEZE`** existed because dialects were once env-flippable: an operator could change
  what a stage emitted through configuration, so a freeze flag was needed to pin the dialect across
  a cutover window. The no-output-affecting-knobs law (L4/D25) removed the premise — identity is
  composed from code (`<stage>.v<S>-<backend>.v<B>`), a behavior change is a version bump, and no
  env var moves a byte of a record. With nothing to flip, there is nothing to freeze. Deleted (D26).

- **The two-record video shape** was the D8 weave: a screen chunk produced a caption record and a
  separate OCR record, the OCR text also injected into the caption prompt for grounding. It was the
  most defensible fan-out (each record independently placeable and losable), which is exactly why it
  drove the discriminator and independence riders. The clip redesign (WS-VC) then the rebuild folded
  it into one record with a `caption` slot and an `ocr` slot (L5), keeping the D-09 grounding weave
  as the L11 provenance corollary (one witness on two channels) — the shape survives as slots, the
  second record does not.

---

## Open questions

**Engineering**
1. C1 delivery semantics — push vs pull, ordering, at-least-once + dedup key.
   **Resolved ([D11](../../DECISIONS.md), 2026-07-09): push, at-least-once.**

   **Rules**

   - We are **idempotent on `chunk_id`**, which is the dedup key.
   - Ordering and gap detection ride dense zero-based `(stream_id, sequence)`; any break is a lost
     chunk.
   - Recording writes the blob to `/raw` **first** (blob-first) and we pull the bytes by `blob_ref`
     for ASR.
   - We must tolerate a since-deleted blob, because deletion and re-pull both exist.
   - Pinned shape: `contracts/c1_raw_stream_envelope.v0.json`.
2. C8 latency budget vs pipeline weight: do interactive requests run a lighter captioning profile (same code, config-only difference), and what is the p95 target? Settle with `input`.
3. GPU placement for pipeline models (ASR, diarization, captioners): dedicated allocation vs sharing the a3-mega partition with `continuum`'s nightly window — contention policy needed.
4. ~~Device clock discipline: does `recording` guarantee synced wall-clock stamps, or must M4
   estimate skew from content?~~ **Resolved ([D17](../../DECISIONS.md), 2026-07-26).** Neither: the
   envelope *declares* its own discipline and we pass it through untouched.

   **Rules**

   - C1 carries `device_clock` (`synced|unsynced`), `device_tz` (IANA) and
     `device_utc_offset_minutes`; we copy all of them verbatim into C2 `source{}`.
   - We perform **no timezone or clock logic whatsoever** — no derivation, validation,
     normalization or inference.
   - The time spine is the device's UTC `t_start`/`t_end` exactly as reported.

   **Why it's this way**

   - A zone is a fact about where the user physically was, knowable only at the capture device and
     only at capture time. Anything we reconstructed downstream would be a guess dressed as data.
   - Skew stays *detectable* rather than *corrected*: `device_clock` flags an undisciplined stamp,
     `device_utc_offset_minutes` witnesses what the device believed, and storage's `ingest_time` is
     an independent clock.
   - **Consequence for the Slot Law:** these fields are envelope *provenance we forward*, not
     signals we produce, so the L10 consumer-today rule does not gate them — the same way
     `device_id` and `blob_ref` are not gated. (Under v0 this was the emission law's T2 test; the
     rule survived the rebuild as L10.)
   - M4 skew *estimation* remains available if a real unsynced-fleet problem appears, and would
     land as an additive corrected field beside the raw stamp, never overwriting it.
5. Reprocessing policy on `pipeline_version` bumps: reprocess history (cost) vs version-forward only — interacts with `continuum`'s training windows.
6. Known-faces/people registry — split is pinned in [ARCHITECTURE.md § Ownership splits](../../ARCHITECTURE.md#ownership-splits-pinned--cross-referenced-from-the-charters): we own matching/enrichment, `storage` persists the registry, `input` owns curation + consent UX. Still open here: what we cache locally and how registry edits invalidate that cache.

**Research**
7. Captioning granularity for continuous life streams: the POC ran 20/10/5/1-min targets at ≈$7.8k for 753 h — which operating point (granularities × model tier) fits a per-user-day budget?
8. Teacher vs self-hosted captioner: POC gold used a frontier API; acceptable for private life data, or self-host a VLM captioner? Privacy/cost/quality triangle — escalate to CTO.
9. Real-world-time verification: with device clocks in the C1 envelope, is deterministic envelope
   time enough, or do we keep the POC's content-based RWT reconstruction as a cross-check?
   **Substantially answered ([D17](../../DECISIONS.md), 2026-07-26): envelope time is enough, and
   is now auditable rather than merely trusted.**

   **Why it's this way**

   - The envelope carries the instant (`t_start`/`t_end` UTC), the civil-time context (`device_tz`,
     `device_utc_offset_minutes`), a discipline flag (`device_clock`), and dense
     `(stream_id, sequence)` continuity; storage adds an independent `ingest_time`.
   - That is enough to *detect* a bad clock from stored data, which is what a cross-check is for.
   - Content-based RWT reconstruction is therefore **not** a standing pipeline pass. It stays a
     diagnostic to reach for if the `device_clock` / `ingest_time` disagreement rate ever proves
     non-trivial on a real fleet.

   **Watch out for**

   - **Residual, genuinely open:** nobody measures that disagreement rate yet. A cheap `/metrics`
     counter on `unsynced` chunks and |`ingest_time` − `t_end`| outliers would close it properly.
10. Screen capture is OCR-heavy: dense captioning at ~205-px effective frames drops small text (POC
    token-budget math). **Resolved (CTO, [D8](../../DECISIONS.md)): decouple OCR from the base
    model.** *Resolved for screen video (WS-VC, 2026-07-25)* in the specifics below.

    **Rules**

    - A dedicated OCR-strong pass transcribes legible text plus its frame location; that text is
      woven into the description we write to /context, and returned via C8 for interactive turns
      → /sessions.
    - For screen video: a **CPU OCR model server** (`servers/ocr/`, PP-OCR-class det+rec, its own
      venv, under the supervised model-server fleet — L9) reads text; the frame width is a code
      pin in `clipprep`, not an env knob (L4). This is the `screentext` stage.
    - The captioner (`clipcap`) reads layout and never reads text.
    - **OCR is both injected into the caption prompt** (grounding, D-09, the L11 provenance
      corollary — one witness on two channels) *and emitted as its own `ocr` slot*, separable
      downstream — continuum renders a `World text (OCR):` line.
    - Cadence is **event-driven**, a binarized-change delta gate rather than a fixed clock;
      static-text dedup is within-chunk.

    **Why it's this way**

    - The user model learns on-screen text from the *description target*, not by reading pixels
      natively at inference, so base-model OCR quality (the D6 caveat) stops gating anything.

    **Watch out for**

    - Cost and frame-rate gates: **O-2**, the real-macOS-frame OCR bar, still governs the pilot's
      quality budget; `servers/ocr` now serves the real PP-OCRv4 det+rec (identity-checked), not
      a mock. The captioning window equals the C1 chunk, span-parametric, and 60 s is escalation E-1.
    - **Not screen:** which OCR model for body-cam or browser is a later per-scenario call. The
      prompt pack plus a new backend version (a `vB` bump — no env knob) make it a code change.
    - Residual engineering choices elsewhere: keyframe cadence for video, cost per screen-hour, and
      dedup of static text across frames.
11. Voice-to-person linking: diarization yields anonymous speaker labels; linking them to people-registry identities (known-vs-unknown speakers) rides the same registry, and the v0-vs-deferred call is ours ([ARCHITECTURE.md § Ownership splits](../../ARCHITECTURE.md#ownership-splits-pinned--cross-referenced-from-the-charters)). **Recorded: deferred — not in the M5 exit gate**; revisit if speaker embeddings already produced by the diarizer make matching cheap.
12. Non-speech and silence audio. ASR transcribes **speech only**, and Whisper hallucinates on
    non-speech or silence, so a chunk of pure ambient sound — a dishwasher, a car, a dog, yields
    nothing or garbage from ASR. *Decided 2026-07-09.*

    **Rules**

    - A **VAD gate runs before ASR**: run ASR only on speech regions, and mark a no-speech chunk
      explicitly as *present-but-quiet*.
    - That is **not** a transport gap or a lost chunk — the raw bytes are safely in `/raw`.
    - Non-speech audio that carries context is **captioned or tagged by an acoustic-event model**
      (`servers/ast`, the `acoustic` stage), the audio analogue of dense video captioning:
      "doing dishes", "driving", "dog nearby".
    - It lands in the record as its own **`acoustic` slot** beside `asr`/`transcript` (Slot Law
      L5), not as a second record.

    **Why it's this way**

    - Ambient sound is life-context signal, not noise to drop.
    - A chunk with both speech and ambient sound is still **one record**: `asr`/`transcript` and
      `acoustic` are separate slots in the same C2, each written by one stage. The v0 answer —
      two records told apart by a within-chunk discriminator, or ambient tags in `enrichments` —
      is gone with those concepts (§Condensed history); slots make the multi-signal chunk trivial.

    **Watch out for**

    - Residual: which VAD, which audio-tagging or captioning model, and cost per audio-hour.
13. Ingest processing mode. M0 `/ingest` processed **inline** — pull → run → C2 → store inside the
    request handler. *Resolved (2026-07-19, M7-early) and then made the operating default at the
    rebuild's Stage F:* async (`INGEST_ASYNC=1`) is the live mode after drill 1 paid the
    standing D16 async-default gate. Inline stays available and byte-identical (it is C8's
    skeleton). See [handoff/ws-async-observability.md](handoff/ws-async-observability.md).

    **Rules**

    - Async ACKs `202 {ok,accepted,chunk_id}` and enqueues onto a bounded worker pool, decoupled
      from capture cadence.
    - A redelivery of a done chunk still returns `record_ids` with a 200; an in-flight redelivery
      re-ACKs 202; a full queue is 503 backpressure.
    - Retry safety rides `chunk_id` dedup plus the deterministic `record_id` upsert; transient
      failures retry-then-dead-letter in the worker; a durable journal re-drives the pending set
      on restart (the in-flight-kill recovery witnessed at the Stage F soak).
    - The reply shape is an **inter-service wire decided jointly with recording** and recorded in
      *both* canvases, on the OQ4 precedent.

    **Why it's this way**

    - The `/ingest` reply still carries `record_ids:[…]` (a list), but under C2 v1 it is exactly
      **one id per chunk** (Slot Law L2) — the list shape is kept for the wire's stability, not
      because a chunk fans out. The v0 fan-out (many records per chunk — video keyframes) is gone.
    - The **"zero silent loss" invariant is preserved by a surgical recording change**: DP
      `/continuity` additively reports `processed` and `dead_lettered` per stream, and recording
      confirms `dp_acked=1` only on `processed`.
    - So an accepted-then-lost chunk reads `recording` or `gaps`, never a silent `clean`.

    **Watch out for**

    - The durable pending-journal + boot re-drive close the kill/drain-recovery half of M7 (proven
      at the Stage F cutover drills and the soak's in-flight kill). Backpressure + dead-letter are
      built; a `/raw`-replay backfill-by-version tool is the remaining owed piece (plan OD-2).
14. C2-additive gaps surfaced by the modality-seam pressure-test (2026-07-10). Both were
    **deferred, non-blocking, no version bump**, each owned by the modality that needs it. Both are
    now closed by WS-VC (2026-07-25).

    **(a) Video per-keyframe/sub-span timing — dissolved by the rebuild.**

    - **Was** — N keyframe records from one chunk shared the chunk `t_start`/`t_end`, so they
      collided on storage's `(user_id, t_start)` index.
    - **Changed** — the v0 fix was a per-`ProcessedUnit` timestamp hook in `build_c2`; the rebuild
      removed the problem at the root — a video chunk is now **one record** with a `caption` and an
      `ocr` slot (Slot Law L2), carrying the C1 span verbatim, so there are no sibling keyframe
      records to collide.
    - **Now** — one record per chunk, span verbatim; the collision cannot arise.
    - **Payoff** — the seam hook and the whole keyframe-fan-out shape are deleted, not maintained.

    **(b) Image and keyframe OCR frame-location (bbox) — deliberately not emitted.**

    - **Was** — `content` had no home for structured region geometry.
    - OCR *text* survives in the `ocr` slot; only the bbox was ever in question.
    - The planned fix was an additive optional field (`enrichments.text_regions` in the v0
      framing), schema-additive when a real OCR pass landed — parked as escalation **E-5**.
    - **Changed** — a real OCR pass landed (`servers/ocr`), and the decision was to *not* emit
      bbox to C2. The OCR stage uses region geometry internally — reading order plus a coarse
      region *role* woven into the text, then discards it.
    - **Now** — the additive edit (a `text_regions[]` field plus a root `quality{}`) is written up
      and parked as escalation *E-5*, to be taken in one additive commit when the first geometry
      or quality consumer exists. Under C2 v1 it would land as an additive slot/field, not an
      `enrichments` block (that block is gone).
    - **Payoff** — the bbox has *zero readers* in continuum today, which is exactly what Slot Law
      L10 (named-consumer-today or an explicit `speculative` marker) declines to store for.
    - The originally-planned fix is preserved rather than performed, and it stays schema-additive
      so records still validate whenever it lands.

---

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Frontier-API captioning cost scales with capture hours | Per-user-day cost blows the pilot budget | Tiered granularity; cheap bulk model + selective re-do (POC Phase-3 pattern); self-host path (Q8) |
| Raw life data sent to third-party caption/ASR APIs | Privacy/regulatory exposure | Self-host option; provider DPAs; escalated decision, recorded in ARCHITECTURE.md |
| Clock skew across devices | Misaligned time spine silently corrupts training data | M4 skew handling; alignment tests in CI; per-record time-confidence field |
| Mixed `pipeline_version` records in /context | `continuum` trains on inconsistent dialects | Version stamped in every C2 record; explicit reprocessing policy (Q5) |
| C8 path drifts from the batch path | Interactive requests stop matching the training distribution | One code path enforced; profiles are config-only; contract test diffs both paths on shared fixtures |
| GPU contention with `continuum` nightly training | Processing backlog; stale /context | Off-peak scheduling; explicit backlog tolerance; escalate allocation to `platform` |
| Corrupt/gapped capture (device offline, bad blobs) | Silent holes in the user's timeline | Dead-letter + gap records; idempotent re-pull via C1 refs; daily coverage report |

---

## Team shape

v0 = **one lead session + on-demand workstream agents**. As the service grows:

| Sub-team | Owns |
|---|---|
| Audio/text pipelines | denoise, diarization, ASR, translation, normalization |
| Vision pipelines | ImgProc/VidProc, dense captioning, the OCR path |
| Enrichment & time spine | world-data injection, registries, cross-source alignment |
| Backend/reliability | stream orchestration, C1/C2/C8 surfaces, CI/CD, backpressure, cost + observability |
| Research | captioning operating points, self-hosted captioner, RWT verification |

---

## Related work

- **[poc/live_stream_stability](../../../poc/live_stream_stability/README.md) — direct ancestor.**
  Phase-1 (download → ASR → diarize) prototypes the audio pipeline; Phase-2 (chunking + the
  20-min/1-fps operating point) prototypes VidProc; Phase-3 (dense describe with video-relative +
  real-world-time timestamps, multi-granularity, batch economics) prototypes video dense
  captioning + timestamp injection.
- Deep record: its HANDOFF.md + `experiments/phase3_describe/`.
- **[poc/recursive_finetuning_stability](../../../poc/recursive_finetuning_stability/HANDOFF.md)** —
  `continuum`-side lineage; relevant here for the shared operational conventions only: manifests
  as the spine, idempotent/resumable pipelines, GCS as source of truth for bulk data.
- Tooling proven in the POC: faster-whisper / WhisperX + pyannote (ASR + diarization), ffmpeg
  segment muxer (lossless chunking), Vertex Batch (bulk captioning at ~50% cost).
