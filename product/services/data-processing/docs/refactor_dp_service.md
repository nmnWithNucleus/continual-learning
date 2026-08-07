# DP Service Rebuild — Design & Migration Plan

**Status:** EXECUTED 2026-08-07 (D23–D28) — Stages A–G complete on `main`; the rebuild is live and this doc is now its historical record. Design consensus reached point-by-point in founder session 2026-08-05; migration decisions OD-1/2/3 ruled (§8); the §7 rows entered the register 2026-08-06 as amended by the Stage A cleanup round (rulings of 2026-08-06: `processed_at` dropped, Heard-lines `asr` fallback in, `device_clock` stays, empty slots map legal). The Slot Law is the running law; C2 v1 is the live wire since the Stage F cutover; `record-emission-law.md` is retired into the DP charter §Condensed history; the code demolition and doc rewrite are Stage G. **Execution record — the stages and their commit ranges:**

| Stage | What landed | Commit range (first WP → close) |
|---|---|---|
| A — Ratify & cut paper | D23–D28 into the register, CHARTER §Slot Law, C2 v1 + C10 v2 schemas | `f639fda` → `42e90a8` |
| B — Machinery | `servers/common` + supervisor + model client; whisper/pyannote/ast/ocr behind the seam | `3b70b68` → `4d70ecb` |
| C — New stagegraph | uniform `Stage`, executor rewrite, two-component id, C2 v1 assembler, T-1…T-6 spine; dead concepts + `isolation.py` deleted | `1589c0f` → `5f88fbb` |
| D — Ledger v2 | journal done-row (stage_status/heal_attempts), L8 claim tree, heal containment + crash-table (T-5) | `494e396` → `06b0534` |
| E — Storage v2 | `created_at`/`updated_at` byte-compare, C10 v2 slot-walk renderer, E-2 whole-record retraction, D20 parity re-baseline | `139b1ce` → `e3cedb6` |
| F — Cutover | vLLM + probe (F0a), continuum v2 stamps (F0b), cutover kit (F0c), GATE 1, merge `bf1e806` no-ff, five drills, the amended synthetic soak + in-flight kill + train leg | `413b3a6` → `26f763f` |
| G — Demolition & docs | sidecars/ + dead scripts + circuit.py + per-frame-v0 retired (clipcap vlm.v1→v2); emission law folded into §Condensed history; CHARTER/ARCHITECTURE/plan/field-guide/boards rewritten to the new world | `afe0103` → `242eb31` |
**Date:** 2026-08-05
**Scope:** everything downstream of chunk acceptance (the stage graph, record emission, C2 shape, model execution). The pre-graph machinery — journal, dedup claims, ingest queue, backpressure, continuity, blob-first pull, D16 reply wire — is explicitly KEPT and is the foundation this design stands on.
**Supersedes on ratification (all EXECUTED by Stage G):** CHARTER §Record-vs-mutation law; `docs/record-emission-law.md` (retired at Stage G, condensed into the DP charter §Condensed history); D10's C2-v0-shape clause; D16's fan-out `record_ids` clause; D19's "discriminator surfaced" clause; D8's shipped two-record shape (its one-liner — specialist OCR feeding the caption — survives as the L11 provenance corollary).
**Untouched:** C1 / D11, D15, D17 (tz trio rides `source{}` verbatim), D22, all recording-side decisions, storage's ingest-upsert core.

---

## 0. Intent

Replace the multi-record + in-place-mutation model — and the governance it required (the emission law's five ordered tests, five riders, primary/mutate/sidecar kinds, discriminators, SlotView capability proxies, the frozen exemption) — with a strictly simpler model whose invariants hold **by construction**:

- one chunk → **one C2 record**, built from **slots**, each written by exactly one stage, never edited;
- identity and versioning carried entirely by **code** (no output-affecting config knobs);
- models run as **long-lived server processes** (machinery); the DP service is a thin async orchestrator (bureaucracy);
- failure handling that **heals** instead of freezing holes, riding the existing journal/redrive machinery.

Target outcome: materially less code, fewer concepts (~20 coined terms → ~10), no loss of crash-safety, and a service a new engineer can hold in their head in a day.

---

## 1. The Laws (replaces the emission law; goes into CHARTER on ratification)

- **L1 — Chunk purity** *(old T1, unchanged)*. A stage output is a pure function of this chunk's bytes + code. Cross-chunk work belongs to continuum. No cross-chunk state in DP, ever.
- **L2 — One record per chunk, schema-hard.** Exactly one C2 record per `(chunk_id, pipeline_version)`. The executor structurally emits one; a test asserts it; the contract states it. Adding a second record per chunk requires forking the contract (C2 v2) — impossible to do casually.
- **L3 — Identity.** `record_id = sha256(chunk_id ␀ pipeline_version)` — NUL-joined, hex. Blind upsert on it. No discriminator (L2 is why that's safe).
- **L4 — Version law.** `pipeline_version` = the `+`-joined **sorted** list of every enabled stage's string `<stage>.v<S>-<backend>.v<B>[.exp-<code>]`, resolved before any stage runs (states the *attempted* dialect). Plain string, never hashed.
  - Stage version `vS` bumps on **contract** changes: `needs`, slot name/shape, emit semantics, budget.
  - Backend version `vB` bumps on **implementation** changes: model, weights, prompts, thresholds, any behavior.
  - **No output-affecting env knobs exist.** Every env var is operational-only (endpoints, replicas, timeouts, log levels). CI enforces this with a determinism matrix: fixed version strings + fixed bytes ⇒ byte-identical record across the whole settings matrix (§6, T-1).
  - **Experiments fork the dialect.** An in-code A/B (weblab-style) must surface its treatment in the string (`.exp-<code>`). Invisible-to-identity experimentation is forbidden.
- **L5 — Slots.** `content.slots` is a **map** keyed by slot name. One enabled producer per slot (resolve-time hard error). No stage edits another stage's slot — derived views (e.g. speaker-aligned transcript) are **new slots** from ordinary stages whose `needs` reference the inputs. Every slot is emitted into the record. Slot values are JSON text/structure only, with a **byte budget declared in the stage file** (`len(utf8(json(value)))`); raising a budget is a stage-version bump; exceeding it at assembly is a stage failure, never silent truncation. **Binary artifacts ride refs**: bytes go to blob storage, the slot carries `{blob_ref, meta}`; within a run the blackboard carries `{ref, bytes}` and the executor frees `bytes` when the last consumer finishes. Re-derivable heavy data (deterministic decode of `/raw` bytes) defaults to not-persisted.
- **L6 — One POST.** The blackboard fills in memory; exactly one atomic `POST /context` per completed graph attempt; ACK only after storage confirms (`dp_acked=1 ⇔ C2 durably written`, unchanged). Partial-record writes to storage are forbidden.
- **L7 — Required / optional.** Each stage declares `required: bool`. Required failure ⇒ **no record**, chunk attempt fails ⇒ worker retry (`INGEST_MAX_RETRIES`) ⇒ durable dead-letter (poison chunk) ⇒ visible in `/continuity.dead_lettered` ⇒ recording's verdict reads `gaps` (never falsely clean). Optional failure ⇒ slot absent (a **hole**), downstream cone cancelled, statuses recorded, record ships.
- **L8 — Done-ledger & healing.** The journal's done-row is `chunk_id → {pipeline_version, record_id, stage_status{name→ok|failed|cancelled}, heal_attempts}`. On (re)delivery:
  1. no row → fresh: process.
  2. row exists, **version differs** → fresh: version-forward reprocess; new record lands *beside* the old.
  3. version matches, all green → **skip** (200 + record_id).
  4. version matches, holes, budget left → **heal**: full graph re-run (the redelivery body carries the C1 envelope, so no stored envelope is needed), re-POST same `record_id`.
     - The re-POST carries whatever the re-run produced — the ledger, not the record, carries hole truth; convergence is the guarantee, not monotonicity.
     - Any non-green completed heal ⇒ `heal_attempts++` (a green heal never charges; a failed re-run charges via the failure path); at budget ⇒ holes **permanent**, row done-final, metric fires.
     - *(Clause corrected to the built truth at the Stage D close-out, 2026-08-06; was "upsert replaces holey with fuller; same stage fails again ⇒ heal_attempts++".)*
  - Heal recompute policy: **recompute-all now**; the done-row schema includes a nullable `cached_slots` column (specified, unpopulated) so run-only-the-failed-cone can be added later without a schema break.
- **L9 — Machinery / bureaucracy.** Every model (ASR, diarization, acoustic, OCR, any local VLM) is a **long-lived model-server process** — replicated, GPU-pinned via supervisor manifest, loaded once, health-checked, restart-on-crash. Stages are **thin clients** (prepare request → call server/cloud → post-process into slot) with per-call timeouts and bounded transient retries against other replicas. ffmpeg remains a subprocess (self-isolating). **No model loads inside the DP process.** The supervisor lives in DP for now. No per-chunk child processes; `isolation.py` is deleted, with condensed history in the handoff.
- **L10 — Consumer & budget rule** *(old T2 + R5)*. A slot ships with a named consumer-today, or an explicit `speculative` marker plus its byte budget. Every slot names its rough chars-per-second-of-life cost. CI screams at unbudgeted or megabyte slots.
- **L11 — Honesty** *(old R3)*. The dialect states what was attempted. Reading a record: stage name in `pipeline_version` + slot **absent** = attempted and failed (hole); slot **present with empty value** = honest empty claim (e.g. OCR ran, screen had no text); stage name **not in dialect** = never attempted. Consumers never infer a negative from an absent slot under a dialect that didn't attempt it. *Provenance corollary (old R2c2):* a slot derived from another slot (OCR text injected into the caption prompt) is one witness on two channels — agreement is not corroboration.
- **L12 — Sub-array stability** *(old R4 residue)*. Arrays inside slots (keyframe-like structures) key their elements on a grid derived from the declared C1 span — never on model output, survivor ordinals, or decoder frame indices.

Dead concepts, deleted with their subject matter: primary/mutate/sidecar, `best_effort` policy machinery, discriminator, `writes`/`mutable_slots`, SlotView, mutate chain edges, R1 fork rider + `R1_EXEMPT_SIDECARS`, T3/T5, `DP_DIALECT_FREEZE`, `INGEST_ISOLATION`, per-unit `assemble()`, the `enrichments` present-but-empty block, `ProcessedUnit` fan-out.

---

## 2. C2 v1 record

```jsonc
{
  "contract": "C2", "version": "1",
  "record_id": "sha256(chunk_id ␀ pipeline_version)",
  "user_id": "nmn",
  "modality": "audio",                       // root-level; C1 chunks are strictly single-modality
  "source": {                                 // provenance, verbatim from C1 (minus modality)
    "device_id": "...", "stream_id": "...", "chunk_id": "...", "blob_ref": "raw/...",
    "device_tz": "Asia/Tokyo", "device_utc_offset_minutes": 540   // D17 trio, verbatim, optional
  },
  "t_start": "2026-07-19T17:04:10Z",          // C1 span strings carried VERBATIM (D-05 rule)
  "t_end":   "2026-07-19T17:04:22Z",
  "pipeline_version": "acoustic.v1-ast.v1+asr.v2-fw.v3+diarize.v1-pyannote.v1+speaker_align.v1-builtin.v1",
  "content": {
    "slots": {
      "asr":          { "version": "asr.v2-fw.v3", "language": "en", "value": "...", "splits": [ { "t_start": "...", "t_end": "...", "value": "..." } ] },
      "diarization":  { "version": "diarize.v1-pyannote.v1", "splits": [ { "t_start": "...", "t_end": "...", "speaker": "speaker-0" } ] },
      "transcript":   { "version": "speaker_align.v1-builtin.v1", "splits": [ { "t_start": "...", "t_end": "...", "value": "...", "speaker": "speaker-0" } ] },
      "acoustic":     { "version": "acoustic.v1-ast.v1", "values": ["keyboard typing"], "confidence": 0.87 }
    }
  }
}
```

Schema rules: strict (`additionalProperties: false`) at every level; each slot type gets a typed sub-schema added **additively** when its first producer ships; slot names = the producing stage's declared slot name (default: stage name). Storage-side timestamps (`created_at`, `updated_at`) are **not** in C2 — storage assigns them (§5). `t_start`/`t_end` remain the sole ordering axis; sub-slot `splits[]` carry absolute RFC3339 times for C10's sub-span bucketing.

Video chunk example slots: `caption` (from `clipcap`), `ocr` (from `screentext`; `value: ""` = ran-and-empty), plus optional ref-slots for persisted artifacts. Exactly one record, same as audio.

---

## 3. Runtime architecture

```
                        ┌──────────────  DP service (asyncio, one process)  ─────────────┐
 recording ──C1──▶ /ingest ─▶ journal.accept ─▶ dedup/heal decision (L8) ─▶ queue        │
                        │        (durable)          │200/202/503 (D16 wire, unchanged)   │
                        │   worker task per chunk (modality-fairness permits, unchanged) │
                        │     resolve graph → pipeline_version (pre-run)                 │
                        │     stage tasks by readiness (TaskGroup):                      │
                        │       thin clients ──HTTP──▶ model servers / cloud APIs        │
                        │     blackboard (in-mem slots) → assemble → ONE POST /context   │
                        │     journal.mark_processed (statuses) → ACK/confirm            │
                        └────────────────────────────────────────────────────────────────┘
 supervisor (in DP): reads manifest {model, replicas, gpu} → spawns/health-checks/restarts
   servers/whisper  servers/pyannote  servers/ast  servers/ocr   (long-lived, warm, replicated)
```

- **Working units:** each *model* is a process (pooled); each *chunk* is an asyncio task; each *stage* is a step inside it. No per-chunk child processes.
- **Failure modes:** replica dies mid-call → transient retry on another replica (in-memory, cheap). All replicas fail an input identically → deterministic failure → L7. Chunk task dies → journal redelivers. DP dies → journal recovery on restart (kill-9 path, kept). Server hangs → client timeout (per-call, in the stage client). VRAM leak → supervisor restarts replica.
- **Batching (later):** servers may batch across chunks — the architecture supports it; not built in v1.
- Mock backends for tests are **client-level fakes** (no server spawned), selected the same way real backends are — by name, in the version string.

---

## 4. Crash truth table (consensus record)

| Crash point | Outcome |
|---|---|
| before journal.accept | no ACK ever sent → recording retries; nothing lost |
| after accept, before processing | restart recovery re-enqueues from journal |
| mid-graph | attempt fails → worker retry budget → dead-letter; blackboard was a cache of deterministic work |
| after POST, before mark_processed | redelivery reprocesses fully → byte-identical output → upsert no-op → converges; epoch fencing stops zombie writers |
| model server crash | contained to server; client retries other replicas |

The byte-identical claim holds by construction: no wall-clock field exists inside the record
(`processed_at` dropped, ruled 2026-08-06 — it broke the §5.1 byte-compare and T-1), so a full
reprocess under fixed versions is byte-identical per T-1.

---

## 5. Storage-side changes (joint-ratification set)

1. **`ingest_time` → `created_at` + `updated_at`.** `created_at` = first landing of a `record_id` (old semantics). `updated_at` = last time `record_json` **actually changed** — the upsert byte-compares and bumps `updated_at` only on real change (no-op redeliveries must not re-window records). Training-window membership and the day-log dedup axis move to `updated_at`. Healed records therefore flow into the next window (accepted double-training, same class as version bumps — the correction wins).
2. **Day-log (C10 v2).** Renderer walks `content.slots` instead of per-kind records. Dedup key: latest `updated_at` per `(chunk_id)`, rowid tiebreak. Line routing: `slots.caption` → Scene, `slots.ocr` → World text (OCR), `slots.transcript` → speaker-bucketed transcript lines via `splits[]`. Speech lines render from `slots.transcript`; when absent, from `slots.asr` (speakers unlabeled; ruled 2026-08-06). `recipe_id`/`daylog_format_version` bump; continuum's stamp-refusal is the transition safety net.
3. **E-2 retraction** redesigned as whole-record operations (delete by `record_id` / `chunk_id` / `pipeline_version`; manifest by `pipeline_version`) — simpler than the kind-granular design, and finally built (§8 Stage E).
4. D20 parity bar re-baselined against the v2 renderer.

---

## 6. New test spine (the law is executable or it is decoration)

- **T-1 Determinism matrix** (L4): fixed bytes + fixed versions ⇒ byte-identical record across the env-var matrix; any knob that moves bytes = red.
- **T-2 One-record guard** (L2): the executor cannot emit ≠1 record; schema-hard assertion.
- **T-3 Version composition** (L4): sorted join; stage-set change ⇒ string change; `needs` change without `vS` bump = red (registration snapshot test).
- **T-4 Slot law** (L5): one producer per slot; budgets enforced; binary-in-slot = red.
- **T-5 Ledger/heal flows** (L7/L8): version-mismatch reprocess; skip; heal; heal-budget exhaustion → permanent holes + metric; crash-point table above replayed.
- **T-6 Honesty** (L11): hole vs empty-claim vs not-attempted distinguishable from record + dialect alone.
- Kept suites: journal, dedup (edited), async ingest, continuity, fairness, parse, delta, clipprep internals, prompt pack, civil-time passthrough (v1 shape), metrics core.
- Deleted suites: `test_emission_law`, `test_discriminator`, `test_legacy_dialect`, `test_isolation`, legacy video halves.

---

## 7. Decision-register work (ratified 2026-08-06 as D23–D28; drafted at Stage A as D-R1…D-R6)

- **D23** *(was D-R1)* Ratify the Slot Law (§1 L1–L12); retire the WS-VC emission law + riders (charter edit, same ceremony that ratified it).
- **D24** *(was D-R2)* C2 v1 (§2) supersedes D10's shape clause; D19's discriminator clause retired; D16's fan-out clause restated (exactly one derivable id per chunk — strengthened); partially supersedes D8 (shipped two-record shape; its one-liner (specialist OCR feeding the caption) survives).
- **D25** *(was D-R3)* Version law (L4): no-knobs discipline, stage/backend split, experiments-fork-the-dialect.
- **D26** *(was D-R4)* Machinery/bureaucracy split (L9); `isolation.py` + `INGEST_ISOLATION` + `DP_DIALECT_FREEZE` retired with condensed history.
- **D27** *(was D-R5)* Heal ledger (L8) + storage `created_at`/`updated_at` (§5.1) — joint row with storage.
- **D28** *(was D-R6)* C10 v2 + E-2 whole-record retraction (§5.2–5.3) — joint row with storage; D20 parity re-baseline.

---

## 8. Migration plan

> **Ruled by founder, 2026-08-05:**
> - **OD-1 Migration shape → big-bang beside-build.** v1 is built on a branch while the old service keeps running untouched; cutover is one deploy + wipe; no dual-path flag code ever exists.
> - **OD-2 Historical data → fresh-forward.** At cutover, wipe `/context` + DP journal (D19 license); `/raw` is kept (bytes are sacred). The `/raw`-replay backfill tool is built later and doubles as the owed reprocess-by-version tool.
> - **OD-3 Model-server rollout → all four at once.** whisper, pyannote, ast, ocr all move behind the server seam in Stage B; no half-migrated calling conventions.

Stages are sequential; work-packages (WPs) inside a stage can run as parallel agents where marked ∥. Every WP ends with its tests green and a one-line worklog entry.

**Stage A — Ratify & cut paper (S).**
WP-A1: D-rows D23…D28 (drafted as D-R1…D-R6) into `product/DECISIONS.md`; CHARTER §Slot Law written; ARCHITECTURE C2/C10 cards updated. WP-A2: `contracts/c2_processed_record.v1.json`, `contracts/c10_daylog.v2.json` (README contract-edit order respected). No runtime change. **Exit:** founder sign-off on this doc + schemas — given 2026-08-06.

**Stage B — Machinery (M).** ∥ after B1
WP-B1: server framework (`servers/common`: FastAPI skeleton, health, warmup, request schema) + `app/supervisor.py` (manifest → spawn/health/restart) + `app/model_client.py` (replica pick, timeout, bounded transient retry).
WP-B2∥: `servers/whisper` (move `asr/faster_whisper.py` model-side). WP-B3∥: `servers/pyannote`. WP-B4∥: `servers/ast`. WP-B5∥: `servers/ocr` (relocate `sidecars/ocr`, unify into the framework).
**Exit:** all servers pass health + golden-output smoke on node-7; old service still running untouched.

**Stage C — New stagegraph (L — the long pole).**
WP-C1: `stagegraph/stage.py` rewrite: uniform `Stage{name, modality, stage_version, backend, needs, slot, required, budget, run_sync|run_async}`; registration checks (unique slot per modality, exactly-one-of run methods). WP-C2: `executor.py` rewrite: keep readiness TaskGroup, commit-on-success, cancel-and-await, leaf re-raise; add sorted version composition, single-record assemble, budget enforcement; delete kinds/chains/SlotView/units. WP-C3: `pipeline.py` (two-component id, v1 assembler; port D-05 verbatim-span rule). WP-C4∥: audio stages as thin clients + new `speaker_align`. WP-C5∥: video stages (`clipprep` mostly intact; `screentext`/`clipcap` clients; delete `keyframes.py`/`captions.py`/`emit.py`, porting `render_caption` + caps into clipcap). WP-C6: new test spine T-1…T-6.
**Exit:** full v1 suite green with mock + real backends on node-7; audio and video e2e produce valid v1 records against a scratch storage.

**Stage D — Ledger v2 (M).**
WP-D1: journal done-row extension (`stage_status`, `heal_attempts`, `cached_slots` nullable; version-compared lookup). WP-D2: dedup claim tree (fresh/version-mismatch/skip/heal/in-flight) + `ingest_core` one-POST emit. WP-D3: heal-flow + crash-table tests (T-5).
**Exit:** redelivery matrix green: skip, version-forward, heal, heal-exhaustion, poison.

**Stage E — Storage v2 (M).**
WP-E1: `db.py` `created_at`/`updated_at` + byte-compare bump. WP-E2: `daylog.py` v2 renderer (slot-walk, `(chunk_id)` key, transcript bucketing via `slots.transcript.splits`). WP-E3: E-2 whole-record retraction endpoint + manifest. WP-E4: parity re-baseline (D20).
**Exit:** hand-posted v1 records render a correct day-log; retraction drill passes.

**Stage F — Cutover (S/M).**
Freeze old DP → wipe `/context` + DP journal (per OD-2), keep `/raw` → deploy v1 fleet + supervisor manifest → resume ingest → run the three drills: (1) D16 re-drive drill (owed anyway — the standing gate), (2) version-bump drill (bump one `vB`, verify beside-semantics + next-window), (3) heal drill (kill one optional server mid-day, watch holes heal on redrive). Recording never stops capturing; its gap-report reconciliation stays as the safety net during the wipe (that's exactly why it was retained).
**Exit:** one full pilot day captured → processed → day-log rendered → trained end-to-end on v1.

**Stage G — Demolition & docs (S/M). EXECUTED 2026-08-07 ([refactor_stage_G.md](refactor_stage_G.md)).**
Deleted: `sidecars/` (live OCR is `servers/ocr`), `smoke_audio_backends.py`, the offline-eval harness (`prompt_ab.py` + `capture_chunkset.py` + `oracle_gemini.py`) with its rebuild path named, `app/vision/circuit.py` (wired nowhere), the legacy `per-frame-v0` prompt pack (clipcap `vlm.v1→v2`); `isolation.py` and the dead config knobs went at Stage C. Docs: CHARTER rewritten to the new world with a **§Condensed history** (the brief placed the six-paragraph fold in the charter, not the handoff); `record-emission-law.md` retired; ARCHITECTURE §Vocabulary + C2/C10 cards flipped; the field guide rewritten (D22: repo wins, same-session correction); the four service boards + the founders' board moved the rebuild to history and seeded the next phase; this doc flipped to `Status: EXECUTED`.
**Exit (met):** the dead-vocabulary grep (`discriminator|mutate|sidecar|best_effort|DIALECT_FREEZE|INGEST_ISOLATION|ProcessedUnit|emission law`) returns hits only in history sections, worklogs, and this plan doc; all suites green (DP/storage/continuum/servers-common); the live v1 fleet answered 200 before and after every commit.

---

## 9. File disposition (ruthless pass — verdicts to be confirmed line-by-line at execution)

### `app/` core
| File | Verdict |
|---|---|
| `main.py` | KEEP, edit (drop isolation/freeze wiring) |
| `config.py` | KEEP, **shrink hard** — operational-only knobs survive; output-affecting knobs migrate into backend code (L4) |
| `ingest_core.py` | REWRITE small (one-POST emit) |
| `ingest_queue.py` | KEEP, edit (heal-claim job type; drop isolation dispatch) |
| `journal.py` | KEEP, extend (done-row columns; version-compared lookup) |
| `dedup.py` | REWRITE small (L8 claim tree) |
| `isolation.py` | **DELETE** |
| `pipeline.py` | REWRITE small (L3 id; v1 assembler) |
| `continuity.py`, `storage_client.py`, `timeutil.py`, `models.py` | KEEP |
| `metrics.py` | KEEP, extend (heal/hole/server-call counters) |
| `schemas.py` | REWRITE (C2 v1 mirror — "a contract edit is one change with four parts") |

### `app/stagegraph/` + `app/stages/`
| File | Verdict |
|---|---|
| `stagegraph/stage.py` | REWRITE (~⅓ size: uniform Stage) |
| `stagegraph/executor.py` | REWRITE (~½ size: keep scheduler core; delete kind/mutate/SlotView/assembly machinery) |
| `stagegraph/processor.py` | KEEP (public seam) |
| `processing/*` | FOLD — `ProcessedUnit` fan-out dies; modality routing merges into stage registry |
| `stages/audio/asr.py`, `diarize.py`, `acoustic.py`, `translate.py`, `injected_caption.py` | REWRITE as thin clients; **NEW** `speaker_align.py` |
| `stages/video/keyframes.py`, `captions.py` | **DELETE** (legacy graph + with it the R1 exemption) |
| `stages/video/clipprep.py` | KEEP mostly (ffmpeg passes; ref-slots for artifacts) |
| `stages/video/screentext.py`, `clipcap.py` | REWRITE as clients (clipcap absorbs `render_caption`, D-11/D-12 caps, D-05 span rule) |

### model code → `servers/`
| From | To |
|---|---|
| `asr/faster_whisper.py` | `servers/whisper` |
| `audio/diarize/pyannote.py` | `servers/pyannote` |
| `audio/acoustic/ast.py` | `servers/ast` |
| `sidecars/ocr/` + `vision/ocr/ppocr.py` | `servers/ocr` |
| mocks (`*/mock.py`) | client-level fakes (no server) |
| `vision/emit.py` | **DELETE** (two rules ported: D-05 verbatim spans → pipeline.py; L12 grid rule → law text + T-4) |
| `vision/frames|delta|parse|mode|budget|circuit|clip*.py` | KEEP (clipprep/clipcap internals) |
| `vision/prompts/*` | KEEP (packs become backend-versioned assets; LOCK survives) |
| `vision/version.py` | REWRITE → version-composition module |

### Elsewhere
Storage: `db.py` (§5.1), `daylog.py` (§5.2), E-2 (§5.3), tests. Contracts: v1/v2 files + README/ARCH cards. Tests: per §6. Docs: per Stage G.

---

## 10. Condensed history (to be written into the handoff at Stage G)

One paragraph each, no code kept: why the emission law existed (multi-record + mutation needed governance; both capabilities deleted), why discriminators existed (sibling identity under fan-out), why SlotView existed (mutation-by-reference when stages shared memory with in-process models), why `INGEST_ISOLATION` existed (models lived in-process; died when models became servers), why `DP_DIALECT_FREEZE` existed (env-flippable dialects; died with the no-knobs law), and the two-record video shape (D8 weave → clip redesign → slots).
