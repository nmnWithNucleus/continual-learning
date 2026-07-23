# WS1 — Nightly-loop scaffold (mock cycle, headless green)

**Status:** done (this session, 2026-07-22) · **Branch:** `svc/continuum-scaffold`

The M0 skeleton: the full nightly consolidation cycle runs end-to-end on a synthetic
day with `TRAINER_BACKEND=mock` — no GPU, no storage service, no research code —
exercising every orchestration path the real backend will plug into. **46 tests green**
after an adversarial review round (31-agent find→verify workflow): 26 confirmed findings,
all fixed same-session — headline fixes: the gate/publish tail is now terminal-guarded
(re-runs replay the recorded outcome with zero side effects — no double strikes, no
duplicate C5 rows, no alias regression), strike/freeze accounting is window-monotonic,
replay-mix stage keys hash reservoir CONTENT, rollback is re-entrant stack-replay down to
base, prune is recency-ordered and can't delete rollback targets, `user_id`/`window_id`
are validated before any path use, all durable writes are atomic+fsync with torn-tolerant
readers (`app/fsio.py`), block anchors render in the wearer's local clock, and blocks
break on camera-off gaps. Regression suite: `tests/test_review_regressions.py`.

## What was built

| Module | Owns |
|---|---|
| `app/config.py` | env-per-call settings (DP posture); `TRAINER_BACKEND` mock/engram switch, loud fallback |
| `app/recipe.py` + `recipes/consolidation-v1.0.json` | the recipe as CONFIG — engram consolidation v1.0 knobs pinned (48×/neg .15/replay .30/r128 α256/lr 1e-4/3ep/1024-tok + gate thresholds); every change forks `recipe_id` |
| `app/window.py` | consolidation windows: 04:00→04:00 user-local, half-open, `window_id` keyed on local start date; `closed_window_before()` for the nightly trigger |
| `app/context_reader.py` | C10 client on the beta range-read shape (`GET /context/records?user_id=&from=&to=`); fails loudly, never trains on a truncated window |
| `app/daylog.py` | C2 records → ~10 s segment rows (TIME-WINDOW join — audio chunks are 5–30 s VAD-carved, video captions per-keyframe; diarized sub-spans land in their own buckets) → ~2 min blocks; engram field names (`seg_id/caption/asr/ocr/quality`, `block_id/seg_ids/text/anchors`) so the ported code's I/O is already the shape |
| `app/renderer.py` | materializes `segments.jsonl`/`blocks.jsonl`/`day.txt` at the trainer seam (canonical rows live upstream; files are a boundary artifact) |
| `app/backends/` | the seam: `amplify/train/evaluate` protocol; `mock` (deterministic, recipe-shaped output incl. deny-then-correct negatives + ok-rate stat); `engram` fails loudly with a pointer until ws-morpheus-port lands |
| `app/reservoir.py` | permanent per-user store of amplified corpora; uniform pooled-paragraph replay sampler with `before_window` guard; negatives tagged at admission (for the neg-boost knob later) |
| `app/gate.py` | service-owned verdict; 3 of the 6 research checks wired (new-day floor, traps floor, heldout ceiling) + probe-count floor; unwired checks visibly listed in every report |
| `app/publish.py` | C5-shaped `entries.jsonl` + atomic `active.json` alias + `rollback()` + 14-snapshot retention (never prunes active); `active_before(window)` gives the correct resume adapter for re-runs |
| `app/cycle.py` | the orchestrator: daylog → amplify → replay-mix → train (continue the ONE life adapter) → gate → publish/record → reservoir admission; every stage journaled + keyed by content hash (idempotent re-runs, crash-safe); strike/freeze/debt state per user |
| `app/nightly.py` | CLI: `python -m app.nightly --user u1 --tz America/Los_Angeles [--synthetic]` |
| `app/synth.py` | synthetic C2 day generator (mixed modalities + out-of-window stragglers) |

Run everything: `./run.sh` (venv bootstrap → pytest → one synthetic night).

## Design decisions recorded

- **Idempotency = content-hash stage keys** (the research pipeline's `(day, stage,
  content-hash)` discipline, in-process): changed upstream input invalidates exactly the
  stages below it; a re-run of an unchanged night skips to the same adapter version.
  Caught live by the tests: resume-adapter selection had to be `active_before(window)`,
  not the live alias, or a re-run resumes from its own output.
- **Quality gate placement:** the day log keeps everything; `quality < 0.5` rows are
  excluded from *amplification* only. Unscored (`None`) passes — C2 v0 has no quality
  field yet (DP flag).
- **Gate-fail policy:** candidate recorded (`status=gate_failed`, audit trail), prior
  adapter keeps serving, strike counted, window added to debt; 2 consecutive strikes
  freeze the user (`--force` or state-file clear to resume). Failed-day *merge* into the
  next night's corpus is ws-morpheus-port scope (debt is already tracked).
- **First night has no replay** (empty reservoir) — matches the research recipe; the
  sequential-collapse risk begins night 2, which is exactly when replay kicks in.
- **`skipped_no_data`** (empty window) is not a strike — the charter M4 min-data rule's
  trivial case; a char-floor threshold is deferred to M4 proper.

## Known gaps (deliberate, tracked)

- Replay mixing **appends** (corpus grows); the research displaces under matched
  compute (`train_on` max-chunks budget). The budget cap ports with the trainer —
  it's a *training*-time constraint, not a mixing-time one. Noted so nobody reads
  the mock corpus sizes as recipe-faithful.
- No late-data watermark: the design of record specifies none; v0 runs shortly after
  the boundary and idempotent re-runs make catch-up safe. A real policy lands with
  C10's freeze (watermark semantics are charter OQ9).
- `/metrics` + dashboard (D9) not started.
- Fleet scheduling (M4) not started; `nightly.py` is the unit a per-user cron loops.
