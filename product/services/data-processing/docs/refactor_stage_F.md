# DP Rebuild — Stage F worklog (Cutover)

**Stage:** F — Cutover · **Status:** IN_PROGRESS · *Dated:* 2026-08-07
**Branch:** dp-rebuild-v1 — merges to main at WP-F1; the merge is part of this stage · **Plan:** [refactor_dp_service.md](refactor_dp_service.md) §8 Stage F
**Laws this stage:** OD-1 (the beside-build lands in one deploy) · OD-2 fresh-forward wipe under the D19 license, `/raw` sacred · OD-3 already paid (all four servers behind the seam since Stage B) · D28 stamp bump ("2" + consolidation-v2.0) · D20 block-text contract · D16 redrive (the standing async-default gate, paid at drill 1) · D27 `updated_at` window axis
**Scope:** WP-F0a vLLM + identity probe + caption first-contact · WP-F0b continuum stamp teaching (the one licensed continuum change) · WP-F0c cutover kit (wipe script, un-repoint, rollback runbook, merge preflight) · GATE 1 · WP-F1 cutover · WP-F2 five drills · WP-F3 soak setup

This stage is different in kind: two hard gates where work stops for an explicit founder
message in this session. Only a message containing "CUTOVER APPROVED" unlocks WP-F1; the
wipe additionally requires "WIPE APPROVED"; both get quoted verbatim in this worklog as
the authority for what follows them. Rollback here means restoring services, not
reverting commits.

Carried-over instructions honoured this stage (from Stage A–E "Noticed for later
stages" — the accumulated F-carries are binding): the bumped stamp values are picked and
taught before cutover ("2", consolidation-v2.0 — Stage A asked, Stage E made concrete;
WP-F0b); the wipe covers `/context` + DP journal only, `/raw` and `/sessions` stay
(Stage A); the deploy story wires the supervisor fleet + explicit HF_TOKEN passthrough
and the `:8097` stray finally gets its ruled disposition executed at F1 (Stage B/C);
clipcap's `VLM_URL` must point at an endpoint actually serving the pinned model and the
startup `/v1/models` probe suggestion is honoured as a built, tested boot gate
(Stage C/E; WP-F0a); circuit.py's wire-or-retire lands with the deploy story (Stage C/D
— ruled below); the heal drill composes filtered manifests, e2e file ordering stays
(Stage D); recording's 501-retry taxonomy is verified before cutover (Stage E — finding
recorded in WP-F0c); the repoint inventory is re-verified live and the un-repoint
retires the five `STORAGE_*` pins + the worktree `.venv` symlink alongside
`SERVICES_ROOT`/`DP_VAR_DIR` (Stage E; WP-F0c); the recipes/policies live↔branch
coupling stays additive until it dissolves at F1 (Stage E); acoustic stays unrouted in
C10 v2 — no renderer patch smuggled in (Stage E); the OD-2 wipe meets the D27 migration
ladder cleanly — rows are cleared, the DB file and its shape stay (Stage E); the
dry-run-SQL-variable bound and the `/context` ack-echo notes ride to Platform M2, named
here so the carry survives the stage (Stage E).

---

## Pre-flight notes (2026-08-07)

- GPU inventory: 8× NVIDIA H100 80GB HBM3, all eight idle — 0 MiB used, zero compute
  processes (`nvidia-smi` queried at stage open). GPUs 0-1 are free for vLLM; there are
  no foreign eval jobs to protect this session.
- A git remote exists (`origin git@github.com:nmnWithNucleus/continual-learning.git`),
  so per the brief any push is gated with the cutover approval; nothing is pushed
  before GATE 1's founder message.
- Live processes re-verified against Stage E's repoint inventory — exact match:
  `:8083` storage (pid 330817, code from `/home/ubuntu/nmn/dp-v0-live`, interpreter
  this tree's `storage/.venv` via the worktree symlink), `:8085` DP v0 (pid 3835816,
  worktree code, `.venv-learn`), `:8084` recording (pid 3356458, this tree's code,
  `.venv-learn`), `:8097` OCR-sidecar orphan (pid 1595671, cwd shows a deleted
  worktree), `:8099` Jul-24 storage smoke (pid 803820, scratch DB).
- The two stray onboarding files (`onboarding/field-guide.html`,
  `onboarding/review_actions.md`) stay uncommitted through everything, as every stage
  before this one held.

## WP-F0a — vLLM up, the identity probe built, caption first-contact

GPU inventory first, per the brief: all eight H100s idle, GPUs 0-1 free, no foreign
eval jobs (pre-flight above). vLLM serves on **:8161** — the next server decade after
ocr's 815x, loopback only, deliberately distinct from the serve loop's user-facing
:8000 (that split is tracked escalation E-3(b); this stage implements the split, the
founder's gate approval rules it — board rows annotate at F1).

**Model + weights provenance (the pins):** `Qwen/Qwen3-VL-32B-Instruct` at revision
`0cfaf48183f594c314753d30a4c4974bc75f3ccb` — the exact snapshot already resident in
this machine's HF hub cache (`~/.cache/huggingface/hub/models--Qwen--Qwen3-VL-32B-Instruct`),
downloaded from huggingface.co 2026-07-01→02, 63 GB, 14/14 safetensors shards present,
`refs/main` pointing at that same commit. Serving stack: `vllm==0.11.2` (the version
already proven on this box) in a dedicated `platform/deploy/.venv-vllm` (Python 3.12),
tensor-parallel 2 over GPUs 0-1, `--max-model-len 32768`, `--served-model-name` pinned
to the clipcap string. Both pins are CONSTANTS in the launcher, not env knobs — same
law as clipcap's model pin and the fleet manifest revisions.

| File | Action | Why |
|---|---|---|
| `platform/deploy/run_vllm.sh` | NEW | launcher on the run_learn.sh precedent: up/stop/status/restart, pidfile + log, adopt-if-healthy; "healthy" REQUIRES /v1/models to list the pinned model, so a wrong-weights server on the right port reads as down |
| `platform/deploy/.gitignore` | edited | `.venv-vllm/` + `run-vllm/` runtime artifacts excluded, beside their learn-loop twins |
| `dp/app/main.py` | edited (TDD) | `_assert_vlm_identity()` — boot probe GETs `{VLM_URL}/v1/models` and refuses to serve unless clipcap's pinned model is listed; wired first in the lifespan under the `DP_SUPERVISOR` opt-in (the deploy shape), before the fleet starts; uses clipcap's own env reads + the patchable `vlm.make_async_client` factory so probe and stage can never disagree about the endpoint |
| `dp/tests/test_vlm_boot_probe.py` | NEW (TDD) | wrong endpoint refuses boot naming found-vs-pinned; right endpoint boots; unit-shaped construction (no opt-in) never touches the VLM factory |
| `product/STACK.md` §ports | edited | learn-loop port additions recorded: DP servers 8121–8152, captioner vLLM 8161 |
| `platform/deploy/README-learn.md` | edited | port table + the E-3(b) rationale line |

**Evidence (2026-08-07):** probe TDD red watched first
(`test_boot_refuses_when_v1_models_lacks_the_pinned_model` → `DID NOT RAISE` against
shipped code), green after; DP suite **576 passed, 4 skipped** (Stage E exit was
573+4; +3 are the probe tests), env-allowlist test untouched-green (the probe reads
only already-allowlisted names). vLLM boot: launcher reported
`vLLM healthy (pid 470702): /v1/models lists Qwen/Qwen3-VL-32B-Instruct`;
`GET :8161/v1/models` → `{"id": "Qwen/Qwen3-VL-32B-Instruct", … "max_model_len": 32768}`;
~70 GiB used on each of GPUs 0-1.

**Caption first-contact (the clip dialect meets a real VLM):** one real clipcap call
through the live endpoint against the committed fixture
(`tests/fixtures/video_scenes.mp4` under its committed C1 template), run through the
actual graph — `POST /ingest`, real ffmpeg clipprep, real ocr server pair on
8151/8152 via a filtered manifest (the Stage D drill idiom), FakeStorage as the only
stand-in. Output, pasted verbatim:

```
[smoke] ocr replicas healthy on [8151, 8152]
[smoke] POST /ingest -> 200 in 34.3s
[smoke] response: {"ok":true,"record_ids":["6cc0b6f5687669ca207d96c03711abb5e9c706370e4abef5f0d22a5fd80d6fde"]}
[smoke] pipeline_version: clipcap.v1-vlm.v1+clipprep.v1-ffmpeg.v1+screentext.v1-ppocr.v1
[smoke] slots present: ['caption', 'ocr']
[smoke] CAPTION SLOT, verbatim:
{
  "version": "clipcap.v1-vlm.v1",
  "value": "an unidentified presentation application — switching between slides."
}
[smoke] ocr fleet torn down; ports free: True
```

The fixture is synthetic slide-like scenes, so "an unidentified presentation
application — switching between slides" is a sane first caption: the dialect's
scenario framing (`screen-mac`), the D-11/D-12 caps and the parse ladder all held on
a real reply. `VLM_URL=http://127.0.0.1:8161` is staged for learn.env in WP-F0c's
un-repoint diff; nothing live reads it yet.

## WP-F0b — continuum stamp teaching (the one licensed continuum change)

The v2 stamps are exactly the pair Stage E named: `daylog_format_version "2"` and
`recipe_id "consolidation-v2.0"`, riding a C10 body whose contract `version` is now
"2". Acceptance evidence is the WP-E4 parity re-baseline — the D20/M9 differential fed
each side its own shape of the same content (v0 originals to continuum's untouched
local reference, 24 hand-built v1 equivalents to the slot-walk renderer), all 31 checks
green over both origins, committed as `storage/scripts/daylog_parity_diff.out.txt`.
That is the D20 joint framing: the block text is the contract, the differential is the
proof, and widening the dialect tuple without a re-run of that proof is exactly what
the tuple's own comment forbids. The re-run happened at Stage E; this WP consumes it.

| File | Action | Why |
|---|---|---|
| `continuum/app/clients/daylog_client.py` | edited | `SUPPORTED_DAYLOG_FORMAT_VERSIONS` ("1",) → ("2",) with a dated comment citing the parity report; contract gate accepts version "2", refuses "1"; the three seam messages that named v1 as current now say v2 |
| `continuum/app/config.py` | edited (1 line) | `CONTINUUM_RECIPE_ID` default → consolidation-v2.0 |
| `continuum/recipes/consolidation-v2.0.json` | NEW (copy) | deliberate byte-identical copy of storage's recipe — recipes are immutable under their id, verified identical with `diff` at copy time |
| `continuum/tests/test_http_clients.py` | edited + 2 NEW (TDD) | `_daylog_body` helper now serves what tip-storage serves (v2 stamps); the stranger dialect in the unknown-format test moves "2" → "3" so the net still catches strangers; tuple pin test flips to ("2",); two new tests prove v1 is now refused on each axis |
| `continuum/scripts/seam_check.py` | edited | STEP 6's body check reads "C10 v2"; the 7b shipped-defaults blocker accepts any rawlog-source recipe (v1.1 or v2.0) via a new `RECIPE_V20` constant — 7b/7c still drive v1.0/v1.1 explicitly, those proofs are properties of the recipes, not of the default |

Mechanism notes, read before editing:

- The refusal-test flip is two-sided by design ("the net must still catch strangers"):
  `test_a_v1_stamped_body_is_refused_at_the_contract_gate` proves a full v1 body
  (version "1" + format "1") dies at the contract gate;
  `test_a_v1_format_stamp_alone_is_refused_by_the_dialect_net` proves format "1" alone
  (on a v2-stamped body) dies at the tuple — the chimera a rolled-back renderer behind
  a current API version would produce. The unknown-dialect test now sends "3", so
  acceptance of "2" never widened into acceptance of everything.
- "1" leaves the tuple in the same edit, deliberately: the wipe is fresh-forward
  (OD-2), so no v1 day-log can legitimately exist post-cutover — a v1 body after F1 is
  a stale or rolled-back storage, and refusing it is the net working. The tuple's
  two-dialect transition capacity stays unused because nothing will ever fetch v1 again.
- C10 v2 schema acceptance needs no continuum-side parse change and that is a verified
  fact, not an omission: continuum does no jsonschema validation (storage
  contract-checks its own output before serving); the segment/block item shapes are
  byte-identical between `c10_daylog.v1.json` and `c10_daylog.v2.json` (v2 moves the
  window axis and the dedup key — renderer-side facts), so the dataclass construction
  in `_to_daylog` reads both identically. The stamps were the whole boundary.
- Recipe registry: continuum's default read path is HTTP (storage serves its copy
  verbatim), but the local registry must resolve the same id for local-mode runs and
  the id-echo check — hence the copy, `diff`-verified byte-identical at copy time.
  Policy pin (`gate-policy-v1.1`) is untouched; nothing about how a night trains
  changes (the recipe's own `source` field says the same).

**Evidence (2026-08-07):** TDD red watched first — flipping the test helper to v2
stamps before any production edit produced `14 failed, 30 passed` in
`test_http_clients.py`, the failures being exactly the v1-only gate refusing v2 bodies
(`ValueError: expected a C10 v1 day-log body, got … version='2'`) and the two new
v1-refusal tests not raising. After the production edits: `test_http_clients.py` 44
passed; full continuum suite **264 passed, 7 skipped** (Stage E baseline was 262+7;
the +2 are the new refusal proofs). `seam_check.py` compiles; its full live drill is
not re-run this WP — STEP 7c's refusal proof is recipe-axis and unaffected, and the
real acceptance proof post-cutover is WP-F2 drill 4 (continuum accepts the stamps on a
real fetch, not a test).

## WP-F0c — the cutover kit (nothing executed)

| File | Action | Why |
|---|---|---|
| `platform/deploy/cutover_wipe.py` | NEW | the OD-2 wipe with the mandatory dry-run as default mode; full KEEP/WIPE classification in code; structural refusals (below) |
| `platform/deploy/learn.env.stage-f-unrepoint` | NEW | the staged un-repoint, applied at F1 by copy-over (learn.env itself is gitignored); every change annotated in the file |
| `platform/deploy/ROLLBACK-stage-F.md` | NEW | the rollback runbook — restore services, not commits; every step named with its verification, nothing assumed |

**The wipe script's safety story is structural, not prose:** every table in every DB
must be classified KEEP or WIPE in code and every continuum `var/` subdir likewise —
anything unknown aborts BOTH modes before a row is touched; the wet path can only run
`DELETE FROM <allowlisted table>` (plus the one `dp_state` UPDATE under
`--recording-claims reset`) — DDL, DROP and every KEEP table are unreachable; dry-run
opens the DBs read-only (sqlite `mode=ro`); `--execute` refuses while `:8083`/`:8085`
still answer (the freeze comes first); wet prints the same manifest code path before
and after acting, so "wet matches dry" is checkable line by line. The classification:
WIPE = `context_records`, `day_logs`, `training_windows`, reservoir files, DP journal
(`pending`+`processed`), continuum learn-state subdirs (journal/state/cycles/adapters/
model_directory/reservoir — all verified absent today). KEEP = `turns` (/sessions),
`raw_blobs` + the raw_store tree, `user_profiles` (C12: declared facts, not derived
data), `model_directory` (the C6 base seed), recording's entire capture ledger +
spool, continuum's research dirs (diag/parity/phase3/slurm). `training_windows` is
wiped deliberately: the D18 watermark is derived from published rows over windows
that stop existing — fresh-forward resets both or the first post-cutover night would
chase a watermark into a wiped corpus.

**Evidence:** scratch-world test (throwaway sqlite files mimicking every real ledger
shape, in the session scratchpad — an evidence script like the caption smoke):
25/25 checks — dry-run mutates nothing; wet empties exactly the WIPE set with every
KEEP row/byte intact (including raw_store bytes compared verbatim); `keep` preserves
all `dp_state`, `reset` NULLs only `processed` and preserves `accepted` (the buffered
redrive the cutover relies on); an unclassified table aborts both modes with nothing
touched; an unclassified continuum subdir aborts; wet refuses while a freeze port
listens. The LIVE dry-run manifest (read-only, services untouched) is pasted in the
GATE 1 report: 4 `context_records` + 4 DP `processed` rows to wipe, `day_logs`/
`training_windows`/reservoir/continuum-state all 0, recording ledger fully empty
today — so the dp_state disposition question is about chunks captured between now
and the freeze, not about existing rows.

**Recording's 501-retry carry, verified (the Stage E carry asked; the answer is
"no"):** the taxonomy does NOT treat 501 as non-retryable — `app/clients.py:64` sends
everything ≥500 down the transient branch (bounded: 4 attempts with backoff, then the
segment is marked `failed` with its spool file kept; `failed` is never auto-re-enqueued,
so "loop forever" does not happen — the real cost is burnt retries and a manual
`/retry` for the segment). Exposure today is dormant: recording demuxes only
audio/video tracks, and both modalities have registered v1 pipelines, so no 501 can
arise from the capture path. Disposition offered at GATE 1: accept as-is for the
cutover (dormant + bounded), with the one-line taxonomy fix (501 → permanent, beside
the 4xx branch, TDD) available as a licensed change if the founder prefers it closed
before F1.

**Un-repoint, staged (applied at F1 only):** `SERVICES_ROOT`/`DP_VAR_DIR` return to
this tree; the five `STORAGE_*` pins retire (with one tree, run.sh's own defaults
resolve to the same live data paths — the repoint moved code, never data);
`ASR_BACKEND`/`ASR_LANGUAGE` retire (v0 knobs the rebuilt DP does not read);
`DP_SUPERVISOR=1` and `VLM_URL=http://127.0.0.1:8161` are added. HF_TOKEN passthrough
(Stage B carry): resolved by fact, not by env — the fleet reads hub auth from
`~/.cache/huggingface/token` (verified present); no secret enters the committed
staging file. The recipes/policies live↔branch coupling dissolves with this apply +
the worktree retirement (`git worktree remove /home/ubuntu/nmn/dp-v0-live` after the
old fleet stops; the storage `.venv` symlink dies with it), closing the Stage E
checklist item. Circuit.py's wire-or-retire (Stage C/D carry): NOT wired into the
deploy — the Stage D reasoning stands at cutover (heal-on-redrive removed the
correctness need; the one honest use is a fleet-cost optimization) — retire lands
with Stage G demolition unless the founder rules otherwise at the gate.

**Merge preflight (2026-08-07):** `main` = merge-base = `9307b7e` — the branch is 41
commits strictly ahead, zero divergence, so the planned `git merge --no-ff
dp-rebuild-v1` is conflict-free by construction (strategy: no-ff, per the brief; the
push, if any, is gated with the cutover approval). Cross-suite at the branch tip:
DP **576 passed, 4 skipped** · servers/common **30 passed** · storage **354 passed**
· continuum (post-teaching) **264 passed, 7 skipped**. The two onboarding strays
remain uncommitted.

## WP-F1 — the cutover (executing past GATE 1)

**The authority for everything below, quoted verbatim (founder, in-session,
2026-08-07):**

> CUTOVER APPROVED at 445e1ce. WIPE APPROVED — dp_state: keep. 501: accept as-is.
> E-3(b) :8161 split: ratified.

Rulings consumed: the wipe runs with `--recording-claims keep`; recording's
501-retry taxonomy ships as-is (bounded transient class, dormant); the :8161
captioner split is ratified — the three HANDOFF E-3(b) rows annotate in this WP.

**Executed order, one refinement over the staged plan (documented, not silent):**
the v0 DP journal — an approved wipe target — lives INSIDE the dp-v0-live worktree
(`var/dp.db`), so the wipe must run before the worktree retires or its target
vanishes and wet could no longer match the approved dry-run. The finding-4
constraint (worktree retires before `git checkout main`) is unaffected. Final
order: freeze (:8085, :8083, the :8099 old-world smoke; recording keeps capturing)
→ approved wipe (keep) → retire worktree → checkout main → merge --no-ff →
un-repoint → relaunch from merged main → kill :8097 (the authorized disposition)
→ resume/redrive verification → /health shapes. Log below, pids + UTC timestamps.

**Freeze (executed):** pid identities re-verified against the pre-flight inventory
immediately before each kill; recording checked 200 before, during and after.

```
freeze begin: 2026-08-07T05:49:43Z
:8085 freed 2026-08-07T05:49:46Z (dp v0 pid 3835816 stopped)
:8083 freed 2026-08-07T05:49:49Z (storage v0 pid 330817 stopped)
:8099 freed 2026-08-07T05:49:51Z (Jul-24 smoke pid 803820 stopped)
recording during freeze: 200
```

The :8099 stop rides F1's "anything else running from the old world" — a Jul-24
storage smoke on a scratch DB, holding pre-Stage-E code in memory. `:8097` waits
for its own authorized step after relaunch.

**The approved wipe (executed `2026-08-07T05:50:13Z`,
`--execute --recording-claims keep`):** wet output matches the approved dry-run
LINE FOR LINE on the pre-action manifest (same counts, same verdicts), then:

```
[wet] storage dev.db: DELETE FROM context_records -> 4 row(s)
[wet] storage dev.db: DELETE FROM day_logs -> 0 row(s)
[wet] storage dev.db: DELETE FROM training_windows -> 0 row(s)
[wet] DP journal (v0): DELETE FROM pending -> 0 row(s)
[wet] DP journal (v0): DELETE FROM processed -> 4 row(s)
[wet] recording ledger: claims kept (documented reconciliation noise)
[wet] storage reservoir: 0 file(s) removed
```

Post-wipe verification (same manifest code path): every WIPE target 0; every KEEP
count unchanged — model_directory 1, raw_blobs 4, turns 0, user_profiles 0,
recording ledger all 0, continuum research dirs 236/36/90/93. `/raw` bytes
untouched by construction (never opened for write). Full transcript retained in
the session log.

**Worktree retired `05:51:01Z`** — `git worktree remove` clean (no force), pruned,
directory gone; the storage `.venv` symlink and the wiped v0 journal husk died
with it, dissolving the recipes/policies live↔branch coupling and the symlink
carry in one act. **Merged `05:51:22Z`:** `git checkout main` (accepted — the
worktree no longer held it) then `git merge --no-ff dp-rebuild-v1` → merge commit
**`bf1e806`**, zero conflicts (merge-base was main's tip), the two onboarding
strays riding through uncommitted exactly as designed.

**Un-repoint applied `05:51:39Z`:** `learn.env` backed up to
`learn.env.pre-stage-f.bak`, staged file copied over; active lines verified to be
exactly five — `RECORDING_HTTP_TIMEOUT=120`, tree-local `SERVICES_ROOT` +
`DP_VAR_DIR`, `DP_SUPERVISOR=1`, `VLM_URL=http://127.0.0.1:8161`.

**Relaunch from merged main `05:51:58Z` → checklist printed `05:52:30Z`:** storage
:8083 and DP :8085 started fresh under `.venv-learn`; recording :8084 ADOPTED
healthy (never restarted — its buffer semantics were the whole reason). DP booted
under `DP_SUPERVISOR=1`, which means the VLM identity probe ran and passed before
serving. All eight model-server ports listening with 200s by **`05:53:52Z`** —
under two minutes from merge to full fleet. GPU map as designed: vLLM ~70 GiB on
0-1, pyannote on 2-3, whisper on 4-5, ast on 6-7, ocr on CPU.

**The `:8097` orphan, executed `05:53:48Z`** (authorized here and only here): pid
1595671, `python3 app.py` from a deleted worktree — TERM, port freed. With the
freeze's `:8099` stop, the stray-process ledger from Stage B/E is finally clean.

**Resume + redrive verification:** ingest resumed by construction (recording's
`DP_URL` is :8085, now the v1 service). The capture ledger held ZERO buffered
chunks at cutover (captures/segments/chunks all 0 — re-verified read-only
post-relaunch), so the drain had nothing to drain: stated plainly rather than
claimed. The one spool file
(`spool/01KXW45FYZEN411SK5MNAJ26DC/0.f490bdd40bf6.mp4`) is the known Aug-3
orphan with no ledger row — untouched (KEEP), owner decision unchanged. The
redrive path gets its real exercise at drill 1.

**`/health`, the new world's shapes (pasted):** storage `{"ok":true}` · recording
`{"ok":true}` · DP —

```
{"ok": true, "ingest_mode": "inline",
 "pipeline_versions": {
   "audio": "acoustic.v1-ast.v1+asr.v1-fw.v1+diarize.v1-pyannote.v1+speaker_align.v1-builtin.v1",
   "video": "clipcap.v1-vlm.v1+clipprep.v1-ffmpeg.v1+screentext.v1-ppocr.v1"},
 "supervisor": true}
```

`ingest_mode: inline` is CORRECT at this instant: D16 pins `INGEST_ASYNC` off by
default until the redrive drill pays the async-default gate — that is drill 1's
whole point; the flip happens there, not here. **E-3(b) board rows annotated
resolved** (product/HANDOFF.md ×3 sites, platform and DP boards) per the gate
ratification; the OQ3 charter edit rides Stage G's paper sweep.

## WP-F2 — the drills (live stack; any failure stops the line)

### Drill 1 — D16 redrive (the standing async-default gate, paid)

`INGEST_ASYNC=1` added to learn.env (dated comment); DP restarted `05:56:31Z`;
`/health` flipped to `ingest_mode: async`; fleet respawned 8/8 by `05:57:15Z`.
First capture attempt used `POST /capture/run` — the M0 smoke door — and its 3
chunks processed green (3×202, journal + storage confirmed) but wrote NO ledger
rows: that door bypasses the durable capture ledger by design. Recorded as a
lesson, not a failure: the drill re-ran through the durable door
(`POST /capture/segments`), which is the path a real device drives.

Durable capture `f2-d16` (user `u-f2-drill`): one 60 s + eight 10 s WAV segments
→ 9 chunks, each 202-accepted by the async DP. **Honest disclosure on the crash
shape:** the plan was SIGKILL mid-processing (in-flight loss); the worker queue
outran the operator — at the moment all 9 were accepted the journal already
showed every record processed. The kill (`SIGKILL pid 536333, 06:10:36Z`)
therefore sprang the OTHER crash window: records durable, receipts unpropagated,
recording holding 9 `accepted` rows against a fully-done journal — the exact
reconciliation `dp_acked ⇔ C2-durable` exists for; the lost-in-flight shape
remains covered by the committed kill-9 replay tests (T-5 §4). The SIGKILL
orphaned the model-server fleet (teardown never ran) — ports cleared with
`fuser -k` before relaunch, an operational note for the runbooks.

DP relaunched (pid 545235, boot redrive found 0 pendings — correct, nothing was
in flight); fleet 8/8 by `06:11:50Z`. Redrive, pasted verbatim:

```
redrive: 2026-08-07T06:12:04Z
{"ok":true,"capture_id":"f2-d16","redriven":9,"confirmed":9,"still_accepted":0}
--- ledger after redrive:        processed|9
--- DP /ingest 200s (done-dedup hits) during redrive: 9
--- updated_at diff vs pre-redrive snapshot:  IDENTICAL — no re-window, no new records
--- records per chunk:           9|9
```

Every redelivery answered the D16 done-hit shape (200 + record_ids, zero 202s);
both ledgers reconverged; the pre/post `updated_at` snapshot is byte-identical
(the §5.1 no-op-upsert held on the live wire); exactly one record per chunk.
**The gate is paid — async stays the operating mode.**

### Drill 2 — version-forward (controlled vB bump, scratch dialect, live storage)

Scratch user `u-f2-vfwd`, scratch mock dialect, LIVE storage — no fleet involvement,
no real users. Two in-process scratch DP apps sharing one journal: app A at
`asr.v1-mock.v1` landed R1 (`2a8e17b4…`); app B — the same stage with
`Backend("mock", 2)`, distinct canned text — redelivered the SAME C1 and the
journal's version-compare produced the version-forward verdict. Pasted:

```
[d2] A: POST /ingest (asr.v1-mock.v1) -> 200 record_ids=["2a8e17b4…"]
[d2] R1 in live storage: pv=asr.v1-mock.v1 updated_at=2026-08-07T06:15:30Z
[d2] B: redeliver under asr.v1-mock.v2 -> 200 record_ids=["bd3729b9…"]
[d2] BESIDE: R1 2a8e17b4eb40… pv=asr.v1-mock.v1 updated_at=UNCHANGED
[d2]         R2 bd3729b9b087… pv=asr.v1-mock.v2 (same chunk_id: True)
[d2] window open -> 409 … "window end is not strictly greater than the floor"
[d2] window open -> 200 window_id=w20260807T061534Z
[d2] day-log stamps: contract=C10 version=2 format=2 recipe=consolidation-v2.0
[d2] next-window content: v2-dialect lines=2, v1-dialect lines=0
[d2] E-2 dry-run: records=2 by_pipeline_version={mock.v1:1, mock.v2:1} day_logs=1
[d2] E-2 wet: identical counts; R2 GET -> 404; post-wet dry-run: records=0 day_logs=0
[d2] window closed (operator abandon verb) -> 200 outcome=crashed
[d2] DRILL 2 PASSED
```

Two live facts worth their ink: the first window open was REFUSED (409, "window
end is not strictly greater than the floor") because the drill records' `updated_at`
sat inside the settle lag — the D27 axis defending its own invariant on the live
wire; twenty seconds later the same call returned 200. And the next-window proof is
content-level: the rendered day-log carried the v2 dialect's lines only (2 v2 / 0
v1) while R1 stayed durable beside — beside-semantics for lineage, latest-wins for
training, exactly D27's bargain. Cleanup verified to zero; the scratch profile row
remains (C12 rows are declared facts with no delete verb — noted, harmless).

### Drill 3 — live heal (ast down, hole ships, heal on redelivery, skip)

Against the LIVE fleet, user `u-f2-heal`. Both ast replicas SIGKILLed
(`06:19:07Z`, pids 545274/545276); a real 10 s chunk shipped into the hole window
one second later through the durable door. Pasted:

```
journal done-row: {"acoustic":"failed","asr":"ok","diarize":"ok","speaker_align":"ok"}
record 30ca082e… slots: ['asr','diarization','transcript']        <- honest hole
ast respawned by supervisor, both replicas healthy: 06:19:35Z     <- ~28 s, no operator hand
redrive -> {"redriven":1,"confirmed":0,"still_accepted":1}        <- the HEAL claim (202), not a done-hit
healed 06:19:48Z: {"acoustic":"ok",…} same record_id, updated_at 06:19:10Z -> 06:19:37Z
acoustic slot: {"version":"acoustic.v1-ast.v1","values":["chirp tone","sine wave","singing bowl"],…}
[d3] BYTE-CHECK healed vs clean-fleet twin slots: IDENTICAL
[d3] confirming redrive -> {"redriven":1,"confirmed":1,"still_accepted":0}
[d3] pure-skip re-POST of the ORIGINAL C1 -> 200 record_ids=["30ca082e…"]
[d3] ast server-call counters across the skip: UNMOVED
[d3] DRILL 3 PASSED
```

Every clause of the drill spec landed: the hole was honest (attempted, absent),
the SUPERVISOR restarted ast (the restart-on-crash story proven live, ~28 s),
the redelivery drew the heal claim not a skip, the healed record kept its
record_id and bumped `updated_at` (D27's accepted double-training), the
byte-check against a clean-fleet twin (same bytes, same C1 times, fresh
chunk_id) came back IDENTICAL, the ledger reads all-ok, and the final
redelivery was a pure skip — 200 + the healed record id with the ast
server-call counters unmoved. The twin was retracted after the comparison
(E-2, records=1). The AST's real tags for a synthetic tone WAV — "chirp tone,
sine wave, singing bowl" — are the model being honest about test audio.

### Drills 4 + 5 — the first real day-log, with a real-VLM Scene line

One flow, deliberately: the committed video fixture was captured through
recording's DURABLE door (`f2-video`, user `u-f2-drill`, `06:22:03Z`) so the
first real day-log would carry both modalities. The video chunk processed
through the live fleet — real clipprep, real ocr replicas, real Qwen3-VL on
:8161 — and its redelivery confirmed as a done-hit
(`{"redriven":1,"confirmed":1,"still_accepted":0}`). Then the window. Pasted:

```
[d4] window open -> 200 w20260807T062800Z [2026-08-07T06:04:00Z .. 2026-08-07T06:28:00Z] state=open
[d4] GET /training/daylog -> 200; stamps: contract=C10 version=2 daylog_format_version=2 recipe_id=consolidation-v2.0
c10_daylog.v2.json validation: CLEAN — 0 violations
[d4] body: 1 segments, 1 blocks, fingerprint ae7cb8a452cb53b7…
[d5] SCENE LINE, verbatim: 'Scene: an unidentified presentation application — switching between slides.'
[d4] continuum pins: SUPPORTED_DAYLOG_FORMAT_VERSIONS=('2',) recipe_id=consolidation-v2.0
[d4] CONTINUUM ACCEPTED THE STAMPS on a real fetch: DayLog(window_id='w20260807T062800Z',
     segments=1, blocks=1, fingerprint=ae7cb8a452cb53b7…)
[d4] fingerprints match storage's body — same day-log, both sides
[d4][d5] DRILLS 4+5 PASSED
```

The rendered block, in full — the first real-VLM Scene line to reach a training
corpus, with the C12 home-tz conversion doing its quiet work (06:22Z →
23:22 the prior local day):

```
On 2026-08-06, around 23:22–23:22 local time:
Scene: an unidentified presentation application — switching between slides.
World text (OCR): +2s main: SLIDE42Q3revenue · +4s main:
```

Two honest notes. The day-log has no Heard lines and one segment: the drill's
nine audio chunks are SYNTHETIC TONES — whisper correctly heard no speech, the
transcripts are empty, and the v2 renderer renders absence as absence rather
than inventing rows (the audio records are in-window on the `updated_at` axis;
they simply contribute nothing hearable). Real speech arrives with the soak's
pilot day. And the caption is byte-identical to the F0a smoke's caption — the
pack-pinned decode params holding across two independent calls. The continuum
acceptance ran under continuum's own venv through `HttpDayLogClient` over a real
socket — the same class, same gate, same pins that will fetch tonight's window;
the window is left OPEN for the soak night by design.

## WP-F3 — soak setup (the stage stays open)

The fleet is LEFT CAPTURING: recording :8084 (never restarted through any of
this), storage :8083, DP :8085 (async — the paid D16 default), the eight model
servers on their manifest ports, vLLM :8161. Nothing scheduled, per the brief.

**Soak-exit definition (plan §8 Stage F exit, recorded verbatim as the bar):**
one full pilot day captured → processed → day-log rendered → trained end-to-end
on v1. Concretely for the verdict: (1) a real pilot day's chunks land through
recording's durable door and every one reaches `dp_state=processed` with the
gap-report clean; (2) the day's records are C2 v1, schema-clean, under the
composed real pipeline_versions; (3) the day-log for the pilot's window renders
under the v2 stamps and validates against `c10_daylog.v2.json`; (4) a continuum
night consumes that window end-to-end — fetch accepted, corpus rendered,
train + gate + publish per recipe consolidation-v2.0 — and the window closes
`published` with the C5 lineage row present. The soak verdict closes this stage
tomorrow; until then **Status stays IN_PROGRESS** and Stage G does not start.

## 2026-08-07 — GATE 1 verification round (founder, 3 lenses; fixes applied)

> verification · an independent 3-lens round over the first GATE 1 post, the wipe
> script exercised WET against live copies; substance confirmed ready, the gate
> re-cut before approval. Everything above stands as written; corrections below
> amend, never rewrite.

- **The phantom hash, caught at verification (finding 1):** the first GATE 1 post
  cited the WP-F0c commit as "`06e11d4`*" — that hash exists nowhere in this
  repository (`git cat-file -t 06e11d4` → `fatal: Not a valid object name`; the
  founder's round verified against the object store, all reflogs and fsck). The real
  WP-F0c commit is **`d21a38a`**. The asterisk caveat in the post does not excuse it:
  a gate citation is a claim, and an unverified claim at a gate is exactly what
  verification rounds exist to catch. The commit message's "41 ahead" was likewise a
  stale pre-F0c measurement — true figure at `d21a38a`: **42 ahead, 0 behind**.
- **Reset-disposition text corrected everywhere (finding 2a):** WP-F0c above says
  reset means "recording re-emits those chunks on redrive, i.e. an immediate
  uncontrolled backfill" — **wrong, and the founder's verifiers proved it against
  the code**: no redrive path selects a NULL `dp_state` row (the chunk redrive
  queries `dp_state='accepted'` only, `ledger.py:495`; startup re-enqueue drives
  `'received'` SEGMENTS, `emitter.py:115-128`). Reset therefore causes **no
  backfill**; its true cost is inert metric incoherence — a processed chunk reads
  as never-emitted forever, and the `dp_acked ⇔ C2-durable` reading stops holding
  for those rows. Corrected in the script docstring, the manifest print, and the
  gate re-post; the keep recommendation stands, now for the corrected reason: reset
  buys nothing and breaks a ledger meaning.
- **Freeze covers every wet-touchable DB (finding 2b):** under `--recording-claims
  reset`, ledger.db is a wet target, so the recording port is appended to the freeze
  list IN CODE (not via the flag — the operator cannot forget it). Implemented
  conditionally, which is a stated interpretation of the founder's "add :8084":
  under `keep`, ledger.db is not a wet target and recording keeps capturing through
  the wipe, which is the F1 design — an unconditional :8084 freeze would contradict
  it. Flagged in the re-post for veto.
- **Missing wipe-target is an abort (finding 2c):** a mistyped `--*-db`/dir path
  previously printed "(missing db — nothing to do)" and exited 0 — a silent no-op
  at F1 would be a corpse in the new world. Now both modes abort on any missing
  target; `--allow-missing` is the explicit escape.
- **The evidence suite is committed (finding 2d):** `deploy/test_cutover_wipe.py` —
  it previously lived only in the session scratchpad, so the repo could not
  reproduce the evidence; and WP-F0c above claims "25/25 checks" — the true count
  of the scratchpad suite was **29** (miscounted, never verified). With this
  round's additions (missing-target aborts, the conditional recording freeze, the
  vLLM pin guard) the committed suite is **39 checks — ALL PASSED**, red watched
  first for every new behavior (8 failed against the pre-round scripts).
- **run_vllm.sh pins hardened (finding 3):** the model/revision constants were
  assigned BEFORE the learn.env sourcing, so an env-file line would have silently
  overridden them with nothing to catch it. Pins now assigned after sourcing, with
  `--status` printing them; the guard test (evil env file → pins unmoved) is two of
  the 39 checks.
- **F1 sequencing corrected in the staged plan (finding 4):** the worktree holds
  `main` checked out, so `git checkout main` in this tree would be refused before
  the worktree retires. The staged F1 order is: stop v0 ingest (:8085) → stop v0
  storage (:8083) → **retire the dp-v0-live worktree** → `git checkout main` →
  `git merge --no-ff dp-rebuild-v1` → wipe (post-freeze) → un-repoint → relaunch →
  retire symlink remnants → kill :8097 → resume ingest → redrive.
- **Small (finding 5):** ROLLBACK step 2 gained its Verify line (`git worktree
  list` + `rev-parse` + `readlink -f`); run_learn.sh's stale `ASR_BACKEND`
  default/export/banner/hint scrubbed (v1 pins its dialect in code); noted for F2:
  the caption smoke's 200-ack proved graph mechanics against FakeStorage — real
  storage acceptance of a v1 video record is first proven by drill 5's real chunk;
  noted for F1: the two onboarding strays ride the merge uncommitted by design (a
  merge does not touch uncommitted working-tree files; they stay out of every
  commit as they have all stage).
