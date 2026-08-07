# DP Rebuild — Stage G worklog (Demolition & Docs)

**Stage:** G — Demolition & docs · **Status:** IN_PROGRESS · *Dated:* 2026-08-07
**Branch:** main (the rebuild is merged; `dp-rebuild-v1` is history) · **Plan:** [refactor_dp_service.md](refactor_dp_service.md) §8 Stage G, §9 file disposition, §10 condensed history
**What this stage is:** the rebuild's last act — delete the dead v0 code the beside-build
left standing, and rewrite the docs that still teach the v0 world. This is the D22 debt
(the field guide still teaches the retired law) and the reason the rebuild began: the
paper must describe the world that now runs, not the one that was replaced.

**Laws / rules honoured this stage:** every deletion carries a disposition line and the
suites stay green after each commit ([ORG.md](../../../ORG.md) no-silent-breakage);
[STYLE.md](../../../STYLE.md) governs every doc edit (§Teaching views for the field
guide); the live v1 fleet is PRODUCTION and stays 200 across every commit; push at
stage-close (founder R1).

**Founder rulings carried in from the Stage F close (quoted verbatim in
[refactor_stage_F.md](refactor_stage_F.md)):** R1 (push permitted at each stage-close),
R2 (the soak bar amended + met — Stage F is DONE), R3 (the two onboarding strays are
committed AS-IS first, preserving the reader-review session's work; this stage's rewrite
then supersedes on top).

**Green baseline before demolition (2026-08-07):** DP suite **576 passed, 4 skipped**
(the WP-F0a exit figure, unchanged). Live fleet 200 on all twelve ports (storage :8083,
recording :8084, DP :8085, vLLM :8161, the eight model servers 8121-8152).

---

## WP-G0 — R3: the onboarding strays committed as-is (before the rewrite)

Founder R3: the two files that rode uncommitted through every stage since Stage A
(`onboarding/field-guide.html`, `onboarding/review_actions.md`) are committed AS-IS
FIRST, so the reader-review round's work is preserved as its own commit in the history,
and Stage G's rewrite (WP-G4 below) then supersedes on top rather than silently
overwriting uncommitted work. These are the working-tree versions exactly as the
reader-review session (CTO modules 06-07 tranche, 2026-08-05) left them — no edit.

Committed as `afe0103` (onboarding: commit … AS-IS), a dedicated commit before any
demolition. WP-G4 rewrites the field guide on top.

## WP-G1 — code demolition

Each deletion carries a disposition line; the DP suite (`testpaths=tests`, mock
backends, in-process) stays green after each commit; the live fleet stays 200 (the OCR
replicas are never restarted — only docstrings/comments are touched, not behavior).

### Commit — `sidecars/` retired + `ocr_probe.py` retired

| Target | Disposition |
|---|---|
| `sidecars/ocr/` (whole tree: `app.py`, `run.sh`, `requirements.txt`, `README.md`, `bakeoff/`, `test_app.py`, `bakeoff/test_score.py`) | **DELETE.** The live OCR is `servers/ocr` (the framework port, Stage B WP-B5, running on 8151/8152). The standalone sidecar was the pre-port quarantine deployable; its `:8097` process was stopped at the F1 cutover. Dead tree — deleted. Its two standalone pytest files (`test_app.py`, `bakeoff/test_score.py`) are outside DP's `testpaths=tests`, so they never counted toward the 576; they go with the tree. |
| `scripts/ocr_probe.py` | **RETIRE (delete).** A WS-A probe of the standalone sidecar's *bespoke* `GET /health`+`POST /ocr` contract and its own venv — none of which `servers/ocr` speaks (it serves the framework `/health` identity + `/infer` envelope, and its identity is checked by the model-client + the DP boot probe). The probe's subject no longer exists, so it is a corpse against the live server. **Rebuild path if a latency probe is ever wanted:** target `servers/ocr` `POST /infer` with a base64 JPEG and time it, reusing the framework client — not the deleted `/ocr` shape. |
| `servers/ocr/{server.py, README.md, requirements.txt}` | **EDITED (comments/docstrings only).** The provenance references to "the retiring sidecar (`sidecars/ocr/app.py`)" reworded to "the v0 OCR service (retired at Stage G)" — no dangling path, and the trigger word cleared from live code. Zero behavior change: the running OCR replicas were not restarted and stayed 200 throughout. |

Evidence: `servers/ocr/server.py` parses and `app.main` imports; DP suite collection
unchanged at **580** (576+4); OCR replicas 8151/8152 held 200 across the edit.

### Commit — `smoke_audio_backends.py` + the offline-eval harness

| Target | Disposition |
|---|---|
| `scripts/smoke_audio_backends.py` | **DELETE.** Superseded by `servers/*/drill_stage_b.py` and the per-server golden smokes. It imports the v0 packages the rebuild deleted at Stage C (`app.asr`, `app.audio.diarize`, `app.audio.translate`, `app.audio.acoustic`) — its top-level imports fail immediately, so it has been dead since Stage C. Real audio backends are now smoked as model servers (Stage B). |
| `scripts/prompt_ab.py` | **DELETE.** The offline prompt-A/B harness, parked-and-broken since Stage C (WP-C5): `main()` refuses to run, and it is built on machinery the rebuild deleted (`VIDEO_PROMPT_DIR`/`VIDEO_CLIP_PROMPT` env forks, `cfg_tag`/`prompt_dir_fingerprint` dialects, the v0 `resolve`/per-unit `run_graph`/4-arg `build_c2` signatures, the `keyframe` legacy pipeline). A parked corpse invites rot; its 4-point rebuild checklist is preserved below and in git. The `DP_OFFLINE_EVAL` boot-guard in `app/main.py` (`_assert_not_offline_eval`) STAYS — it is a standing latch for whatever offline driver replaces this one, and `test_t1_determinism.py` keeps it in the operational-env allowlist. |
| `scripts/capture_chunkset.py` + `scripts/oracle_gemini.py` | **DELETE** (with the rebuild path named — the `build_vision_settings` breakage is not fixed, it is retired). These exist only to feed `prompt_ab`: `capture_chunkset.load_chunkset` is imported by both `prompt_ab` and `oracle_gemini`, and both call the deleted `clip.build_vision_settings()` (`capture_chunkset.py:500` ocr-truth mode; `oracle_gemini.py:156` frame prep), which the rebuild replaced with the frozen `ClipSettings` dataclass + the `CLIP_SETTINGS` code pin in `clipprep.py`. Fixing that one call would resurrect an eval harness whose driver is gone — a half-fix worse than a clear break. Deleted as a unit with `prompt_ab`. |
| `tests/fixtures/chunksets/` (`README.md` + `smoke-v1/`) | **DELETE.** The committed eval corpus `prompt_ab` scored arms over, built by `capture_chunkset.py` and read by `capture_chunkset.load_chunkset()`. No surviving test reads it (`test_eval_scorers.py` was deleted at Stage C; nothing under `tests/` imports the harness), so it is orphaned with its driver. |

**`prompt_ab.py`'s 4-point rebuild checklist, preserved verbatim (the reason a delete is
safe — the design survives here and in git):**

> 1. Arms become IN-CODE stage constructions: `ClipcapStage(backend=Backend("vlm", n),
>    experiment="<arm>")` (the `.exp-<code>` dialect) — experimental packs load via
>    `prompts.load_registry(<arm dir>)` handed to an arm-specific describe(), or the
>    arm pack is temporarily added to the packaged registry on an experiment branch
>    (the digest pin + vB bump make it visible by construction).
> 2. Drive `resolve("video", [clipprep, screentext_fake_or_real, clipcap_arm])` +
>    `run_graph(resolved, c1=..., blob=..., span_seconds=..., clients=...)` and score
>    `GraphResult.slots` (one record; the old per-unit loop is gone).
> 3. The OCR truth/corrupt30 arms become client-level fakes handed via `clients`
>    (no `VIDEO_OCR_BACKEND`).
> 4. The storage-poison guard (`_forbid_storage`) and the scorers are still sound
>    and can be lifted as-is; `DP_OFFLINE_EVAL` keeps its serving-guard role in
>    `app/main.py` for whatever replaces this driver.

### Commit — `app/vision/circuit.py` retired

| Target | Disposition |
|---|---|
| `app/vision/circuit.py` (`CircuitBreaker`, `breaker_for`, `reset_all`, `CircuitOpenError`, the CLOSED/OPEN/HALF_OPEN state machine) | **RETIRE (delete).** Plan §9 listed it KEEP, but the brief overrides: it has been **wired nowhere through all five rebuild stages** (its own docstring said so — "present and tested, WIRED NOWHERE"). Its two v0 customers changed shape in v1: the OCR path rides `app/model_client.py` (replica rotation + bounded transient retry against the supervised fleet, which the supervisor restarts on crash), and the VLM endpoint is the optional `clipcap` stage whose failure is a hole healed by redrive (L7/L8). The one honest remaining use — fast-failing before `clipprep`'s ffmpeg during a sustained captioner outage — would have to live ABOVE the graph, not in this module, and nobody built that. The modern coverage is ModelClient rotation + heal; the breaker is dead weight. |
| `tests/test_circuit.py` (7 breaker tests) | **DELETE** — they go with the module, exactly as the brief directs. |
| `app/vision/__init__.py` | **EDITED** — the one docstring line naming `circuit` as "present, unwired" removed. |

Evidence: `app.main` imports after removal; DP suite **569 passed, 4 skipped**
(576 − the 7 `test_circuit.py` tests = 569, exactly as expected).

### Commit — prompt pack: remove `per-frame-v0`, bump clipcap vB, clear `experimental/` + `archive/`

| Target | Disposition |
|---|---|
| `app/vision/prompts/per-frame-v0.prompt.md` | **DELETE.** The legacy keyframe-era pack (`role: legacy`, `scenario: legacy-keyframe`), unreachable via `routes.json` — no live scenario selects it, and the legacy keyframe graph that used it died at Stage C. |
| `app/stages/video/clipcap.py` — `PACK_DIGEST_PIN` + `Backend` vB | **EDITED (the recorded churn).** The `PACK_DIGEST` is an aggregate over the whole pack dir, so removing per-frame-v0 moved it `565066a0 → 80efa39f`. Re-pinned, and `Backend("vlm", 1) → Backend("vlm", 2)` with a dated docstring line — exactly the "planned demolition (re-pin + vB bump)" the pin's own comment anticipated. Consequence, accepted and recorded: every video record now stamps `clipcap.v1-vlm.v2` (the dialect forks) even though `screen-clip-v1`'s caption bytes are byte-identical — the aggregate-digest guard trades that precision for the guarantee that no pack file can change silently. |
| `app/vision/prompts/LOCK.json` | **EDITED** — `pack_digest → 80efa39f`, per-frame-v0 dropped from `packs{}`. `pack_version` stays `"1"`: the human token was not relocked (this file's own rule — a pack edit forks the dialect via the live digest + the vB bump, not via the version token), which also keeps the relock tests' v1→v2 fixtures valid. |
| `app/vision/prompts/experimental/` (`screen-clip-blind-v1`, `screen-clip-hint-v1`, `README.md`) | **CLEARED (delete), stated.** Their only consumer was `prompt_ab.py`'s A/B arms, deleted above. The live experiment mechanism is unaffected — it is the in-code `.exp-<code>` Backend path (`app/vision/prompts/__init__.py`, `app/stagegraph/stage.py`), not this on-disk arm dir. |
| `app/vision/prompts/archive/` (`p1.json`) | **CLEARED (delete), stated.** It was the digest-input snapshot for reconstructing a historical video `pipeline_version`'s exact wire text — a promise that the OD-2 cutover mooted (every v0 record was wiped; no record produced under the old pack survives to reconstruct), and the pack it most needed to preserve (per-frame-v0) is now deleted. `relock` still writes a fresh `archive/pN.json` when a future version is deliberately cut; the relock tests generate their own archive in a tmp copy, so clearing the committed one leaves them green. |
| `tests/test_prompt_pack.py`, `tests/test_clipcap.py`, `tests/test_video_graph_v1.py`, `tests/test_t3_version_composition.py`, `tests/test_e2e_real.py` | **EDITED** — `_ALL` drops per-frame-v0; the two per-frame-specific parser tests rewritten to inline schema-less/placeholder-free fixtures (parser coverage no longer depends on a retired pack); every `clipcap.v1-vlm.v1` dialect assertion updated to `vlm.v2`. |

Evidence: DP suite **569 passed, 4 skipped** (unchanged — the two per-frame tests were
rewritten in place, not dropped). **Production continuity:** the running DP process
(old code, imported before the edit) still reports the video dialect
`clipcap.v1-vlm.v1+…` on `/health` — the vB bump is a *pending deploy* that takes effect
on the next deliberate fleet restart (drain-and-replace, D-14), not a live disruption;
the new pin is consistent with the demolished pack on disk for that restart.
