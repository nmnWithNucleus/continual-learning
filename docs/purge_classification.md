# Old-world surface purge — Phase 1: inventory and classification

> **This document is a gate, not an execution.** Nothing in the repository changed in this
> session except this file. Every DELETE / REWRITE / SURGICAL EDIT below is a **proposal**;
> the founder and the verifying co-founder rule on each before Phase 2 touches anything.
>
> **Policy being applied** (founder, 2026-08-07): the working tree is the teaching surface for
> future newcomers; git history is the archive. Every trace of the pre-rebuild world — its
> D-numbers, A/E/OQ numbers, retired laws, worklogs, transition records, condensed histories,
> war-story rationales — leaves the tree. Nothing is lost: the repo's git history and origin
> retain everything. **Never touch `.git`, never rewrite history, never force-push.**
>
> **The world that stays** is the one the DP charter describes: the Slot Law (D23), C2 v1
> (D24), the version law (D25), the machinery/bureaucracy split (D26), the heal ledger +
> `created_at`/`updated_at` (D27), C10 v2 + whole-record retraction (D28) — plus everything
> the other services run today.

**Prepared:** 2026-08-07 · repo at `main` = `cb1fb2e` · census script and raw JSON preserved
in this branch's history (`/tmp/census.py` run against the tree; per-file matrix summarized
in §1). All suites assumed green per `HANDOFF.md` (storage 354 · continuum 264+7sk ·
recording 144 · data-processing 569+4sk); no suite was re-run in this session (read-only).

---

## 0. Two rulings the founders must make before anything executes

These are policy collisions Phase 2 cannot resolve on its own.

### 0.1 The purge policy vs. DECISIONS.md's own register law

`DECISIONS.md` §Stage states, as standing law:

> "A decision is never silently rewritten to say something different. It is superseded by a
> new numbered row, so the record shows what changed our minds. … D17 supersedes its own
> same-day first draft; D19 overturned two clauses of D18. Both are visible above rather
> than edited away."

The purge policy says pre-rebuild D-numbers and transition records leave the tree. These
conflict for any register row proposed for removal or for any card whose text narrates the
transition. **Proposed resolution** (needs explicit ratification, because it amends the
register's own rules): the register keeps its *rows* for every decision that still governs
(most of them — see §3.1); rows that governed only deleted machinery are removed whole,
leaving numbering holes; the "superseded rows stay visible" clause is amended to "…stay
visible *in git history*", and the pointer line (§5) is added to the register header. The
alternative — keep every row forever and only trim cards — is also coherent; it just leaves
more old world in the tree than the stated policy allows.

Same collision, smaller: STYLE.md rule 10 ("Quoted material keeps its original wording. …
These rules govern what we write, not what we preserve") is the rule DP's `worklog.md`
invokes to preserve retired v0 items verbatim. The purge deletes documents rule 10
protects. Proposed resolution: rule 10 keeps governing *how* preserved text is treated
wherever preservation is chosen; the history policy decides *whether* it is preserved in
the tree at all. No STYLE edit needed — but the ruling should be recorded with the gate.

### 0.2 The v0 parity apparatus is alive, not residue

The strongest-looking purge target — storage's v0-parity machinery — is **load-bearing
today**:

- `services/storage/scripts/daylog_parity_diff.py` is storage CHARTER **M9's executable exit
  bar**, re-baselined 2026-08-06 for the v2 world (D28, Stage E WP-E4: the v2 slot-walk over
  hand-built C2 v1 records vs the untouched v0 reference — 31 checks, both origins, tier A
  byte-identical). It is imported **in-process by a green test**
  (`services/storage/tests/test_daylog_parity.py:90`, `importlib.util.spec_from_file_location`),
  so "a future change to EITHER renderer trips this bar in the suite."
- The "untouched v0 reference" it diffs against is continuum's `LocalDayLogClient` +
  `app/daylog.py` renderer, fed by `app/synth.py`'s v0-shaped records
  (`content.kind` / `enrichments` / `processed_at` / discriminator dedup). Those v0 shapes are
  the *fixtures of the proof*, exercised by ~8 green continuum test files
  (`test_clients.py`, `test_cycle_mock.py`, `test_daylog.py`, `test_http_clients.py`, …).

**Consequence:** the old-world *vocabulary* inside this apparatus cannot be grepped away
without deleting the parity proof itself. Ruling needed: (a) keep the apparatus as-is and
exempt it from the token purge (recommended — it is D20/D28's proof, and its old-world
content is quarantined inside one script + one reference client), or (b) retire the
differential proof deliberately (a founders' act against D20, out of purge scope). This
report assumes (a) everywhere below.

Related defect found during inventory (not purge work, recorded so it is not lost):
`services/continuum/scripts/seam_check.py` builds and asserts `version:"0"` C2 records at
three sites (`:461`, `:614`, `:1011`, `:1628`) and `run.sh`'s `--seed-demo` path seeds v0
records via `app/synth.py` — both would fail against the live fleet, which schema-gates C2
**v1** (`storage/app/schemas.py:29`). D20's "golden" definition names `seam_check.py`. These
need a v1 rewrite as ordinary engineering work, whatever the purge decides.

---

## 1. Token census

Method: `rg -c` per token over the whole repo, excluding `.git`, venvs, `__pycache__`,
`node_modules`, caches, and binary media. Raw per-file matrix retained with the census
script. Counts below are files / total hits at `cb1fb2e`.

### 1.1 The token list

| Token (regex family) | Files | Hits | What it was |
|---|---|---|---|
| `discriminator` | 42 | 205 | v0's third `record_id` component; sibling identity under fan-out. Retired by D24. |
| `mutate` / `mutable_slots` / mutation-model | 19 | 88 | v0 in-place mutation (diarize filling another stage's slots). Dead with L5. |
| `sidecar` | 28 | 166 | v0 stage kind (also the old `sidecars/ocr` service dir, deleted Stage G). Note: continuum uses "sidecar" for its DP injected-caption test double — different sense, 5 hits. |
| `best_effort` | 29 | 50 | v0 stage failure policy. Replaced by L7 required/optional. (Recording's `best_effort` hits are a *different, live* upload-queue concept — see §1.3.) |
| `SlotView` | 12 | 26 | v0 capability proxy for mutation-by-reference. Dead with L9. |
| "emission law" / record-emission-law | 12 | 49 | the retired five-tests/five-riders governance. `docs/record-emission-law.md` itself already deleted at Stage G; 7 files still cite the filename. |
| riders R1–R5, `R1_EXEMPT_SIDECARS`, "fork rider" | 12 | 25 | the emission law's riders. *(Census regex corrected after first run; this row re-counted directly.)* |
| old tests T1–T5 (unhyphenated) | 8 | 49 | v0 emission-law test names. **Heavy false positives** — see §1.3. The new spine is T-1…T-6 (hyphenated), current-world. |
| `DIALECT_FREEZE` | 10 | 28 | v0 env freeze flag. Retired by D26/L4. |
| `INGEST_ISOLATION` / `isolation.py` | 17 | 43 | v0 per-chunk subprocess isolation. Deleted (D26). |
| `ProcessedUnit` | 8 | 15 | v0 fan-out unit type. Dead with L2. |
| keyframe | 45 | 231 | the v0 video pipeline (per-keyframe records). Replaced by the clip path; L12 still says "keyframe-like structures" (current usage, 1 hit). |
| VidProc / vidproc | 8 | 33 | v0 video pipeline name + `vidproc-vlm-v0` dialect. |
| old dialect strings (`asr-fw-v0/v1`, `diar-pyannote`, `+ocr-ppv4-cpu-v1`, `cfg_tag`, `prompt_dir_fingerprint`) | 17 | 57 | v0 composed-dialect fragments. v1 dialects are `<stage>.v<S>-<backend>.v<B>`. |
| WS-\* workstream names (WS-VC, WS-A/B/C/D/E/F/G/H, WS-SG, WS-AO, WS-V…) | 66 | 317 | pre-rebuild workstream ids, in docs and code comments. |
| `refactor_stage_[A-G]` | 15 | 27 | the rebuild's per-stage worklog filenames. |
| `refactor_dp_service` | 17 | 29 | the rebuild plan filename. |
| `D-R1`…`D-R6` | 2 | 35 | the rebuild rows' draft ids before ratification (map to D23–D28; `refactor_dp_service.md` §7). |
| `enrichments` | 35 | 78 | v0's present-but-empty block. Gone in v1 (the E-5 additive is parked, deliberately untaken). |
| `content.kind` / per-kind | 36 | 98 | v0 record labeling. Dead with the per-kind record model. |
| `processed_at` | 25 | 40 | v0 wall-clock field. Dropped (ruled 2026-08-06; breaks byte-compare + T-1). |
| `dp-rebuild-v1` (branch name) | 14 | 27 | the beside-build branch. |
| Stage A–G rebuild refs (`Stage [A-G]`, `WP-[A-G]n`) | 107 | 695 | rebuild stage/work-package references. **Broadest, noisiest token** — see §1.3. |
| OD-1/2/3 | 26 | 46 | the migration rulings (beside-build / fresh-forward / all-four-servers). OD-2's *backfill tool* is still owed (HANDOFF Next 2) — that forward obligation must survive any strip. |
| `w-day5` (old window ids) | 7 | 17 | pre-D18 literal window ids from a retired smoke script; two live C5 entries on disk still carry them; validator rejects the shape. |
| `window_for` / `closed_window_before` / `Window.local_date` / `local_window_date` | 26 | 69 | the deleted local-date window arithmetic. All hits are tombstones ("X is deleted") or the deletion-recording comments in `window.py`. |
| `LocalDayLogClient` | 15 | 42 | the v0 reference renderer client — **alive as the parity reference** (§0.2). |
| `per-frame-v0` (old prompt pack) | 9 | 19 | removed at Stage G (clipcap `vlm.v1→v2`); fleet deploy of the bump still pending (HANDOFF Next 4). |
| `VIDEO_*` dead env knobs (`VIDEO_PIPELINE`, `VIDEO_OCR_*`, `VIDEO_CLIP_*`, `VIDEO_VLM_*`, `VIDEO_KEYFRAME_*`, …) | 31 | 238 | v0 output-affecting knobs, forbidden under L4. DP `app/config.py` has **zero** of them; all hits are docs, comments, and two stranded scripts. |
| `D-M1-*` / `D-E*` (pre-register recording rows) | 27 | 108 | recording's local decision ids from before the founders' register existed (now R-1/R-2 + code comments + client-JS comments). |
| WS-VC internal `D-0x`/`D-1x`/`A-1x`/`O-x` ids | 49 | 382 | the screen-video workstream's internal register, cited from live code docstrings and tests. |
| pre-rebuild / pre-cutover / "the v0 world" markers | 15 | 31 | explicit era markers, mostly in LEARN_LOOP and the stage docs. |

### 1.2 Top files by total hits

```
393  services/data-processing/handoff/ws-video-clip-buildlog.md
369  services/data-processing/handoff/ws-video-clip.md
155  services/data-processing/docs/refactor_stage_C.md
130  product/onboarding/LEARN_LOOP.md
119  services/data-processing/docs/refactor_stage_G.md
118  product/handoff/engineering.md
109  services/data-processing/docs/refactor_stage_E.md
106  services/data-processing/docs/refactor_stage_A.md
103  services/data-processing/docs/refactor_dp_service.md
 82  services/data-processing/CHARTER.md
 75  services/data-processing/docs/refactor_stage_D.md
 72  services/data-processing/docs/refactor_stage_F.md
 66  services/data-processing/handoff/ws-video-clip-eval.md
 61  services/data-processing/handoff/ws-video-pipeline.md
 57  product/DECISIONS.md
 55  product/ARCHITECTURE.md
 49  services/data-processing/docs/refactor_stage_B.md
 43  services/data-processing/handoff/ws-dp-stage-graph.md
 43  services/data-processing/handoff/ws-video-clip-probe.md
 32  services/data-processing/handoff/ws-dp-hardening.md
 30  services/data-processing/handoff/worklog.md
 29  services/storage/scripts/daylog_parity_diff.py
 27  product/HANDOFF.md
 25  services/storage/CHARTER.md
 25  services/recording/handoff/ws-e-extension.md
```

The shape is what the policy predicts: ~85 % of all hits sit in nine deletable historical
documents (the rebuild plan + stage worklogs + the WS-VC family + `engineering.md` +
`LEARN_LOOP.md`); the remainder is card-level residue in the registers and comment-level
residue in live code.

### 1.3 Census caveats — do not grep-and-destroy these

- **T1–T5**: `recording/tests/test_mac_client.py` (10 hits) uses `T1 = "2026-07-18T12:00:10.000Z"`
  — timestamps, not test names. The *current* spine T-1…T-6 is hyphenated and stays.
- **`best_effort` in recording** (`capture_web.py`, extension/web clients): a live
  upload-queue concept, unrelated to DP's retired stage policy. Stays.
- **"Stage A–G" regex** also matches current, legitimate text (e.g. `servers/*` PROVENANCE
  notes, `run.sh`, `STACK.md`'s ":8161 … Stage F" one-liner). Each hit needs eyes, not sed.
- **`keyframe`** survives legitimately in Slot Law L12 ("keyframe-like structures"), in
  `readings/` as generic video vocabulary, and in recording's generic ffmpeg context.
- **"sidecar" in continuum** (`phase-3*` reports, `morpheus/probes.py`, slurm scripts):
  names the Phase-3 dogfood's injected-caption test double, not DP's stage kind.
- **Negative assertions**: several green tests *assert the old world stays dead*
  (`test_t2_one_record.py` — no discriminator; `test_stage_registry.py` — no
  kind/SlotView/best_effort registration surface; `test_t1_determinism.py` — the
  `DIALECT_FREEZE`/`INGEST_ISOLATION` knobs are inert). The mentions are the tests' job.
  Recommended treatment: keep the tests, make their docstrings self-contained (§2.8).

---

## 2. File dispositions

Legend — **DELETE** (git keeps it) · **REWRITE** (new-world only) · **SURGICAL EDIT**
(strip old-world passages, keep the document) · **KEEP** (as-is). Every DELETE carries its
inertness evidence. "Consequential edits" are the places that cite the file and must be
made self-contained in the same commit.

### 2.1 `product/` top level

| File | Disposition | Why / what changes |
|---|---|---|
| `README.md` | **SURGICAL EDIT** | Census-clean. One addition: the §5 pointer line, plus (if ratified) a one-line note that `onboarding/` is being rebuilt. |
| `VISION.md` | **KEEP** | Census-clean, stable "why". |
| `ORG.md` | **KEEP** | Census-clean. Its §Keeping-documents-true rules are the *home* for why-rescue #10. |
| `STYLE.md` | **KEEP** | Census-clean for the token list. One incidental: §The card template's worked example links `c10_daylog.v1.json` (`STYLE.md:72`); if that schema file is deleted, the example's link becomes `c10_daylog.v2.json` — a one-word edit inside an illustration. |
| `PROMPTS.md` | **KEEP** | Census-clean. |
| `STACK.md` | **SURGICAL EDIT** | One line: ":8161 … (the E-3(b) split, Stage F)" → drop the stage reference, keep the split. |
| `DECISIONS.md` | **SURGICAL EDIT** | Row-by-row treatment in §3.1: card rewrites for D23–D28 transition framing, D16 staleness fix, D10/D15 resolution, replacement of every "full reasoning: [rebuild plan / engineering.md]" pointer, §Stage preamble war-story rescue (#1), pointer line in the header. |
| `ARCHITECTURE.md` | **SURGICAL EDIT** | C2 card: drop the v0 shape narrative from §How it got here (2026-08-05 / 07-27 / 07-26 discriminator entries), drop the "(archived)" v0 schema link. C10 card: drop the "(v0/v1 keyed …)" parentheticals, the 2026-08-05/07-27 history entries, and rewrite the `w-day5` watch-out to its timeless half (#4). §Vocabulary: remove the `discriminator` row (a newcomer no longer meets the term) — founder may instead keep it as a one-line tombstone. Everything else (splits, walkthroughs, founding posture) is current. |
| `HANDOFF.md` | **REWRITE** (its normal mode — the board is rewritten in place every session) | Drop the rebuild-executed narrative from §Where-we-are; make §Escalations self-contained: the intro's "Opened 2026-07-24 by … (WS-VC)" and the "full write-ups … in ws-video-clip.md §10" pointer go; E-2's card is rewritten in v2 terms (§3.2); E-3(b)'s resolved record moves to the worklog/leaves; suite counts drop the "(post-Stage-G demolition)" qualifier. |
| `onboarding/LEARN_LOOP.md` | **REWRITE** | The document's own 2026-08-07 banner says it: "the DP internals this document teaches are the pre-rebuild v0 service … This document's full rewrite is a client-testing-phase task." §3's C2/C10 cards, §4.2, §5, §6 and §8 describe the v0 world; §7's decision list carries v0 framing. 130 census hits. The rewrite teaches the v1 loop; the §8 "pattern worth keeping" is rescued (#10). Until the rewrite lands, an interim option is DELETE + a stub pointing at the DP charter — founder's call; a stale teaching view violates D22's same-session obligation either way. |
| `handoff/engineering.md` | **DELETE** | The founders' engineering thread: worklog 2026-07-08 → 07-28 plus an archive of delivered slices — 2,193 lines, all pre-rebuild history (118 census hits; `window_for` ×17, discriminator ×15, keyframe ×11, WS-\* ×9, w-day5 ×6…). Nothing imports it. **Consequential edits:** D3, D10, D12, D15, D16, D17, D18, D19, D20 cards cite it as "full reasoning" / "recorded in" — each pointer is replaced per §3.1 (the card keeps a self-contained rationale; depth lives in git history). HANDOFF's aspect-threads table row gets reseeded or dropped. |
| `handoff/research.md` · `design.md` · `hiring-ops.md` | **KEEP** | Seeded, census-clean. |

### 2.2 `product/contracts/`

| File | Disposition | Evidence |
|---|---|---|
| `c2_processed_record.v0.json` | **DELETE** | **Inert to the fleet:** storage and DP both validate v1 (`storage/app/schemas.py:29`, `data-processing/app/schemas.py:29` — `$id …c2_processed_record.v1.json`); no code loads the v0 file. Remaining references are prose: ARCHITECTURE C2 card "(archived)" link, `contracts/README.md`, LEARN_LOOP, storage CHARTER OQ7, platform `deploy/README-learn.md`, and three continuum comment/echo sites (`app/synth.py:70`, `run.sh:62`, `scripts/seam_check.py:433`) — the continuum sites describe the v0-shaped *parity fixtures* (§0.2) and get comment rewrites ("the retired v0 shape, kept as the parity reference's fixture format"), not behavior changes. |
| `c10_daylog.v1.json` | **DELETE** | **Inert:** storage validates the day-log against v2 (`storage/app/schemas.py:44`); the only code-adjacent references are docstring asides (`storage/app/models.py:274,365` — "SIBLING of c10_daylog.v1.json") plus prose (ARCHITECTURE, contracts/README, LEARN_LOOP, stage docs). Docstring one-liners edited. |
| `c10_training_window.v1.json` | **KEEP** | Current contract; validated by a green test (`storage/tests/test_windows.py:566` `validate_c10_window`). *v1 here is not old-world* — the window ledger never re-versioned. |
| `c2_processed_record.v1.json` | **SURGICAL EDIT** | The live wire. Keep the negative guards ("`discriminator`, `enrichments`, `content.kind` DO NOT EXIST here and must not be re-added" — that is a *current* prohibition); strip rebuild citations (plan §-refs, WS-VC D-xx ids, `dp-rebuild-v1`) from `description` prose. |
| `c10_daylog.v2.json` | **SURGICAL EDIT** | Same treatment: keep the shape + trap notes; strip "rebuild Stage A/E" and discriminator-history phrasing from descriptions. |
| `contracts/README.md` | **REWRITE** | **Materially stale, independent of the purge:** its table still marks `c2…v0.json` "the running wire" and `c10_daylog.v1.json` "the running read", with v1/v2 as "the rebuild target" — false since the Stage F cutover (ARCHITECTURE and the DP charter both say v1/v2 live). Rewrite to the current fourteen-files-minus-deletions state; keep the two "Why … is two files" arguments and the `pattern`-trap note (why-rescue #14 keeps the lesson without the D17 story); drop the D18-minting and Stage-A narratives. |
| all other schema files | **KEEP** | Current contracts (C1, C3, C4, C6, C9, C12, C13 ×2, C14). `c14_reservoir_ledger.v0.json`'s one census hit is the `local_window_date` *prohibition* in a description — a current guard, keep. |

### 2.3 Data-processing service — `docs/`

| File | Disposition | Evidence |
|---|---|---|
| `docs/refactor_dp_service.md` | **DELETE** | The rebuild's design + migration plan, Status: EXECUTED 2026-08-07 — a transition record by its own declaration ("this doc is now its historical record"). 103 hits; home of D-R1…D-R6 and OD-1/2/3. Inert: cited only by prose (DECISIONS D23–D28 cards' "full reasoning" pointers, CHARTER §Slot Law banner + §Condensed history, HANDOFF, contracts/README, c2 v1/c10 v2 descriptions, LEARN_LOOP). **Consequential edits:** every one of those pointers (§3.1 gives the D23–D28 card rewrites); the **OD-2 backfill obligation** and the **OD-3 all-at-once rationale** are restated where they now bite (DP HANDOFF Next 2 already carries the backfill item — verify self-containment). |
| `docs/refactor_stage_A.md` … `refactor_stage_G.md` (7 files) | **DELETE** | Per-stage worklogs of the executed rebuild (2,721 lines, ~700 hits total). Inert: no code references; prose references from DP HANDOFF §"The rebuild is history", `onboarding/review_actions.md`, worklog.md, DECISIONS (D20's re-baseline stamp cites Stage E WP-E4), contracts/README. Consequential edits listed per citing file below. Note stage F holds the only written record of founder rulings R1/R2 and the drill transcripts; the *standing* outcome (async default paid, live-stream testing next, R2's transfer) is already on the boards — verify nothing else load-bearing is unique to it before deletion. |

*(`docs/record-emission-law.md` no longer exists — retired at Stage G; 7 files still cite
the filename and are edited per their own rows.)*

### 2.4 Data-processing service — `handoff/`

| File | Disposition | Evidence |
|---|---|---|
| `handoff/worklog.md` | **DELETE** | The retired v0 workstream history (M0–M8 era: keyframe pipeline, ProcessedUnit hooks, discriminator-tagged sidecar records, `INGEST_ISOLATION`, DONE-strikethrough archaeology) plus the Stage-A ratification entry. Pure transition record. Inert: prose-only references (DP HANDOFF ×2, review_actions). |
| `handoff/ws-video-clip.md` (1,342 ln) | **DELETE** | The WS-VC design record: the retired record-vs-mutation law's birthplace, the internal D-01…D-16 / A-1…A-19 / O-x / L-x registers, keyframe-vs-clip economics. 369 hits. Inert to code — but **live citations exist in current docs and code comments**: root HANDOFF §Escalations points at "§10" for E-1/E-4/E-6's measured numbers; DP charter OQ10 cites O-2 as the *still-governing* OCR quality bar; ~15 live code docstrings cite D-xx/A-xx ids (§2.8). **Consequential edits:** HANDOFF escalation cards become self-contained (E-1 keeps its "5.8×" figure inline); the O-2 bar's *definition* (≥0.85 recall, ≤0.10 CER over ~200 hand-labelled real macOS frames) moves into DP charter OQ10 verbatim; code docstrings per §2.8. |
| `handoff/ws-video-clip-buildlog.md` (1,275 ln) | **DELETE** | WS-VC build diary; the single largest hit count (393). No live citations found outside the WS-VC family itself. |
| `handoff/ws-video-clip-eval.md` · `ws-video-clip-probe.md` | **DELETE** | The O-2/O-8 eval harness records and the multi-image probe record. The E-3(a) probe *conclusion* (vLLM 0.24.0 defaults suffice) already lives on the HANDOFF board. |
| `handoff/ws-video-pipeline.md` | **DELETE** | The v0 keyframe-pipeline workstream (61 hits, `vidproc-vlm-v0`, ProcessedUnit sub-spans). |
| `handoff/ws-dp-stage-graph.md` | **DELETE** | The v0 stage-graph workstream: kinds (primary/sidecar/mutate), best_effort, SlotView — all deleted concepts (43 hits). The durable journal it introduced survives in code and is documented by the charter (L8) — nothing load-bearing is unique here. |
| `handoff/ws-dp-hardening.md` | **DELETE** | The v0 hardening slice (SlotView capabilities, mutate-overlap chaining, `INGEST_ISOLATION`) — 32 hits. `INGEST_MODALITY_LIMITS` (still real, unset) is already carried by HANDOFF Next 3. |
| `handoff/ws-async-observability.md` | **DELETE** | The async/metrics workstream record. Its standing content (async reply shape, `/continuity` fields, zero-silent-loss invariant) is D16's card + charter OQ13. **Consequential edits:** charter OQ13's "See handoff/ws-async-observability.md" pointer; recording R-2's "Detail:" pointer. |
| `handoff/ws-audio-pipeline.md` | **DELETE** | The v0 audio workstream (discriminator-tagged sidecar records, `+diar-*` fragments). The current audio truth is the charter + HANDOFF ("real backends, live"). |
| `handoff/ws-m1-continuity-asr.md` | **DELETE** | v0 M1 continuity/ASR record (asr-fw-v1 dialect era). |

### 2.5 Data-processing service — CHARTER, HANDOFF, onboarding, scripts, tests, dashboards, readings

| File | Disposition | Evidence |
|---|---|---|
| `CHARTER.md` | **SURGICAL EDIT** | The keeper document, 82 hits, nearly all deliberate. Under the policy ("condensed histories … leave the tree"): **§Condensed history is removed whole** (six tombstone paragraphs — discriminators, SlotView, INGEST_ISOLATION, DIALECT_FREEZE, the two-record shape, the emission law); the §Slot Law banner drops its "It replaced §Record-vs-mutation law…" paragraph and the plan pointer; the "Dead concepts, deleted with their subject matter" list is removed (or, minimal option, kept as the one-line guard the tests enforce — founder call); the status-line/changelog bullets (2026-07-25 → 08-07, all transition entries) collapse to a dateless current status; §On C2 drops its final "The v0 shape … is gone; its reasoning is in §Condensed history" sentence; OQ10/12/14 drop their v0-answer asides (§3.3). **Why-rescue obligation (#9):** before deletion, verify each L-law carries its own first-principles reason in place — L2/L3/L4/L5/L9 already do; add the one-liner for "why no discriminator is safe" to L3 (it is there: "L2 is why dropping it is safe") and the OQ14(b) bbox story is rewritten timelessly (§3.3). |
| `HANDOFF.md` | **SURGICAL EDIT** | Current board. Remove §"The rebuild is history" (its four pointers die with their targets); keep the stage→commit table? **No** — it is a transition record; the commit ranges live in git. Next-items 2 (backfill / OD-2) and 4 (captioner `vlm.v1→v2` deploy, per-frame-v0 removal) are *forward obligations* and stay, reworded self-contained ("the owed reprocess-by-version tool"; "the prompt-pack version bump pending fleet restart"). Status line drops "(post-Stage-G)" and the rebuild parenthetical. |
| `onboarding/field-guide.html` | **SURGICAL EDIT** | Rewritten for the v1 world at Stage G (D22 view), but still carries a history module: 4 discriminator + 2 emission-law + 3 pre-rebuild markers + rebuild-stage refs. Strip the era-narrative passages; the guide teaches the running world only. (Same-session obligation applies once Phase 2 touches the charter it teaches.) |
| `onboarding/review_actions.md` | **SURGICAL EDIT** | Live board (active item: first review of the v1 guide — keep). Strip the two explanatory paragraphs narrating the v0 guide's retired subject matter and findings; keep the pointer to where outcomes live, minus the stage-doc links. |
| `scripts/calibrate_delta.py` | **DELETE** | v0 calibration tool: tunes `VIDEO_OCR_IDLE_PEAK` — a knob that no longer exists (`app/config.py` has no `VIDEO_*` settings; L4 forbids the class). Inert: no test imports anything from `scripts/` (grep: zero); the one live reference is a provenance docstring, `app/vision/delta.py:157`, edited to state the calibrated values without naming the dead tool ("thresholds calibrated on real screen captures, 2026-07; recalibration requires a new probe"). |
| `scripts/vlm_probe.py` | **DELETE** | v0 first-contact probe: `VIDEO_VLM_URL`/`VIDEO_VLM_MODEL`/`VIDEO_CLIP_MAX_FRAMES` env knobs (dead), WS-VC citations. Inert: standalone, no imports. Its Stage-F boot-probe successor, `tests/test_vlm_boot_probe.py`, is current and stays. |
| `servers/drill_stage_b.py` | **DELETE** | The Stage B migration drill harness. Referenced only by the stage docs being deleted. |
| `requirements-video.txt` | **SURGICAL EDIT** | Comment block still explains keyframe-era knobs (6 `VIDEO_*` + 3 keyframe mentions). Pins stay; comments rewritten to the clip stages. |
| `dashboards/data-processing.json` | **KEEP** | Census-clean; current metric families. |
| `readings/OCR processing - thoughts.md` | **KEEP** (flag) | Reference reading; 11 "keyframe" hits in generic video-analysis usage predating/independent of the v0 pipeline. Readings are source material, not law. Founder may prefer DELETE for purity; nothing cites it. |
| `readings/Choosing Frame Rate and Resolution….md` | **KEEP** | Census-clean. |
| `HANDOFF.md §Gotchas`, `run.sh`, `servers/*` READMEs/PROVENANCE | **SURGICAL EDIT** (light) | Scattered single "Stage X"/WS-VC citations in provenance notes → self-contained dates ("2026-08-06, migration drill") instead of stage names. |

### 2.6 Data-processing service — `app/` and `tests/` (comment-level residue)

No v1 *behavior* carries old-world semantics — `app/config.py` has no `VIDEO_*`/`ISOLATION`
knobs, `pipeline.py` hashes two components, the executor emits one record. What remains is
**comments, docstrings and test names** citing the dead world or its documents. Proposed
treatment: one **SURGICAL EDIT** sweep, semantics-frozen (suite must stay 569 green, and no
string that feeds `pipeline_version` or any record byte may change — L4 makes comment-only
edits provably safe via T-1):

- **Tombstone comments that guard against resurrection — keep, reworded self-contained.**
  `app/main.py` (`DIALECT_FREEZE` tombstone), `app/config.py` (`INGEST_ISOLATION` note),
  `app/stagegraph/stage.py` ("dead concepts" note naming mutate/sidecar/SlotView/best_effort),
  `app/models.py` + `app/schemas.py` (v1 "no discriminator/enrichments/kind" guards),
  `clipprep.py`'s "v0 env knob (dead) → code pin" table. These state *current* prohibitions;
  they keep the prohibition and lose the history ("was retired at Stage G" → "does not exist;
  adding one is a version-law violation").
- **WS-VC/A-xx/D-xx citations in live docstrings — replace with self-contained statements.**
  Affected (from the census matrix): `app/vision/delta.py` (14), `app/vision/ocr/assemble.py`
  (11), `app/stages/video/clipcap.py` (11), `app/stages/video/screentext.py` (7),
  `app/vision/clip.py` (6), `app/vision/clipcap/vlm.py` (6), `app/vision/budget.py` (5),
  `prompts/__init__.py` (3), `vertex.py` (3), `parse.py` (3), `redact.py` (2), plus tests:
  `test_clipcap.py` (8), `test_delta.py` (7), `test_budget.py` (3), `test_ocr_assemble.py`
  (4), `conftest_video.py` (4), `test_parse.py` (2), `test_screentext.py`, `test_video_graph_v1.py`.
  Example: `screentext.py`'s "record presence is the coverage signal (rider R3(e))" →
  "an empty-string OCR slot is an honest empty claim (Slot Law L11); consumers must never
  read absence as no-text."
- **Rebuild-stage refs in docstrings** (`journal.py`, `model_client.py`, `supervisor.py`,
  `asr.py`, `acoustic.py`, `schemas.py`, `servers/*`, `tests/test_t*`): "(Stage D)" /
  "(rebuild Stage C)" provenance → plain dates or drop.
- **`prompts/LOCK.json`** carries a `per-frame-v0` history entry — the lock archive is
  functional relock history; **KEEP** (its entries are content-addressed identity evidence;
  editing a lock archive falsifies it). Flag for founder: this one file legitimately retains
  an old pack name.
- **Old-shaped fixtures** (`tests/fixtures/__init__.py` `content.kind` helper,
  `test_ingest_mock.py`/`test_seam_v1.py`/`test_pipeline_v1.py` v0-era names): the tests are
  green against v1 code — most mentions are negative assertions or updated helpers; rename
  only where a name still implies v0 semantics. `test_pipeline_v1.py`'s 5 discriminator hits
  are assertions that the id has two components — keep, self-contained wording.

### 2.7 Storage's v0-parity apparatus (named in the task)

| File | Disposition | Evidence |
|---|---|---|
| `services/storage/scripts/daylog_parity_diff.py` | **KEEP** (exempt; optional narrative-only SURGICAL EDIT) | See §0.2. Imported by green `tests/test_daylog_parity.py` (in-process, `:90`); it *is* D20's bar, re-baselined by D28. Its 16 discriminator hits are the proof's v0 fixture side. The optional edit strips only the D20-narrowing *story* from the header while keeping the three-tier bar statement (why-rescue #7 provides the timeless header). |
| `services/storage/scripts/daylog_parity_diff.out.txt` | **KEEP** | The committed proof output; the test's docstring names it ("never touches the committed …out.txt"). Deleting the output un-proves M9's "committed with its output" exit criterion. |
| `services/storage/tests/test_daylog_parity.py` | **KEEP** | Green suite member; the bar-in-CI. |
| `services/continuum/app/clients/daylog_client.py` (`LocalDayLogClient`) · `app/daylog.py` · `app/synth.py` · `tests/_helpers.py` and the ~8 tests exercising them | **KEEP** | The untouched v0 reference the proof diffs against, plus its fixtures. Deleting or "modernizing" any of it breaks `test_daylog_parity.py` and continuum's suite. Comment-level c2-v0 mentions get the §2.2 rewording. |
| `services/continuum/scripts/seam_check.py` · `run.sh` seed path | **REWRITE** (follow-up engineering, not purge) | Stale against the live v1 wire (§0.2). D20's "golden" clause (b) references the seam check; until rewritten, that clause is unsatisfiable on the current fleet. |

### 2.8 Platform deploy — the cutover kit

| File | Disposition | Evidence |
|---|---|---|
| `deploy/cutover_wipe.py` | **DELETE** | The OD-2 fresh-forward wipe, a one-shot executed at the Stage F cutover (2026-08-07). Inert at runtime: imported only by its own test; `run_vllm.sh` mentions it in a comment (edited). Its wipe/keep classification prose is historical; the *general* lesson (derived vs sacred stores) already lives in storage's charter. |
| `deploy/test_cutover_wipe.py` | **DELETE** (with the tool) | Guard test for a tool that no longer has a job; deleting both keeps the suite green (test count change is expected and noted for the gate). |
| `deploy/ROLLBACK-stage-F.md` | **DELETE** | The cutover rollback runbook. The rollback window is closed by construction — the v0 `/context` was wiped at cutover, so "roll back" no longer names a possible action. |
| `deploy/learn.env.stage-f-unrepoint` | **DELETE** | The env half of the closed rollback window. |
| `deploy/README-learn.md` | **SURGICAL EDIT** | One c2-v0 mention + one stage ref. |
| `deploy/README.md`, `run_all.sh`, `run_learn.sh`, `run_vllm.sh`, `make_sample_wav.py`, selftests | **KEEP** (light comment edits) | Current bring-up; `run_learn.sh`/`run_vllm.sh` each carry one stage-comment. |

### 2.9 Adjacent trees (outside the mandated scope, listed so the census is honest)

Not in this phase's disposition mandate, but the census hits them; Phase 2 should either
extend scope explicitly or leave them for a later sweep:

- **recording/**: CHARTER OQ3's "Why it's this way" cites dead `VIDEO_FRAME_MAX_WIDTH`/
  `VIDEO_KEYFRAME_INTERVAL_S` knobs and "Qwen3-VL keyframe captioning" (superseded — the
  cost dial is now clip cadence/budget under L4 code pins) → SURGICAL EDIT.
  `DECISIONS.md` R-1/R-2: current-world rows wearing old cards — "*Was `D-M1-6`*" provenance,
  the sessions→captures naming note, and the **stale** "Flipping `INGEST_ASYNC=1` on the
  fleet remains the open D16 re-drive-drill decision" (paid at the Stage F cutover) →
  SURGICAL EDIT. `handoff/ws-a…f` + `worklog.md` + `alpha-runbook.md`: pre-rebuild
  workstream records (`D-M1-*`, `D-E7` registers — ws-e-extension alone has 23 hits) →
  same DELETE logic as DP's ws-files if scope extends. Code/client comments citing `D-M1-*`
  (~20 files, incl. extension JS): self-contained rephrase. `onboarding/field-guide.html`
  (12 `D-M1-*` hits): D22 view, surgical edit.
- **storage/**: CHARTER §Day-log materialization card still states the *old* dedup rule
  ("latest `ingest_time` wins per `(chunk_id, content.kind, discriminator)`") — superseded
  by D27/D28 and contradicting its own M9 card → SURGICAL EDIT (a real drift, found by this
  inventory). OQ6/OQ7 old-world content per §3.4. `handoff/worklog.md` (discriminator,
  w-day5) → transition record. `app/daylog.py`/`db.py`/`models.py` comment-level tombstones
  → keep-guard rewording.
- **continuum/**: CHARTER OQ10's deletion narrative + `LocalDayLogClient` asides →
  surgical, except parity-apparatus mentions (§2.7). `DECISIONS.md` C-rows per §3.5.
  `handoff/phase-*/overnight-*/ws-*` reports: research records — recommend KEEP (they are
  the parity/provenance chain for Morpheus, `b3c58e1`), founder may rule otherwise.
  `app/window.py`'s deletion-rationale comments (`window_for` ×3): rescue #13 then trim.
- **platform/ · inference/ · input/ · output/**: `HANDOFF.md`s carry WS-\* workstream ids
  (their own serve-loop workstreams — *not* the DP rebuild's; renaming is cosmetic) → leave,
  or normalize naming in a later sweep.

### 2.10 Inertness summary for every proposed DELETE

| Deletion | Fleet/runtime import? | Green-test import? | Doc citations to fix |
|---|---|---|---|
| `contracts/c2…v0.json` | **No** (v1 `$id` validated both sides) | No | ARCH C2 card, contracts/README, LEARN_LOOP, storage OQ7, README-learn, 3 continuum comments |
| `contracts/c10_daylog.v1.json` | **No** (v2 `$id`) | No (comment-only in `test_windows.py`) | ARCH C10 card, contracts/README, LEARN_LOOP, 2 docstrings |
| `docs/refactor_*.md` (8) | No | No | DECISIONS D20/D23–D28 cards, DP CHARTER/HANDOFF, review_actions, contracts/README, worklog |
| `handoff/ws-*.md` (9) + `worklog.md` | No | No | root HANDOFF §Escalations, DP charter OQ10/OQ13, recording R-2 pointer, ~25 code docstrings (§2.6) |
| `handoff/engineering.md` | No | No | 9 DECISIONS cards' "full reasoning" pointers, HANDOFF aspect table |
| `scripts/calibrate_delta.py` · `vlm_probe.py` | No | **No** (zero `scripts.` imports in tests) | `app/vision/delta.py:157` docstring |
| `servers/drill_stage_b.py` | No | No | stage docs only (co-deleted) |
| `deploy/cutover_wipe.py` + test + `ROLLBACK-stage-F.md` + `learn.env.stage-f-unrepoint` | No (one-shot, already executed) | test co-deleted | `run_vllm.sh` comment, stage F doc (co-deleted) |

**Not deletable** (live imports proven): `daylog_parity_diff.py` (+`.out.txt`),
`test_daylog_parity.py`, `LocalDayLogClient`/`synth.py` and their tests — §0.2/§2.7.

---

## 3. Register classification

### 3.1 `product/DECISIONS.md` — every D-number

Classification: **CURRENT** (governs the running system as-is) · **OLD** (superseded by the
rebuild or governing deleted machinery) · **MIXED** (a surviving decision wearing an
old-world card — rewrite proposed). "Card work" lists the Phase-2 edit for rows that stay.

| # | Decision | Class | Card work / lineage consequences |
|---|---|---|---|
| **D28** | C10 v2 + whole-record retraction | **CURRENT** | Card: drop "(rebuild Stage E, WP-E4)" and the plan pointer; state the re-baseline as a fact with its date and result (31 checks, both origins, tier A byte-identical). The "old `(chunk_id, kind, discriminator)` key collapse" sentence → "one record per chunk leaves `(chunk_id)` as the whole key." Lineage "joint row with storage · re-baselines D20's parity bar" stays (D20 stays). |
| **D27** | Heal ledger + `created_at`/`updated_at` | **CURRENT** | Card: replace "full reasoning: rebuild plan §1 L8 + §5.1" with "reasoning: DP charter §Slot Law L7/L8 + ARCHITECTURE C10 card" (both carry it). The two Watch-outs are current law — keep; drop "(corrected … Stage D close-out 2026-08-06)" datebook. |
| **D26** | Machinery/bureaucracy split | **CURRENT** | Card: lineage "retires `isolation.py` · `INGEST_ISOLATION` · `DP_DIALECT_FREEZE` (condensed history at Stage G)" → "retired the v0 in-process model hosting; nothing replaced it because L9 removed the premise." Body: drop "at Stage B, at once (OD-3)" — restate timelessly: "all four models moved behind the server seam in one step, so no half-migrated calling convention ever existed." |
| **D25** | Version law | **CURRENT** | Card: "executable at rebuild Stage C (T-1 … T-3 …)" → "executable: the T-1 determinism matrix and T-3 composition test enforce it in CI." Drop plan pointer. |
| **D24** | C2 v1: one record per chunk | **CURRENT** | Card: drop the last bullet ("v0 stays the wire until the Stage F cutover; … beside-built on `dp-rebuild-v1`") — spent transition clause; drop "a drift v1 closes (ruled …)" phrasing → state the rule ("`source{}` carries the D17 trio verbatim, `device_clock` included"). Lineage stays: D10's shape clause, D8's partial supersession, D16's restatement, D19's discriminator clause are all rows that remain (see below). |
| **D23** | Slot Law replaces the emission law | **CURRENT** | The register's record *that a retirement happened* is the one place the old law's name legitimately survives — the lineage column exists for exactly this. Card: keep "In one line" (it is the honest supersession statement); trim "What was decided" bullet 3 (the dead-concepts enumeration — lives in git); drop the Watch-out (it narrates `record-emission-law.md`'s Stage-G retirement — spent); "executable at Stage C … Stage F cutover, Stage G close" → "EXECUTED — the test spine T-1…T-6 enforces it in CI on the running service." Replace the plan pointer. |
| **D22** | Onboarding teaching views | **CURRENT** | No old-world content. Keep. |
| **D21** | STYLE.md is the SOP | **CURRENT** | Keep. Optional: the "How it got here" trim-story (331→246 tokens) is process history — candidate strip under the policy; harmless either way. |
| **D20** | Cutover exit bar | **MIXED** | The decision has two halves: (a) the three-tier parity bar — **current law**, re-baselined by D28, executable in a green test; (b) the "golden" definition for the *storage↔continuum cutover* — a completed event. Proposed rewrite: the card becomes "the day-log parity bar" (tiers a/b/c + the general representation-vs-content rule), keeps `BUILT` + the D28 re-baseline lineage, and drops clause (b)'s cutover checklist; "full reasoning: engineering.md §Worklog" → the bar's own script header + storage M9. The seam-check reference survives only if the seam check is rewritten for v1 (§2.7). |
| **D19** | Stage: PROTOTYPE | **CURRENT** | The operative posture; every charter banner cites it. Card: the seven-calls list keeps its two live-lineage items (retention, storage-local, wipe-not-migrate, cron trigger, C5 deferral, `home_tz` declared) — the "C2 `discriminator` surfaced" call is already lineage-marked "retired by D24"; under the policy it may be dropped from the list (the lineage column already records the retirement). "Full reasoning" pointer → self-contained (the card already is). |
| **D18** | Storage owns the day-log | **CURRENT** | Governs custody, the watermark (axis moved by D27 — marked), opaque `window_id`, C12/C13/C14 minting. Card: replace the engineering.md pointer; body is already timeless. |
| **D17** | Timezone custody | **CURRENT** | Fully governing (C1/C2 `source{}`, C12, renderer). Replace the engineering.md pointer; body timeless. |
| **D16** | Async `/ingest` reply shape | **MIXED** (current decision, stale card) | The wire governs (202/200-dedup/503, `/continuity` additive fields, never-falsely-clean). Stale: "`INGEST_ASYNC` is off by default" — **async has been the operating default since the Stage F cutover paid this row's own gate**; "recorded in … DP canvas (pinned prose at merge)" points at pre-rebuild canvases. Rewrite: state the shape + the invariant, add one lineage line "async became the operating default 2026-08-07 after the re-drive drill this row required"; fan-out clause already marked restated-by-D24. |
| **D15** | Post-deep-session build order | **MIXED** → mostly OLD | The sequencing (continuum kickoff next, C10 v0 freeze gate, D9 parallel slice) is spent history. The one surviving clause — "DP image/text pipelines (M2) are deferred until a producing surface exists" — still governs (DP charter and boards cite it). Also contains "Screen text already flows via the video-keyframe OCR weave" — retired machinery. Proposal: **rewrite the card to the single surviving deferral clause** (one line, no narrative), or remove the row and re-home the deferral as a D-cited fact in the DP charter — the first option keeps the citation graph intact and is recommended. |
| **D14** | Capture transport | **CURRENT** | Segmented HTTP governs all three clients; the deferred streaming leg is a live design position. Keep. |
| **D13** | Consent gate de-prioritized | **CURRENT** | Still the operative posture (lands before any non-team pilot). Keep. |
| **D12** | Branching + beta model | **CURRENT** | `dev` branch model stands; HANDOFF Next 6 cites it. Keep (the Gnandeep specifics are dated but factual). |
| **D11** | C1 is two legs | **CURRENT** | The running wire. Keep. |
| **D10** | Learn-loop skeleton | **MIXED** → mostly OLD | "Computer mic → ASR → `/context`, and nothing else" was the bootstrap slice — long since outgrown (diarization, vision, acoustic all live). Its shape clause is superseded by D24 (marked). Surviving value: it is C1/C2's minting record (ARCHITECTURE C1 card cites it). Proposal: **rewrite to a minting stub** — "Minted C1 and C2 with the first capture path (audio-only). Shape clause superseded by D24." — or remove the row and let D11/D24 carry the contracts' lineage. Stub recommended: D24's lineage column names D10, and self-contained phrasing beats a dangling number. |
| **D9** | Centralized observability | **CURRENT** | Backbone still owed (HANDOFF Next 2). Keep. |
| **D8** | OCR decoupled from the BWM | **CURRENT** (already correctly marked partially-superseded) | The surviving one-liner (specialist OCR feeds the caption) is live law (screentext stage, L11 corollary). Card already carries the D24 partial-supersession; no old-world narrative beyond that. Keep. |
| **D7** | POCs are reference, not source | **CURRENT** | Keep. |
| **D6** | Base model Qwen3-VL-32B | **CURRENT** | Keep (the D8-retired caveat is proper lineage). |
| **D5** / **D4** / **D3** / **D2** / **D1** | Mobile app · wearable no-speaker · serve-loop-first · doc protocol · platform service | **CURRENT** | Keep. D3's engineering.md pointer → drop (card is self-contained). |

**Net register outcome:** no D-number is proposed for outright removal except optionally
D10/D15 (stub-vs-remove is the founders' call; stubs recommended — see the numbering note
in §5 either way). The pre-rebuild world's *dense* registers were never in this file — they
are the service-local and workstream registers below, which is where the sharp edge lands.

**Pointer replacements needed if §2.1's deletions execute** (every "full reasoning" /
"recorded in" target that dies): D3, D10, D12, D15, D16, D17, D18, D19, D20 (→
`handoff/engineering.md`); D23–D28 (→ `docs/refactor_dp_service.md`); D16 (→ "DP canvas
pinned prose"). Uniform replacement: the card carries its own compressed rationale (each
already does or gains one line per this table), and depth is one sentence: *"Full session
record: git history."*

### 3.2 Root `HANDOFF.md` — E-numbers

| # | State | Class | Treatment |
|---|---|---|---|
| E-1 (segment-seconds 10→60) | open | **CURRENT** (ask stands) | Make self-contained: keep the 5.8× figure inline; drop the ws-video-clip §10 pointer. |
| E-2 (retraction primitive) | partially BUILT | **MIXED** | The whole-record primitive is built and live (D28); the remaining ask is Platform-M2 orchestration + the reservoir cascade. The card's "Why it's this way" narrates the WS-VC double-count and the old `(chunk_id, content.kind, discriminator)` rule, and the Watch-out ("It must key on `content.kind`… Phase-3 proved…") argues for the **retired kind-granular design against D28's ratified whole-record one**. Rewrite: current shape (delete by `record_id`/`chunk_id`/`pipeline_version`, dry-run manifest, day-log cascade built), remaining legs, done. |
| E-3(a) | resolved during build | **OLD** (record) | Resolved-record lines leave the board (boards are rewritten, not accreted); outcome already embodied in `serve_vllm.sh`. |
| E-3(b) (captioner endpoint) | resolved 2026-08-07 | **CURRENT** outcome, historical card | Keep the fact (`:8161`, GPUs 0-1, TP2) where it bites (STACK + DP charter OQ3 — **note: the promised OQ3 charter edit never landed; drift found**); the escalation card itself leaves the board. |
| E-4 (per-fragment timestamps) | open | **CURRENT** | Ask stands (continuum-renderer change). Trim the D17-premise-dissolution narrative to the one-line current statement; verify the `daylog.py` line refs against the *storage* renderer (they cite the v0 layout). |
| E-5 (parked additive C2 edit) | open — "do not take yet" | **CURRENT** | Reword away from `enrichments.text_regions[]` (that block no longer exists): "an additive slot/field + root `quality{}`, parked until the first geometry/quality consumer" (the DP charter OQ14(b) already states this correctly). |
| E-6 (auto-retry failed segments) | open | **CURRENT** | Keep; self-contained already. |

### 3.3 DP charter — OQ register (1–14)

| OQ | Class | Treatment |
|---|---|---|
| 1 (C1 semantics — resolved D11) | **CURRENT** | Keep. |
| 2 (C8 latency budget) | **CURRENT** | Keep. |
| 3 (GPU placement) | **CURRENT**, stale | E-3(b) resolved it 2026-08-07; the promised charter edit is missing. Strike-through with the ruling (`:8161` split) — an ordinary doc-truth fix, fold into Phase 2. |
| 4 (device clock — resolved D17) | **CURRENT** | One old-world aside: "(Under v0 this was the emission law's T2 test; the rule survived the rebuild as L10.)" → drop the sentence; the L10 statement stands alone (why-rescue #8). |
| 5 (reprocessing policy) | **CURRENT** | Keep (partially answered by L8 + the owed OD-2 backfill tool — cross-ref HANDOFF Next 2). |
| 6 (registry cache) | **CURRENT** | Keep. |
| 7 (captioning operating point) | **CURRENT** (research) | Keep. |
| 8 (teacher vs self-hosted) | **CURRENT** | Keep. |
| 9 (RWT verification — resolved D17) | **CURRENT** | Keep; residual metric note stands. |
| 10 (screen OCR — resolved D8 + WS-VC) | **MIXED** | The rules are current (CPU OCR server, code-pinned width, event-driven cadence, both-channels weave). Old-world bits: "*Resolved for screen video (WS-VC, 2026-07-25)*" attribution, the "O-2" bare citation (its ws-file home is proposed DELETE → inline the bar: ≥0.85 recall / ≤0.10 CER on ~200 hand-labelled real frames), "the D-09 grounding weave" internal id → "the L11 provenance corollary." |
| 11 (voice-to-person) | **CURRENT** | Keep. |
| 12 (non-speech audio) | **MIXED** | Rules current (`acoustic` slot). Drop the closing "The v0 answer — two records told apart by a within-chunk discriminator … — is gone with those concepts (§Condensed history)" sentence; the positive statement suffices. |
| 13 (ingest mode) | **CURRENT** | Async-default correctly recorded. Edits: drop "(the in-flight-kill recovery witnessed at the Stage F soak)" and the ws-async pointer; keep the OD-2 backfill obligation in plain words. |
| 14 (a) keyframe timing | **OLD** (dissolved) | The Was/Changed/Now/Payoff block narrates a v0 problem the rebuild removed at the root. Replace with one line: "A video chunk is one record carrying the C1 span verbatim (L2); per-keyframe sibling records cannot exist, so the old index-collision class is structurally closed." |
| 14 (b) bbox not emitted | **CURRENT** | The decision (no geometry until a consumer exists; L10) stands. Strip the `enrichments.text_regions` v0 framing — E-5's reworded card (§3.2) is the cross-ref. |

Also in the DP charter: §Scope's video row still says "per keyframe" in the OCR-specialist
line (v0 vocabulary — the running path is per selected clip frame) → one-word surgical fix.
The M-table (M0–M8) mixes met and open milestones with v0-era exit texts (M3 "ported from
POC Phase-2/3 machinery") → light surgical pass, keep the register.

### 3.4 Storage charter — OQ register (1–9) and cards

| Item | Class | Treatment |
|---|---|---|
| OQ1 (tech — resolved D19) · OQ2 (artifacts) · OQ4 (ids) · OQ5 (deletion vs weights) · OQ8 (double exposure) · OQ9 (cascade) | **CURRENT** | Keep. OQ8's premise survives the axis change (re-verify wording against `updated_at`). |
| OQ3 (clock skew — resolved D17) | **CURRENT** | Keep. |
| OQ6 (watermark semantics — resolved D18) | **MIXED** | Mostly current; its "Reprocessed records: one dialect per record, latest `ingest_time` wins, keyed `(chunk_id, content.kind, discriminator)` … kind-blind rule drops transcripts" paragraph is the **superseded** v0/v1 rule (D28: latest `updated_at` per `(chunk_id)`). Rewrite that paragraph to the v2 rule; the orderability argument (composed strings don't order; the store's own clock does) survives verbatim as the timeless why. |
| OQ7 (discriminator surfacing — resolved) | **OLD** | The entire item is the v0 discriminator's surfacing story, `pipeline.py` line refs included. Remove the item (the OQ preamble already blesses struck-through-in-place stability — removal leaves the numbered hole per §5; alternative: one-line tombstone "retired with the v0 record model, D24"). |
| §Day-log materialization card | **stale rule — SURGICAL EDIT** | States "latest `ingest_time` wins per `(chunk_id, content.kind, discriminator)`" — contradicts D27/D28 and its own M9 card. Fix to "latest `updated_at` per `(chunk_id)`." (Drift found by this inventory, worth fixing even if the purge stalls.) |
| §Scope D18 expansion banner | **MIXED** | The 2026-07-23 framing kept "because it is still the rationale" is a designed-in history block. Under the policy: compress to the standing rationale (three data jobs + profile prerequisite + cascade obligation) without the ratification chronology. Founder call — this is the storage charter's version of §Condensed history. |
| M5/M9 cards, §Retention, §Time index | **CURRENT** | Keep; M9's "How it got here" narrowing/widening story is why-rescue #7's source — strip after rescue. §How-this-charter-got-here (4 dated entries): transition records → remove under policy. |

### 3.5 Continuum — charter OQs and the C-register

Charter: OQ1–8 (research) **CURRENT** — keep. OQ9 (resolved D18) **CURRENT**. OQ10 —
**MIXED**: the trigger question is live; the card carries the `window_for`-deletion story,
the min-data-floor false-claim retraction, and datebooks → compress to the standing facts
(cron per `home_tz`; floor designed, not built — cross-ref HANDOFF Next 5). OQ11/OQ12
**CURRENT**. §Day-log-construction card + §C5 card: `window_for`/`local_date`/
`LocalDayLogClient` tombstones → keep the C5 "opaque token, never a date" instruction
(current law), trim the deletion narratives (rescue #13 first). §How-this-charter-got-here:
transition records → remove under policy.

`DECISIONS.md` (C-register):

| Row | Class | Treatment |
|---|---|---|
| C-1 (serve-time harness → inference) · C-2 (DP owns data heavy-lifting) | **CURRENT** (both still "pending founders' board") | Keep — they are live escalation state. |
| C-3 (synthetic text never in `/context`) | **CURRENT** | Keep (standing invariant; C14 card cites it). |
| C-4 (sequencing) | **OLD** (spent) | Remove or one-line stub. |
| C-5 (port, don't pin) · C-9 (naming: Morpheus) | **CURRENT** (provenance-bearing) | Keep — the `b3c58e1` snapshot hash is the parity chain's anchor (§0.2 apparatus); deleting the rows orphans the parity goldens' lineage. |
| C-6 (day-log terms as derived views) | **OLD** | "C2 v0 stays frozen; video captions per-keyframe" — v0 through and through; the surviving vocabulary lives in ARCHITECTURE §Vocabulary. Remove. |
| C-7 (storage owns the data jobs) · C-11 (contract consequences) | **OLD** (absorbed) | Both ratified into D18 and BUILT; the D-number carries the decision. Remove, or collapse to "→ D18" stubs. |
| C-8 (5-verb loop) | **CURRENT** | Keep (the running shape). |
| C-10 (recipe v1.0 target) | **MIXED** | v1.0/v1.1 story is spent; the live fleet trains under `consolidation-v2.0`. The no-over-calibration *principle* and the replay-source tie are current. Rewrite status + one-line principle. |

### 3.6 Recording — charter OQs and the R-register

Charter OQs: 1, 2, 5, 6, 7, 8, 9 **CURRENT** — keep (OQ8's demux/status text is current).
OQ3 — **MIXED**: the per-modality resolution conclusions stand, but the "Why" block cites
`VIDEO_FRAME_MAX_WIDTH=768`, `VIDEO_KEYFRAME_INTERVAL_S`/`VIDEO_MAX_KEYFRAMES` (dead knobs
— superseded by L4 code pins) and "keyframe VLM captioning" → rewrite those sentences to
the clip-path equivalents (768 px caption split / 1728 px OCR split are now code pins in
`clipprep`; the cost dial is clip cadence/budget). OQ4 (resolved, cites `D-M1-2`) —
**CURRENT** rule; re-cite as "ratified 2026-07-18 (recording × DP)" without the dead local
id. §Milestone-progress note: alpha-era narrative with `D-M1-5`, `D-E7` ids → surgical
trim. §Parked (gap-report reconciliation) — **CURRENT**, keep as-is (live reasoning).

`DECISIONS.md` (R-register): R-1, R-2 — **CURRENT** decisions (recording's side of
D14/D16), **old cards**: strip "*Was `D-M1-6`/`D-M1-5`*" and the pre-register provenance
footer; fix R-2's stale closer ("Flipping `INGEST_ASYNC=1` … remains the open D16
re-drive-drill decision" — it flipped at the cutover); the naming note (sessions→captures,
2026-07-29) is a transition record → its surviving fact is the glossary's, drop here.

### 3.7 Input · Output · Inference · Platform charters

All four are census-clean or near-clean in their registers; every OQ is **CURRENT** (the
serve-loop world was not rebuilt). Platform OQ2/charter rows still describe the GPU
allocation as an unresolved proposal — E-3(b)'s ruling partially answers it; same drift
class as DP OQ3, fix together. No old-world rows to remove in any of the four.

### 3.8 The registers that die whole (with their documents)

| Register | Home (proposed DELETE) | Fate of citations |
|---|---|---|
| WS-VC pinned decisions D-01…D-16, adversarial findings A-1…A-19, gates O-1…O-9, flags L-1…L-x, asks E-1…E-6 (the *originals* of today's board E-numbers) | `ws-video-clip.md` + buildlog/eval/probe | Board E-numbers survive on `HANDOFF.md` (§3.2) — they are the *current* register; the ws-internal ids vanish. ~25 live code docstrings citing D-xx/A-xx → self-contained rephrasing (§2.6). O-2's bar inlined into DP charter OQ10. |
| D-R1…D-R6 (draft rebuild rows) | `refactor_dp_service.md` §7 + `refactor_stage_A.md` | None live — D23–D28 are the ratified names everywhere that matters. |
| `D-M1-1`…`D-M1-6`, `D-E7` (recording pre-register rows) | recording ws-files + code comments | R-1/R-2 keep the two that were founders' decisions (as D14/D16 implementations); code/client comments rephrased (§2.9). |
| O-1…O-11 (the 2026-07-25/26 doc-review items) | `LEARN_LOOP.md` §8 + `engineering.md` | Die with their documents; the *pattern* is rescued (#10). |
| Founder rulings R1/R2 (rebuild gate rulings) | `refactor_stage_F.md` | R2's standing consequence (client live-stream testing is the next phase) already lives on both boards — verify then delete. |
| T1–T5 / R1–R5 (emission-law tests + riders) | already deleted with `record-emission-law.md` at Stage G | Only mentions remain; leave with their carrying documents. |

---

## 4. The why-rescue list

Every place a current law leans on a war story. The story leaves; the reasoning stays —
quoted, then the proposed timeless replacement.

1. **DECISIONS.md §Stage — why ids are stable handles.**
   Quote: "An id that encodes meaning has to change when the meaning does, and this repo
   has already paid for that lesson once. D18's `window_id` was `w<local-date>` until the
   window stopped having a local date; re-keying it moved filesystem paths, the training
   seed and C5 lineage at once."
   Replacement: "An id that encodes meaning must change when the meaning does, and every
   consumer that parsed it breaks with it. Ids are stable handles; meaning lives in the
   columns beside them — so a decision's number never changes, only its status."

2. **ARCHITECTURE C10 card — the `w-day5` watch-out.**
   Quote: "`w-day5` is a mess, not a precedent. The literal was written by a pre-D18
   continuum smoke script, retired 2026-07-28… Two on-disk C5 entries still carry it."
   Replacement: "`window_id` has exactly one minter and one validator. A second minting
   site can violate the total order silently (`w-day10` < `w-day5` under string order), and
   the fixed width and zero padding are the entire basis of 'string order ==
   chronological order' — do not tidy them away."

3. **ARCHITECTURE C2 card — mirrors move with the schema.**
   Quote: "DP's and storage's pydantic mirrors are `extra=\"forbid\"` — the trap D17 hit. A
   field added to the schema but not to both mirrors fails closed."
   Replacement: "Both mirrors are `extra=\"forbid\"` on purpose: an edit that lands in the
   schema but not in both mirrors fails closed instead of passing silently. Every C2 field
   change is therefore one change with four parts — schema, two mirrors, card."

4. **DECISIONS D24 card — why `processed_at` does not exist.**
   Quote: "No `processed_at` (ruled 2026-08-06): a wall-clock field inside the record
   breaks the §5.1 byte-compare (every reprocess would re-window) and makes T-1 unpassable."
   Replacement (already nearly timeless — keep, minus the ruling date and §-pointer): "No
   wall-clock lives inside the record: it would make byte-identical reprocesses impossible,
   so every redelivery would re-window and determinism would be untestable. Latency is a
   `/metrics` matter; storage owns `created_at`/`updated_at`."

5. **Storage §Retention — why policy is data, versioned.**
   Quote: "we already learned this shape once: the gate policy was split from the training
   recipe (2026-07-24) precisely so a threshold change could not fork an artifact id."
   Replacement: "A policy value must be changeable without forking any artifact's identity
   or re-running any compute keyed on it — so retention is a versioned document the service
   reads, never a constant in code."

6. **Storage M9 card — how the parity bar got its three tiers.**
   Quote: "The bar was narrowed 2026-07-27 (D20) after the first run failed it… continuum's
   `seg_id` *was* `floor((t − window_start)/segment_seconds)` over an event-time window
   origin… Measured, not argued: shifting that fixture's origin 1–9 s failed tier A at all
   nine offsets."
   Replacement: "The bar compares exactly what the trainer can see: byte-identity for block
   text/ordering/ids/anchors/quality and segment payloads (tier A); a proven
   order-preserving bijection for labels nothing external stores (`seg_id`, tier B); and
   deliberate exclusion for self-referential cache keys (`content_fingerprint`, tier C).
   It runs over an aligned *and* a misaligned window origin because the bucket grid is
   origin-independent and a proof that only ever sees the aligned case proves nothing."

7. **DP charter OQ4 — provenance vs. produced signal.**
   Quote: "(Under v0 this was the emission law's T2 test; the rule survived the rebuild as
   L10.)"
   Replacement: delete the sentence — the surviving rule is already stated timelessly one
   line above: "these fields are envelope *provenance we forward*, not signals we produce,
   so the L10 consumer-today rule does not gate them — the same way `device_id` and
   `blob_ref` are not gated."

8. **DP charter §Condensed history — the six tombstones** (discriminator, SlotView,
   INGEST_ISOLATION, DIALECT_FREEZE, two-record shape, the emission law itself).
   The section is war stories *by design*; the policy names condensed histories as leaving.
   Rescue check performed: each Slot Law law already carries its first-principles reason in
   place — L2 ("adding a second record per chunk requires forking the contract"), L3 ("L2
   is why dropping it is safe"), L4 ("no output-affecting env knobs exist… CI enforces
   this"), L5 (one producer, written once), L9 ("no model loads inside the DP process"),
   L11 (hole vs. empty claim vs. never-attempted). **Nothing in §Condensed history is the
   sole home of a live rule.** One transplant recommended: the L11 provenance corollary's
   "one witness on two channels" phrasing already sits in L11 — confirmed; delete the
   section without residue.

9. **LEARN_LOOP §8 preamble — the doc-truth pattern.**
   Quote: "Every serious defect in the D18 slice — a day-log stamping a recipe whose knobs
   it never used, a default path that silently trained on nothing, a rollback that had
   quietly stopped working — started exactly like the items below: a document disagreeing
   with the code, or with another document. None was caught by a test. Two harnesses were
   green while asserting a defect as correct behaviour."
   Replacement (proposed home: ORG.md §Keeping documents true): "A document that disagrees
   with the code, or with another document, is the leading indicator of a real defect —
   the class no test catches, because a harness can be green while asserting the defect as
   correct behaviour. Doc-truth passes are defect hunts, not housekeeping."

10. **HANDOFF E-2 card — why deletion is not correctness.**
    Quote: "The WS-VC double-count was fixed by the day-log materialization rule, not by a
    delete — at D18 that rule read latest `ingest_time` wins per `(chunk_id, content.kind,
    discriminator)`…"
    Replacement: "Correctness at read time is the renderer's dedup rule (latest
    `updated_at` per chunk); deletion is retention and right-to-be-forgotten, never the
    mechanism for correctness. If deleting records makes training come out right, the bug
    is upstream." (The storage charter already states the general law; the card cites it.)

11. **ARCHITECTURE C10 card — the dialect key.**
    Quote: "`pipeline_version` cannot be the dialect key. It is a *composed* string… (This
    was the v0/v1 reasoning; C10 v2 dedups on `(chunk_id)` alone…)"
    Replacement: "Dedup keys must be orderable by the store's own clock: a composed version
    string has no order, `updated_at` does. One record per chunk is what lets the key be
    `(chunk_id)` alone."

12. **Continuum `app/window.py` deletion comments.**
    Quote (paraphrase of `window.py:9-27`): each deleted function's comment records why the
    deletion was load-bearing (local-date arithmetic re-derived prior windows under
    *tonight's* timezone).
    Replacement: one comment on the surviving `Window` value object: "Windows are storage's
    facts: enumerated and fetched, never recomputed — recomputing under a current-timezone
    lens would re-derive different bounds for the same past window."

13. **contracts/README — the `pattern` trap and drift lesson.**
    Quote: "Writing a schema no service validates against is how prose and schema drift
    apart, which is the failure D17 hit (a claim hiding in a schema `description`…)."
    Replacement: "A schema no service validates against will drift from prose unnoticed —
    every schema here must have a validating consumer, and claims belong in validated
    structure, not in `description` prose." (The unanchored-`pattern`/`fullmatch` trap note
    is already timeless; keep verbatim.)

14. **DP HANDOFF §Gotchas — the inline path.**
    Quote: "Do not delete it 'because async exists' — proposed and refused on that ground."
    Replacement: "The inline path is C8's skeleton and is byte-identical to async for one
    chunk; deleting it would orphan the synchronous contract." (Drops the session anecdote,
    keeps the refusal's reason.)

15. **Recording charter §Parked — gap-report reconciliation.**
    Current-world reasoning (wipe asymmetry, fleet skew, safe-by-construction excuse) —
    **no rescue needed**; it earns its place as standing rationale. Listed so Phase 2 does
    not mistake it for a war story: it is a live design defence, not history.

---

## 5. Numbering holes and the pointer line

**The pointer line, exact wording, two placements:**

> History before 2026-08-08 lives in git history, not in this tree.

- `product/DECISIONS.md` — appended as its own line at the end of the header blockquote
  (after "…a founders' session ratifies and numbers it.").
- `product/README.md` — as a one-line note under the **Map** section (recommended spot:
  directly after the tree block). If the founders want it at repo root too, the top-level
  `README.md` takes the same line verbatim; not assumed here.

**The numbering-hole note (proposed text, lives beside the pointer line in DECISIONS.md and
is mirrored in any register that loses rows):**

> Registers in this tree may skip numbers. A hole is not an error: retired rows leave the
> working tree under the history policy above, and **numbers are never reused**. The same
> holds for service-local registers (R-n, C-n), escalation numbers (E-n), and charter OQ
> numbers — storage's OQ register already skips 5 by construction, and its preamble's rule
> ("OQ numbers are stable identifiers and are never renumbered") is the register-wide law.

Holes this classification would create if every proposal executes: DECISIONS none (D10/D15
become stubs) or {10, 15} (if removed); continuum C-register {4, 6, 7, 11}; storage OQ {7}
(plus the pre-existing 5); recording loses no numbered rows; DP OQ loses none (14(a) is
rewritten, not removed); root E-register retires E-3 (resolved) leaving {1, 2, 4, 5, 6}.

---

## 6. Phase-2 execution guardrails (proposed, for the gate to ratify)

1. **Order:** (1) why-rescue transplants (§4) → (2) register card rewrites (§3) → (3) doc
   deletions (§2, leaf documents before the documents that cite them is *reversed* — edit
   citers first, delete last, so no intermediate commit contains a dangling pointer) →
   (4) code-comment sweep (§2.6) → (5) pointer line + numbering note.
2. **Safety rails:** never touch `.git`; no history rewrite; no force-push; the parity
   apparatus (§0.2/§2.7) and `prompts/LOCK.json` are exempt from token sweeps; no edit may
   change any byte that feeds `pipeline_version`, a record, a golden, or a locked prompt —
   all four suites green after every commit (DP count drops only by the co-deleted
   `test_cutover_wipe.py`).
3. **Done means grep-clean, with a defined exemption list:** the §1.1 tokens return zero
   hits outside (a) the parity apparatus, (b) negative-assertion tests and resurrection
   guards (reworded self-contained), (c) `LOCK.json`, (d) this file's own history —
   `docs/purge_classification.md` itself should be deleted in the same Phase-2 close-out
   once executed, for the same reason everything else leaves: the tree teaches the present.
4. **Each DELETE lands as its own commit** with the inertness note from §2.10 in the
   message, so the archive is self-explaining at every step.
