# WS2 — Engram core port (the real TRAINER_BACKEND)

**Status:** queued — blocked on Gnandeep's answers to the lethal questions (below) ·
**Target:** close the loop on Speed's data with the recipe EXACTLY as researched, so
recipe changes later are drop-in.

## Port policy (founder decision, 2026-07-22)

Files from Gnandeep's research repo are **ported into this service and adapted in
place** — not pinned as a submodule. Once the service runs end-to-end he works in our
modules directly. Discipline that replaces the pin:

- **Source snapshot:** `github.com/gnandeep333-spec/nucleus-research` @ branch
  `continuum-research`, commit **`9711f4a`** (2026-07-21). Local clone: `~/nmn/cl-research`.
- Every ported file's docstring records `ported from research/engram/code/<file> @ 9711f4a`.
- The **divergence log** (bottom of this file) records every deliberate departure from
  source — when his research moves ahead, re-sync is a conscious diff against the log,
  never an accidental fork.

## File manifest (source → target → adaptations)

| Source (`research/…`) | Target | Adaptations |
|---|---|---|
| `engram/code/build_day_corpus.py` | `app/engram/corpus.py` | input = our day-log renderer output (blocks.jsonl), not POC description JSONs; keep `render_block` FIELDS, anchor weaving, heldout guards, QA-eval-only whitelist |
| `engram/code/gen_narrative.py` | `app/engram/amplify.py` | keep 6 STYLES + NEG_STYLE + ok-rate≥0.85 exit; **de-Speed the PROMPT + `valid()` day-number check** (lethal Q5: template hook upstream vs parameterized here); backend = vllm/hf as shipped; Vertex backend is DESIGN-ONLY upstream — building it is a separate decision (founders: cost/privacy/GPU) |
| `engram/code/train_cpt.py` | `app/engram/train.py` | `lm_lora_targets` + `chunk_corpus` + r128/α256 CPT core; paths from `config.py`, not `/home/ubuntu/engram`; 32B shard/grad-ckpt flags per Q1 answer |
| `engram/code/phase_d_driver.py` (only `sample_replay` + `train_on` matched-compute budget) | `app/engram/replay.py` | replaces the scaffold's naive append-mix; wire `replay_neg_boost` (traps erode by ~night 12 without it); source amp-vs-rawlog per Q3 answer |
| `engram/code/gen_selfstudy.py`, `build_ext_probes.py` | `app/engram/probes.py` | probe-generator ≠ corpus-generator rule (different model family); our probe bank layout under var/ |
| `engram/code/judge_exact.py` | `app/engram/judge.py` | Gemini judge via litellm; `VERTEX_PROJECT` = ours when platform lands creds (his project short-term per Q4) |
| `engram/code/eval_adapter_days.py`, `eval_final_adapter.py` | `app/engram/eval.py` | drives the gate's real scores (unlocks the 3 skipped checks) |
| `speed-lora/code/score_canaries.py` (in-repo copy) | `app/engram/scorers.py` | f1/contains/trap scorers engram imports via `SPEED_CODE` — the import becomes local |
| `engram/code/sbatch/*` | **not ported** | replaced by `cycle.py` stage keys + M1 SLURM submission; the idempotent-gate pattern is already in-process |
| `engram/code/{engram_server,engram_worker,planner_cli,mneme,train_mneme_proto,ttt_probe}.py` | **not ported here** | serve-time memory harness — inference service scope (founder decision 2026-07-22); mneme/reader-LoRA *training* returns here later as artifact jobs |

## Lethal questions for Gnandeep (V0-blocking)

1. **32B chain:** did `life32b_s0` (job 521) hold — recall/decay/traps vs 8B? Exact base
   variant (HF id) + where adapter snapshots live. (32B-specific recipe changes: sharding, lr?)
2. **Production-night entrypoint:** `build_day_corpus → gen_narrative → train_cpt` with
   replay pre-mixed, or `phase_d_driver --arm replay` as-is? Which commit is the stable snapshot?
3. **Blessed knobs:** replay_frac (code default 0.15 vs recipe 0.30), replay-source
   `amp` vs `rawlog`, neg-boost value — what does the current chain run?
4. **Envs + judge:** export `speedlora`/`vllm23` env specs; OK to use his GCP project for
   the judge short-term?
5. *(Blocks real-user nights only, not the Speed dry-run):* prompt + `valid()` anchoring
   is "Day K of Speed's tour" — real users need dates/places. Template hook upstream, or
   parameterize during port?

## Exit criterion

One night of Speed's data (a day-log built from his repo's corpus artifacts) runs through
`TRAINER_BACKEND=engram` on node-7 off-peak and reproduces recipe-v1.0-grade numbers
(judged recall ≈0.26–0.35, traps ≈0.50, heldout ≤0.05) through OUR gate → published via
OUR C5 path → adapter loads in vLLM (charter M0 exit).

## Divergence log

| Date | File | Departure from source @ 9711f4a | Why |
|---|---|---|---|
| — | — | *(none yet — log every deliberate change here)* | |
