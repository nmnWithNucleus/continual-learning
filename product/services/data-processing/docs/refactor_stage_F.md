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
