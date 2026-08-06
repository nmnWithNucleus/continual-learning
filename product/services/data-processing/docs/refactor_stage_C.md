# DP Rebuild — Stage C worklog (New stagegraph)

**Stage:** C — New stagegraph · **Status:** DONE 2026-08-06 · *Dated:* 2026-08-06
**Branch:** `dp-rebuild-v1` · **Plan:** [refactor_dp_service.md](refactor_dp_service.md) §8 Stage C
**Scope:** WP-C0 (live-service pre-flight) · WP-C1 (`stagegraph/stage.py` rewrite) ·
WP-C2 (`executor.py` rewrite) · WP-C3 (`pipeline.py` / `schemas.py` / `config.py`) ·
WP-C4∥ (audio stages as thin clients + `speaker_align`) · WP-C5∥ (video stages) ·
WP-C6 (test spine T-1…T-6 + real-backend e2e). Laws L1–L12 (D23–D28) govern every line.

Carried-over instructions honoured this stage (from Stage A/B "Noticed for later
stages"): T-3 owns `pipeline_version` sortedness; T-3/T-4 assert each emitted
`slot.version` equals its stage's dialect segment; mock-dialect fixtures distinct from
real ones; `schemas.py` mirror is one change with four parts; the OCR client rewire
mapping (old `/ocr` wire + `model_sha_det/rec` → `identity.weights.det_sha256/rec_sha256`
+ the `/infer` envelope); `app.state.vlm_pool` as the ModelClient hand-off hook; AST
caption folding client-side with `ACOUSTIC_TOP_K/THRESHOLD` becoming stage-code pins;
whisper's deliberate dialect change (large-v3/cuda/fp16 vs v0's base/cpu/int8) reflected
in the asr stage's `vB`; carried hardening (starve-proof `/health`, `client_timeout_s`
wire-or-delete, replica re-verify after respawn).

---

## WP-C0 — Live-service pre-flight (v0 moved to a main-pinned worktree)

**The hazard:** the live v0 DP service (uvicorn `:8085`, launched from
`platform/deploy/.venv-learn` by `run_learn.sh`) ran with its working directory inside
this repo's `product/services/data-processing/` — the tree Stage C rewrites. It held v0
code in memory, but any restart would have imported a half-rewritten `app/`.

**Deploy mechanism read first** (`platform/deploy/README-learn.md` + `run_learn.sh`):
plain bash launcher — `( cd "$SERVICES_ROOT/$name" && bash run.sh )` per service,
pidfiles under `deploy/run-learn/`, `learn.env` sourced with `set -a`, no
systemd/supervisor. Matches the plan; no escalation needed. Two mechanism facts made
the repoint clean and surgical:

- `SERVICES_ROOT` is env-overridable (`: "${SERVICES_ROOT:=…}"` runs *after* sourcing
  `learn.env`), so a `learn.env` entry durably repoints every future launch. The
  deploy self-test is immune (it forces `ENV_FILE=…/nonexistent.env`).
- `start_service` *adopts* an already-healthy service instead of restarting it, so a
  bring-up with only DP down restarts only DP — storage `:8083` and recording `:8084`
  were never touched.

**Journal finding:** `dp_var_dir` defaults to `<tree>/var` resolved from `__file__`
(`app/config.py:42`), i.e. it would silently move with the code tree. Empirically
`product/services/data-processing/var/` was **empty** — v0 runs `ingest_mode: inline`
and never created `dp.db` — so there was no journal state to migrate and nothing
pending to recover; recording's at-least-once redrive covers anything in flight.
`DP_VAR_DIR` is now pinned explicitly (worktree `var/`, the same value the worktree's
default resolves to) so the journal location can never drift again.

| Action | Detail |
|---|---|
| Worktree | `git worktree add /home/ubuntu/nmn/dp-v0-live main` → main @ `9307b7e`, own `var/` created |
| Repoint | `learn.env` (gitignored, operational-only) += `SERVICES_ROOT=/home/ubuntu/nmn/dp-v0-live/product/services` + `DP_VAR_DIR=…/dp-v0-live/…/data-processing/var`, dated comment |
| Restart | one tight window: `kill -TERM 3356422` → port free in <1 s → `bash run_learn.sh --skip-install` → healthy |

**Evidence (2026-08-06):**

- Before (06:15:39Z): `:8085` → 200 `{"ok":true,"asr_backend":"faster_whisper",…}`,
  pid 3356422, cwd → `…/continual_learning/product/services/data-processing`.
- Window: TERM 06:16:25Z → `data-processing healthy (pid 3835816)` 06:16:30Z (~5 s).
- After: pid 3835816, cwd → `/home/ubuntu/nmn/dp-v0-live/product/services/data-processing`,
  env carries the same wiring (`ASR_BACKEND=faster_whisper ASR_LANGUAGE=en` + pinned
  `DP_VAR_DIR`), `:8085` → 200, same health body.
- Siblings untouched: storage still pid 3356393, recording still pid 3356458 (compared
  via `ss -tlnp` before/after).
- Chunk processed after restart: fresh 7 s WAV (unique bytes, so dedup could not
  shortcut) → `POST :8084/capture/run` (`chunk_seconds=4`) → 2 chunks emitted, 2
  record_ids returned; `GET :8083/context/records/10228cf4…` → HTTP 200, a C2 **v0**
  record (`content.kind: transcript`, `pipeline_version: asr-fw-v1`) — the v0 wire,
  byte-for-byte the old world, now served from the worktree. Empty transcript is the
  honest result for a synthetic tone under VAD.

v0 is now immune to anything Stage C does to this tree. The single authorized restart
is spent; no further v0 restarts this stage.

## WP-C1 — `app/stagegraph/stage.py` rewrite (uniform Stage)

| File | Action | Why |
|---|---|---|
| `app/stagegraph/stage.py` | REWRITTEN (326 → ~250 lines) | the uniform Stage{name, modality, stage_version, backend, needs, slot, required, byte_budget, one-of run_sync\|run_async}; registration checks (unique slot per modality, one-of run methods, segment grammar); kinds/mutate/writes/mutable_slots/SlotView/best_effort/order/version_fragment/enabled/assemble/R1 machinery all deleted |
| `app/stagegraph/__init__.py` | edited | exports the new stage surface only; executor/processor exports return with WP-C2/C3 |
| `tests/test_stage_registry.py` | created (TDD, red→green) | 59 checks: the uniform declaration, slot default, segment composition (+ `.exp-` codes), backend-override-at-construction (mock named in the version string), every registration rejection, dead concepts absent from module and class |

In-session decisions (things the plan left to code):

- **`Backend(name, version)` frozen dataclass + constructor override.** "backend …
  resolved in code" is realized as an in-code default on the class plus a
  constructor override (`AsrStage(backend=Backend("mock", 1))`) — how tests and any
  future offline harness select fakes/experiments. Selection is always code; the
  dialect always names it (plan §3's mock rule).
- **Grammar enforced at registration**: stage/backend/slot/experiment names must
  match `[a-z0-9_]+` — the same grammar the v1 contract's `pipeline_version` /
  `slot_version` regexes pin, so an illegal segment cannot exist at runtime.
- **`server` attribute (operational only).** Thin clients need a routing key into
  the model-client pool (`"whisper"`, `"ocr"`…). It is deliberately not part of the
  identity segment: endpoints/replicas are operational (L9); the model behind them
  is pinned by server code + manifest identity.
- **`StageOutput{value, bytes}`.** `value` = the JSON slot value (None = stage
  emits no record slot — the clipprep case; the §2 video example shows slots only
  for caption/ocr). `bytes` = the in-run transient payload the executor frees after
  the last consumer (L5's blackboard `{ref, bytes}`).
- **No Settings anywhere in the stage surface.** `StageContext{c1, blob,
  span_seconds, inputs, clients, metrics}` — a stage structurally cannot read a
  knob; L4 by construction, T-1 backstops `os.getenv` cheating.
- **No `order` field.** Execution is readiness-driven, record slots are a map;
  nothing consumes an ordering. `stages_for` sorts by name for determinism only.
- **`registered_modalities()` moves here** — the first half of folding
  `processing/`'s modality routing into the stage registry (completed at WP-C5).

Evidence: `./.venv/bin/python -m pytest tests/test_stage_registry.py -q` →
`59 passed` (was collection-error red against v0 stage.py before the rewrite).
Transitional note: between WP-C1 and WP-C4/C5 the old stage files + executor are
against the dead API and the v0 DP suite does not fully collect — expected
mid-stage state; the suite disposition lands at WP-C6. storage/continuum suites
are untouched by construction.

## WP-C2 — `app/stagegraph/executor.py` rewrite (resolution + readiness executor)

| File | Action | Why |
|---|---|---|
| `app/stagegraph/executor.py` | REWRITTEN (402 → ~330 lines) | KEPT: readiness TaskGroup, commit-on-success, cancel-and-await-siblings, leaf re-raise (ProcessingError preferred), threadpooled `run_sync`. ADDED: sorted `'+'`-join version composition pre-run (L4); one-producer-per-slot + required-never-downstream-of-optional resolve checks; single-`GraphResult` assembly with executor-stamped slot `version`; byte-budget enforcement at slot emission (`SlotEmitError`, never truncation); blackboard `{value, bytes}` with bytes freed after the last consumer. DELETED: kinds/mutate chains/SlotView plumbing/discriminator guard/`assemble` fan-out |
| `app/stagegraph/__init__.py` | edited | executor exports restored (`resolve`, `run_graph`, `GraphResult`, `GraphResolutionError`, `SlotEmitError`) |
| `tests/test_executor.py` | created (TDD) | 27 checks: composition sortedness + `.exp-` riding; every resolve rejection; concurrency timing; leaf re-raise + ProcessingError preference; cancel-and-await; L7 hole/cancelled-cone statuses; budget exact-boundary (fit passes, +1 fails); binary/non-dict/forged-version/wrong-return emission failures; bytes delivered to consumers and freed (incl. under a cancelled cone) |

In-session decisions:

- **Budget enforced at slot emission (stage completion), not final assembly.** L5
  says "exceeding it at assembly is a stage failure"; enforcing when the stage's
  value becomes record bytes is the only point where L7's "downstream cone
  cancelled" stays coherent (a breach discovered after consumers ran could cancel
  nothing). The measure is `len(utf8(json(emitted slot)))` under the canonical
  serialization (compact separators, `ensure_ascii=False`), INCLUDING the stamped
  `version` key — i.e. exactly the bytes the record will carry for that slot.
- **The executor stamps `version`; a stage supplying its own is a loud failure.**
  The contract's deliberate slot-version redundancy can never drift from the
  dialect segment because there is exactly one writer for both (T-3/T-4 assert it
  end-to-end at WP-C6).
- **Slot values must be JSON objects.** Every v1 contract slot is an object; a
  bare string/list would silently break the `{"version": …, **value}` merge.
- **`value=None` = no record slot, status still `ok`** — the clipprep shape (§2
  names video slots as caption/ocr only). L11 note: a transient-output stage's
  name in the dialect with no slot reads as a hole to a consumer that doesn't
  know the stage; acceptable because nothing consumes such a slot — recorded so
  T-6 documents the distinction via slot-producing stages only.
- **Statuses vocabulary `ok|failed|cancelled`** — exactly L8's done-row set, so
  Stage D can persist `GraphResult.statuses` verbatim into the extended row.
- **Bytes freeing**: refcount = declared dependents; released when each consumer
  finishes (ran, failed, or cancelled), plus an unconditional end-of-run sweep in
  a `finally` (transient payloads never outlive the run on any path).
- **Required-failure leaves no `GraphResult`** — the leaf exception propagates;
  there is structurally no partial record to emit (L2/L6).

Evidence: `pytest tests/test_stage_registry.py tests/test_executor.py -q` →
`86 passed`.

## WP-C3 — `pipeline.py` · `schemas.py`/`models.py` mirror · `config.py` shrink · the seam cut

| File | Action | Why |
|---|---|---|
| `app/pipeline.py` | REWRITTEN | `compute_record_id(chunk_id, pipeline_version)` — exactly two NUL-joined components (L3, discriminator gone); `build_c2(c1, slots, pv)` — the v1 assembler: slots map, root modality, source verbatim minus modality + transport fields, D17 trio incl. `device_clock` + `device_location`, t_start/t_end VERBATIM C1 strings (D-05), no `processed_at` |
| `app/schemas.py` | edited | `C2_ID` → `c2_processed_record.v1.json` (the v0 file stays in contracts/ — it is the running wire until Stage F) |
| `app/models.py` | REWRITTEN (C2 side) | the pydantic v1 mirror: `C2RecordV1` + typed slot models for the six contract slots, `extra="forbid"` everywhere, segment-grammar patterns; C1 side untouched. The mirror moves as ONE change with the schema pointer and the tests (the four-parts rule; storage's mirror moves at Stage E, its own stage) |
| `app/config.py` | SHRUNK (122 → ~90 lines) | output-affecting knobs dead (disposition table below); operational knobs survive verbatim |
| `app/ingest_core.py` | REWRITTEN small | the L6 one-POST emit: graph → `build_c2` → schema gate + mirror → ONE atomic POST → journal receipt (v0-shaped, `[record_id]`) → dedup. Pulled forward from WP-C5's "minimal adaptation" so C4∥C5 build against a settled seam (packaging decision, scope unchanged). Adds a pv-drift assertion (accept-time vs graph-time resolution must agree — both are code) |
| `app/stagegraph/processor.py` | REWRITTEN | `GraphProcessor` (public seam kept): `pipeline_version()` resolved from code alone, `process_async(...) -> GraphResult`; `graph_processor(modality)` takes over `processing/`'s modality routing (KeyError → 501) |
| `app/main.py` | edited | processors-registry + `DP_DIALECT_FREEZE` + isolation wiring out; stage-registry routing in; `/health` reports per-modality `pipeline_versions` + supervisor flag (liveness probe, not a frozen contract); lifespan owns the L9 fleet: `DP_SUPERVISOR=1` starts `Supervisor` (operational opt-in — tests never spawn GPUs), model clients built from the manifest with `client_timeout_s` WIRED (Stage B carry-over closed); `dp_partial_write_total` deleted (one atomic POST makes partial writes structurally impossible); metric seeding now per registered slot |
| `app/model_client.py` | edited | Stage B carry-over closed: a transient replica failure clears `verified`, so a respawned replica re-verifies its `/health` identity before serving again |
| `app/processing/` (5 files) | **DELETED** | folded: `ProcessedUnit` fan-out dead (L2), modality routing now in the stage registry |
| `app/isolation.py` | **DELETED** (was WP-C5's line) | models left the DP process (L9) — the subprocess shield has nothing to contain; its config knobs died in the same change, so keeping the file one WP longer only preserved a broken import |
| `app/stages/audio/*.py`, `app/stages/video/*.py` (10 stage adapters) | **DELETED** | written against the dead API; WP-C4/C5 write the new thin clients fresh (v0 logic lives on in `app/asr`, `app/audio`, `app/vision`; the old adapters are in git history at `21fc411`) |
| `tests/test_emission_law.py`, `test_discriminator.py`, `test_legacy_dialect.py`, `test_isolation.py` | **DELETED** | §6's deleted-suites list, verbatim |
| `tests/test_stagegraph.py` | **DELETED** (superseded) | its subject was rewritten; the behaviors worth keeping live on in `test_stage_registry.py` + `test_executor.py` (concurrency, leaf re-raise, cancel-and-await, commit-on-success) |
| `tests/test_pipeline_v1.py` | created (TDD) | 27 checks: two-component id (signature admits no third arg), v1 shape, verbatim spans, D17 passthrough, transport exclusion, empty-slots legality, contract + mirror validation, v0-shape rejection, unknown-slot fail-closed, required-nullable transcript speaker, ran-and-empty ocr, config-shrink assertions |
| `tests/test_seam_v1.py` | created | the seam smoke: mock-dialect set through the real HTTP surface → exactly one v1 POST; dedup redelivery without a second POST; optional failure ships the record with a hole |

**Killed-knob dispositions (`app/config.py`)** — every output-affecting knob dies
by L4; each is either baked into a backend version or killed outright:

| Knob (env) | Disposition |
|---|---|
| `ASR_BACKEND` | killed — backend selection is code (stage files pin `Backend`); mock is a code-constructed stage set named in the dialect |
| `ASR_MODEL`, `ASR_DEVICE`, `ASR_COMPUTE_TYPE` | baked into the whisper server (large-v3 @ pinned revision, cuda, fp16 — server code + manifest identity); the asr stage's vB carries the deliberate dialect change from v0's base/cpu/int8 (Stage B carry-over) |
| `ASR_BEAM_SIZE` | baked into the asr stage's pinned params (beam 1 — the Stage B golden's params); changing it is a vB bump |
| `ASR_LANGUAGE` | baked into the asr stage's pinned params (language 'en' — the beta ruling of 2026-07-18 carried into code); vB bump to change |
| `ASR_VAD` | baked into the asr stage's pinned params (VAD on — the honest-empty-transcript gate); vB bump to change |
| `INGEST_ISOLATION`, `INGEST_SUBPROC_START` | killed with `isolation.py` (D26) |
| `DP_DIALECT_FREEZE` | killed (D26) — dialects live in code; a deploy IS the flip; L8's version-compare (Stage D) owns redelivery semantics |
| survivors | `STORAGE_URL`, `DP_HTTP_TIMEOUT`, `VERIFY_BLOB_SHA256` (integrity guard — cannot alter a valid record's bytes; T-1 proves), `INGEST_ASYNC/WORKERS/QUEUE_MAX/MAX_RETRIES/RETRY_BACKOFF/DRAIN_TIMEOUT`, `DP_VAR_DIR`, `DP_REDRIVE_MAX_ATTEMPTS`, `INGEST_MODALITY_LIMITS`, `METRICS_ENABLED` — all operational-only |
| new operational | `DP_MANIFEST` (fleet manifest path), `DP_SUPERVISOR` (own-the-fleet opt-in; default off so pytest never spawns model servers) |

(`audio/config.py`, `vision/config.py`, `vision/ocr/config.py`,
`INJECT_CAPTION_*`, `DP_OFFLINE_EVAL` knob dispositions land with WP-C4/C5,
where their subject files die.)

In-session decisions:

- **`record_ids` stays a list on the D16 wire** (`{"ok": true, "record_ids": [rid]}`)
  — D24 restates D16 as "exactly one derivable id per chunk"; the wire SHAPE is
  recording's contract and does not change, the cardinality does.
- **Metric `kind` label now means slot name** (the per-kind record model died);
  `dp_vad_empty_total` keys off an empty-claim `asr` slot; `dp_partial_write_total`
  deleted rather than left as a lying zero.
- **`/health` body re-cut** (per-modality `pipeline_versions` map, `supervisor`
  flag; `asr_backend`/`dialect_frozen`/`video_pipeline_version` gone). The probe's
  own docstring says it is not a frozen contract; the platform README's health
  line updates at Stage F with the deploy story.
- **Skeleton boots between WPs**: with the stage adapters deleted the registry is
  empty — `/health` 200 with `pipeline_versions: {}`, `/ingest` → clean 501
  (verified live). C4/C5 fill the registry.

Evidence: `pytest tests/test_seam_v1.py tests/test_pipeline_v1.py
tests/test_stage_registry.py tests/test_executor.py -q` → `116 passed`;
storage suite re-run → `310 passed` (untouched).

## WP-C4 / WP-C5 — parallel subagents (audio / video stages)

Dispatched as parallel subagents after C1–C3 per the plan's ∥ marking, each
confined to disjoint paths (audio: `app/stages/audio` + retiring `app/asr`,
`app/audio`; video: `app/stages/video` + `app/vision`), orchestrator commits.
Both briefs carried the settled registered-set rulings (recorded here BEFORE the
agents ran):

- **Registered audio set: asr (required) + diarize + acoustic + speaker_align
  (optional).** `translate` and `injected_caption` are ported and tested as
  classes but NOT registered: the ratified §2 example dialect excludes them, the
  running v0 beta fleet has both off (`TRANSLATE_BACKEND`/`INJECT_CAPTION_*`
  unset), and the C2 v1 contract carries no `translation`/`injected_caption`
  slot — the contract's own rule adds a sub-schema "when its first producer
  ships", which these have not. Registering either later = code change + the
  additive contract edit, in one commit.
- **Optionality follows the Heard-lines ruling**: the founder-ruled C10 v2
  `asr` fallback exists precisely because `transcript` can be a hole, which
  entails diarize/speaker_align being optional; an acoustic-tagger outage
  likewise must not dead-letter audio (L8 heals instead).
- **Registered video set: clipprep (required, no record slot — frames are
  re-derivable transient bytes) + screentext (optional, slot `ocr`) + clipcap
  (optional, slot `caption`, needs clipprep AND screentext).** The hard
  clipcap→screentext need is D8's surviving one-liner (specialist OCR feeds the
  caption, D-09); its cost is honest: an OCR failure holes BOTH `ocr` and
  `caption` (cancelled cone) until heal — L7 exactly.
- **clipcap's real backend is the v0 `vlm` client** (one multi-image call
  against an OpenAI-compatible endpoint) — already thin (L9's "call
  server/cloud"); `vertex` remains the documented raising stub.

### WP-C4 — audio stages as thin clients + `speaker_align` (subagent; verified + committed by the orchestrator)

| File | Action | Why |
|---|---|---|
| `app/stages/audio/asr.py` | NEW, registered | `asr.v1-fw.v1` required primary over `servers/whisper`; params pinned to the Stage B golden (task=transcribe, beam 1, language en, vad on); owns the shared `absolute_splits` (exact v0 `_absolute_segments` clamp+abs mapping, minus speaker) + `require_client` helpers |
| `app/stages/audio/diarize.py` | NEW, registered | `diarize.v1-pyannote.v1` → slot `diarization`, optional; raw turns only, NO mutation of anything; labels re-derived first-onset as `speaker-N` |
| `app/stages/audio/acoustic.py` | NEW, registered | `acoustic.v1-ast.v1`, optional; v0 caption-folding SELECTION ported client-side (speech-family drop list verbatim, `THRESHOLD = 0.1`, `TOP_K = 3`, `SERVER_TOP_K = 20` — the old `ACOUSTIC_*` env knobs as code pins); `values: []` IS the empty claim (v0's "Ambient background noise." fallback dropped — a fabricated string is a false positive); `confidence` = top selected score, omitted over empty values |
| `app/stages/audio/speaker_align.py` | NEW, registered | `speaker_align.v1-builtin.v1` → slot `transcript`, optional, needs (asr, diarize); pure-CPU join porting v0 `assign.py` semantics (max-overlap, strictly-positive, lexicographic tie-break, `None` on no overlap, one split per asr split in order) |
| `app/stages/audio/translate.py` | NEW, **not registered** | `translate.v1-fw.v1` → slot `translation`; no-call gates (empty text / detected==en) proven by a raises-if-called fake; result language hardcoded en (whisper task=translate is X→English only — the v0 `TRANSLATE_TARGET` pin); emits honest empty claims where v0 emitted no record |
| `app/stages/audio/injected_caption.py` | NEW, **not registered** | `injected_caption.v1-index.v1`; index path is a constructor argument (fail-fast); verbatim index time strings, half-open bisect join, mtime/size parse cache — all v0 semantics |
| `app/asr/` (4 files) · `app/audio/` (16 files) | **DELETED** | model code went server-side at Stage B; mapping/selection/join logic ported into the stage files above; every env knob dispositioned (table below) |
| `tests/test_audio_stages.py`, `test_audio_translate.py`, `test_audio_injected_caption.py` | NEW (TDD, spec-first pins) | 52 tests: golden-fed fake clients pin the exact slot dicts (the WP-C6 real-fleet reference), envelopes, error paths (required asr re-raises `ModelCallError`; optional holes + cancelled cone), contract validation per stage, ≥4× budget-margin pin |
| `tests/test_audio_acoustic.py`, `test_audio_diarization.py`, `test_audio_translation.py`, `test_processor_seam.py` | **DELETED** | v0 env-knob suites + the v0 fan-out seam suite (its subject died with `processing/`; seam coverage lives in `test_seam_v1.py`) |

Knob dispositions (`app/audio/config.py` + v0 injected_caption): `DIARIZE_BACKEND`,
`DIARIZE_SPEAKERS`, `TRANSLATE_BACKEND`, `ACOUSTIC_BACKEND`,
`INJECT_CAPTION_BACKEND` — killed (backend selection is code; registration is the
switch). `DIARIZE_MIN/MAX_SPEAKERS` — baked as "no clustering hints" (the fleet
default; adding one is a vB bump). `TRANSLATE_TARGET` — baked as
`TARGET_LANGUAGE = "en"`. `ACOUSTIC_TOP_K/THRESHOLD` — baked (3 / 0.1).
`INJECT_CAPTION_INDEX` — constructor argument. `HF_TOKEN` — server-side
operational auth since Stage B; the DP process no longer reads it.

Agent decisions endorsed: speaker normalization re-derived client-side
(first-onset over server turns — identical today, immune to server relabeling);
both AST goldens honestly pin `values: []` (speech-only fixtures — the
non-empty selection path is pinned with synthetic payloads);
`enrichments.speakers` aggregation not ported (no C2 v1 home). Registered audio
dialect pinned by test:
`acoustic.v1-ast.v1+asr.v1-fw.v1+diarize.v1-pyannote.v1+speaker_align.v1-builtin.v1`.
Noticed: split timestamps render in `abs_time`'s `+00:00` microsecond spelling
(v0 behavior) while root spans stay verbatim — C10 v2 bucketing must accept both
(Stage E); `scripts/smoke_audio_backends.py` imports the deleted packages —
Stage G demolition item.

Evidence (agent run, then re-run independently by the orchestrator):
`pytest tests/test_audio_* tests/test_stage_registry.py tests/test_executor.py
tests/test_seam_v1.py tests/test_pipeline_v1.py -q` → `168 passed`.

### WP-C5 — video stages (subagent; verified + committed by the orchestrator)

Registered video dialect (resolved from code, pinned by test):
`clipcap.v1-vlm.v1+clipprep.v1-ffmpeg.v1+screentext.v1-ppocr.v1`.

| File | Action | Why |
|---|---|---|
| `app/stages/video/clipprep.py` | NEW, registered | `clipprep.v1-ffmpeg.v1`, required, `run_sync` (ffmpeg subprocess is self-isolating); `StageOutput(value=None, bytes=ClipFrames)` — no record slot (frames are re-derivable transient data); all 10 v0 frame/delta knobs pinned as `CLIP_SETTINGS` verbatim; undecodable blob raises (the mock synthetic-frames fallback is dead); `byte_budget=1` is the structurally-inert registry minimum |
| `app/stages/video/screentext.py` | NEW, registered | `screentext.v1-ppocr.v1` → slot `ocr`, optional, thin client on the `/infer` envelope; the Stage B carry-over rewire done (old `/ocr` wire + `model_sha_det/rec` pins → manifest `expected_identity.weights.det/rec_sha256` + `/infer`); `_match_frame`/`_jpeg_dims`/`_normalize_bbox` ported; v0 A-10 posture (absorb minority frame errors, raise >50%); `value: ""` = ran-and-empty, always emitted on success |
| `app/stages/video/clipcap.py` | NEW, registered | `clipcap.v1-vlm.v1` → slot `caption`, optional, needs (clipprep, screentext); absorbs `render_caption` + the D-11/D-12 caps; **`PACK_DIGEST_PIN` raises `StageRegistrationError` at import if the on-disk pack digest moved** — a `.prompt.md` edit without a vB bump cannot ship; model/scenario/caption-rate all code pins; `VLM_URL`/`VLM_API_KEY`/`VLM_TIMEOUT_S` are the only env in the video path (operational, test-proven output-inert) |
| `app/vision/clip.py`, `budget.py`, `ocr/assemble.py`, `ocr/redact.py`, `delta.py`, `parse.py`, `clip_types.py`, `prompts/*` (+LOCK), `circuit.py` | KEEP (edited to explicit-args, env shims dead) | the §9 keep list; prompts' `select` takes a scenario string; LOCK/digest/relock intact |
| `app/vision/clipcap/vlm.py` | REWRITE | explicit-args `describe` (D-02 payload + D-09 OCR injection byte-for-byte); guided decoding pinned OFF (v0 default; probe never wired) |
| `app/vision/clipcap/vertex.py` | KEEP (edited) | the documented stub, unregistered, still raising |
| `app/vision/emit.py`, `config.py`, `version.py`, `mode.py`, `frames.py`, `result.py`, `vlm.py`, `mock.py`, `ocr/{ppocr,vlm,mock,config}.py`, `clipcap/mock.py` | **DELETED** | dispositions below; three are §9-verdict confirmations (§9's own header licenses line-by-line confirmation at execution): `frames.py` is the LEGACY keyframe decoder clip.py replaced (D-04; sole importers were the deleted adapters), `mode.py`'s resolver had no live importer, `version.py`'s "REWRITE → version-composition module" already shipped as `stagegraph.stage.segment` + `executor.resolve` at WP-C1/C2 |
| `scripts/prompt_ab.py` | PARKED | the env-unlocked pack-override machinery is dead (L4); adaptation is a rebuild, not an edit — loud docstring + `main()` exits 3 with a 4-point rebuild checklist (in-code `.exp-` arm stages, GraphResult scoring, client-fake OCR arms, lifted scorers) |
| video test files | per-file dispositions in the C5 report, §6 applied | deleted: `test_video_pipeline`, `test_clip_pipeline_e2e`, `test_screentext_integration`, `test_clip_consolidation`, `test_metrics_video` (breaker state-machine tests extracted verbatim → NEW `test_circuit.py`), `test_eval_scorers`; rewritten: `test_clipprep` (real ffmpeg over lavfi fixtures), `test_screentext` (fake client replays the REAL `golden_regions.json` verbatim → the pinned `ocr` slot value, C6's real-fleet reference), `test_clipcap` (MockTransport OpenAI fake → pinned caption slot; digest-gate test), `test_prompt_pack`; adapted: `test_ocr_assemble`, `test_budget`; kept: `test_delta`, `test_parse`, `conftest_video` |

Knob dispositions (vision/config.py 31 fields + clip shim + ocr/config.py — the
complete table is in the C5 agent report, reproduced here in summary): frame/
delta/OCR-selection/caption-rate knobs → `CLIP_SETTINGS` and stage pins at v0
default values; `VIDEO_VLM_MODEL` → `MODEL="Qwen/Qwen3-VL-32B-Instruct"` pin;
`VIDEO_SCENARIO` → `"screen-mac"` pin; prompt-dir/pack-override knobs dead
(identity = `PACK_DIGEST_PIN` + vB); `VIDEO_OCR_MODEL_SHA_*` → manifest
`expected_identity` verified by ModelClient; backend selectors dead (code);
three knobs found INERT in v0 and killed as orphans (`VIDEO_CLIP_MAX_TOKENS`,
`VIDEO_OCR_LAYOUT_SPREAD`, `VIDEO_PRIVACY_FILTER` — redaction pinned always-on,
its v0 reality); `VIDEO_VLM_URL/_API_KEY/_TIMEOUT` → operational `VLM_URL`/
`VLM_API_KEY`/`VLM_TIMEOUT_S`. Zero `VIDEO_*` env reads remain in `app/`.

Agent decisions endorsed: the cone coupling proven by test (OCR failure = `ocr`
hole AND cancelled caption — v0's "caption never ships on silently-missing OCR"
guarantee, now structural, healing instead of dead-lettering); the 48 MB body
cap not carried (frame width 1728 bounds a JPEG well under 2 MB by
construction); the `delta` observability trace computed-and-discarded (no
consumer, L10); circuit.py kept+tested but wired nowhere (same as v0) with the
one honest future use flagged for Stage D/F; `_assert_not_offline_eval` KEPT in
main.py (operational-only latch any prompt_ab successor wants).

Evidence (agent run, then re-run independently by the orchestrator):
`pytest -q` over the ENTIRE service suite → `493 passed, 2 skipped` (the skips
are the DP_E2E-gated real-fleet drills).

## WP-C6 — groundwork laid while C4/C5 ran (orchestrator, disjoint paths)

- **Kept core suites adapted to v1** (§6 keep list): `conftest.py` now installs a
  mock-dialect audio registry via the overridable `mock_registry` fixture and
  provides `FakeGraphProcessor` (sync-callable-in-threadpool, preserving the v0
  blocking-gate test idioms); `test_journal.py`, `test_async_ingest.py`,
  `test_ingest_fairness.py` fakes rebuilt on it (stub slot names must be real
  contract slots now — the one-POST path schema-validates); `test_ingest_mock.py`
  rewritten to the v1 record shape; `test_civil_time_passthrough.py` extended
  with the v1-closed `device_clock` gap (+ absent-stays-absent);
  `test_blob_integrity.py`/`test_metrics.py`/`test_continuity.py`/
  `test_dedup_claim.py` pass unchanged on the mock registry. 195 tests green
  across the core set.
- **T-spine files landed** (mock-dialect halves): `test_t1_determinism.py` —
  the env matrix includes every KILLED knob (ASR_*, isolation, freeze, VIDEO_*,
  ACOUSTIC_*, TRANSLATE/INJECT) plus the surviving operational knobs, asserting
  BYTE-identical POSTed record bytes (FakeStorage now captures the raw wire
  bytes), plus the §4 reprocess-byte-identical claim; `test_t2_one_record.py`;
  `test_t3_version_composition.py` — sortedness owned here, `.exp-` riding,
  fullmatch grammar + trailing-newline trap, slot.version ≡ stage segment
  end-to-end, and the real-registry contract-surface snapshot (fills in when
  C4/C5 register); `test_t4_slot_law.py` — record-level slot law + the L12
  statement held executable; `test_t5_ledger_flows.py` — the Stage C subset
  (skip / version-forward-beside / required-no-record / optional-hole), full
  heal flows explicitly deferred to Stage D; `test_t6_honesty.py` — the L11
  read implemented as a consumer would (record + dialect alone), incl. the
  cancelled-cone-reads-as-hole nuance. 40 passed + 1 pending-snapshot skip.
- **Carried hardening: servers/common `/health` is now `async def`** (a sync
  handler shared the threadpool with queued sync `/infer` calls — a busy
  replica's backlog could starve its own liveness probe into a supervisor
  kill); regression test added asserting the handler is loop-native; framework
  suite 27 passed; `/infer` bytes untouched (goldens unaffected).
- **e2e reconnaissance**: GPU 7 hosted a foreign continuum eval job for part of
  the stage (pid 3847430, `holdout_text_control.py`, ~9.5 GB) — not this repo's
  process (it finished before the drill ran; all GPUs read 0 MiB at drill
  start). Qwen3-VL-32B is not in the HF cache and the serve loop (vLLM :8000)
  cannot co-run with the learn loop (shared storage :8083), so the video e2e's
  real backends are clipprep(ffmpeg) + screentext(ocr server); clipcap's
  endpoint is absent on this node — the caption holes honestly (v0's live video
  dialect was mock; the real VLM endpoint is the serve-loop's, a Stage F deploy
  concern).

## WP-C6 — completion (T-spine finished, real-backend e2e, boot check)

- **T-1 finished**: a second matrix runs the same 15 env cells over the REAL
  audio stage classes (clean registry, golden-fed fake clients injected in place
  of `app.state.model_clients`) — the stage code itself, the only code that
  could cheat with `os.getenv`, is proven output-inert; wire bytes identical in
  all 29 cells + both reprocess checks.
- **T-3 finished**: the registration snapshot pins both modalities' full
  contract surface (segment/slot/needs/required/byte_budget per stage); any
  drift without a version bump is red.
- **Real-backend e2e** (`tests/test_e2e_real.py`, gated `DP_E2E=1`): the fleet
  comes up via `python -m app.supervisor --manifest servers/manifest.json`
  (subprocess, own session), all 8 replicas health-gated; the DP app runs the
  REAL registry with storage bound to the LOCAL STUB sink (MockTransport — no
  socket, structurally incapable of reaching :8083). Run 2026-08-06:

  ```
  $ DP_E2E=1 ./.venv/bin/python -m pytest tests/test_e2e_real.py -v
  test_audio_chunk_end_to_end PASSED
  test_video_chunk_end_to_end PASSED
  test_audio_reprocess_is_byte_identical_through_the_real_fleet PASSED
  3 passed in 35.14s
  ```

  Audio: the Stage B real-speech fixture through the live whisper/pyannote/ast
  replicas — the emitted record is schema+mirror valid and its `asr`,
  `diarization`, `acoustic` slots equal BYTE-EXACTLY the golden-fed pins in
  `test_audio_stages.py` (the client path reproduces the server goldens);
  `transcript` present and aligned. Video: real ffmpeg + real ocr server over
  the committed fixture — valid record, `ocr` slot present, `clipprep` slotless
  by design, `caption` an honest hole (no VLM endpoint on this node, reasoning
  above), and a second identical-bytes chunk produced an identical slots map.
  Reprocess through the real fleet: byte-identical wire bytes. Drill
  discipline held: v0 `:8085` 200 before and after (asserted in the fixture and
  re-verified), zero fleet ports listening after teardown, all 8 GPUs back to
  0 MiB.
- **run.sh boots the rebuilt service**: fresh boot on a scratch port → `/health`
  200 with both dialects resolved from code
  (`{"audio": "acoustic.v1-ast.v1+asr.v1-fw.v1+diarize.v1-pyannote.v1+speaker_align.v1-builtin.v1",
  "video": "clipcap.v1-vlm.v1+clipprep.v1-ffmpeg.v1+screentext.v1-ppocr.v1"}`);
  run.sh itself rewritten (ASR_BACKEND gone; `DP_SUPERVISOR` passthrough
  documented).

## Exit criteria (§8 Stage C + the session brief)

| Criterion | Status | Evidence |
|---|---|---|
| Full v1 suite green with mock backends | done | `pytest -q` → **494 passed, 3 skipped** (the skips are the DP_E2E gate itself) — T-1…T-6, kept suites, stage suites, seam |
| Full v1 suite green with real backends on this node | done | `DP_E2E=1` run above: 3/3 passed under the live fleet (manifest ports 8121–8152, GPUs 2–7) |
| Audio + video e2e emit schema-valid C2 v1 records to the stub sink | done | both records validated against `contracts/c2_processed_record.v1.json` + the pydantic mirror; POSTed to the MockTransport stub only — live storage `:8083` untouched by construction |
| Client path reproduces Stage B goldens | done | audio slots byte-equal the golden-fed pins; the OCR chain closes at the layers below (server-vs-golden in `servers/ocr/tests`, client-mapping-vs-golden in `test_screentext.py`) |
| storage/continuum suites untouched-green | done | storage → `310 passed`; continuum → `262 passed, 7 skipped` — identical to the Stage A/B baselines; no file of either service touched |
| v0 keeps running from its worktree all stage long | done | `:8085` → 200 at WP-C0 (before/after its single authorized restart), before/after the fleet drill, and at exit — same pid 3835816, cwd in `/home/ubuntu/nmn/dp-v0-live` |
| run.sh boots the rebuilt service | done | boot check above |
| One commit per WP, worklog in the same commit | done | `1589c0f` C0 · `a870c5e` C1 · `21fc411` C2 · `c97f26c` C3 · `da60443` C4 · `bef7f07` C5 · final commit (C6 + this closing edit) |
| Stage D not started | honored | ledger semantics everywhere v0-shaped; touchpoints inventoried below |

## Noticed for later stages

- **Stage D — where the single-record emit sits**: `ingest_core.process_chunk`
  is the one seam — graph → `pipeline.build_c2` → schema+mirror gate → ONE
  `storage.post_record` → `journal.mark_processed(c1, [record_id], pv,
  now_iso(), epoch)` → `dedup.put`. The ledger touchpoints to extend:
  `journal.accept/unaccept/mark_processed/mark_dead_letter/processed_record_ids`
  (v0 row shape; the version-compare lives in `processed_record_ids`'s callback)
  and `DedupStore.claim_for_async`'s fresh/done/inflight trichotomy →
  L8's five-way claim tree. `GraphResult.statuses` already speaks L8's
  `ok|failed|cancelled` vocabulary and is currently dropped on the floor at the
  seam — persisting it into the extended done-row is the natural WP-D1 move.
  `test_t5_ledger_flows.py` holds the Stage C subset and names what Stage D owes
  (heal, heal-budget exhaustion, crash-table replay).
- **Stage D/F — circuit.py** is kept+tested but wired nowhere (v0 state); the
  one honest use (skip decode work during a sustained captioner outage) sits
  above the graph — wire or retire there.
- **Stage E — C10 v2 bucketing** must accept two RFC3339 spellings: root spans
  verbatim (usually `Z`) and split times in `abs_time`'s `+00:00` microsecond
  form; ocr slot text uses chunk-relative `+Ns` stamps (pinned `rel`) — absolute
  stamps would be a slot-shape (vS) change.
- **Stage F — deploy**: clipcap needs `VLM_URL` (+`VLM_API_KEY`,
  `VLM_TIMEOUT_S`) pointing at an endpoint actually serving
  `Qwen/Qwen3-VL-32B-Instruct`; it is NOT under the manifest identity scheme —
  the model-name pin is the only client-side check. Consider a startup
  `/v1/models` probe or bringing the VLM under manifest identity at cutover.
  `DP_SUPERVISOR=1` is how a deploy makes the service own the fleet; the deploy
  table + env passthrough remain Stage F work (Stage B's note stands).
- **Stage G — demolition list additions**: `scripts/smoke_audio_backends.py`
  (imports the deleted `app.asr`/`app.audio`), `scripts/capture_chunkset.py` +
  `scripts/oracle_gemini.py` (call the deleted `clip.build_vision_settings`),
  the parked `scripts/prompt_ab.py` (rebuild checklist in its docstring), and
  the stray `:8097` sidecar process (Stage B's note stands).
- **Housekeeping**: `tests/__init__.py` and `tests/fixtures` inherited entries
  (`audio.blob`, `image.*`, `text.*`, `video.c1.json`'s 47-byte stub) are v0
  artifacts some deleted suites used; sweep at Stage G.
