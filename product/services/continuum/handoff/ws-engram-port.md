# WS2 — Engram core port (the real TRAINER_BACKEND)

**Status:** unblocked (Gnandeep answered the lethal Qs 2026-07-22) — sequenced behind a
standalone reproduction baseline (Phase 1 below) · **Target:** reproduce the engram
recipe-v1.0 numbers on Speed's data, first with his code as-is, then behind our seam, so
recipe changes later are drop-in.

## Port policy (founder decision, 2026-07-22)

Files from Gnandeep's research repo are **ported into this service and adapted in
place** — not pinned as a submodule. Once the service runs end-to-end he works in our
modules directly. Discipline that replaces the pin:

- **Source snapshot (re-pinned 2026-07-22):** `github.com/gnandeep333-spec/nucleus-research`
  @ branch `continuum-research`, commit **`b3c58e1`** ("32B chain verdict"; supersedes the
  kickoff pin `9711f4a`). Everything through **v39** + all eval results are on it. Local
  clone `~/nmn/cl-research` is fast-forwarded to it.
- Every ported file's docstring records `ported from research/engram/code/<file> @ b3c58e1`.
- The **divergence log** (bottom) records every deliberate departure from source — re-sync
  is a conscious diff against the log, never an accidental fork.
- **Heads-up on drift:** the serve side moved a lot between `9711f4a`→`b3c58e1` (v33→v39,
  the **Council** 4th lane, page-weight cache, fresh 50-q blind banks). It is almost all
  *serve-time* (inference-service scope); the **nightly consolidation recipe is stable at
  v1.0**, so this port (the learn loop) is unaffected. Serve-tier drift is flagged in
  inference's HANDOFF.

## The real production night (corrected by Gnandeep + a read of the chain scripts)

My kickoff guess (`train_cpt` vs `phase_d_driver`) was half-wrong. The actual chain that
produced **both** serving adapters, traced through `submit_chain.sh` / `phased_run.sh` /
`sbatch/engram_phased.sbatch`:

```
build_day_corpus.py         5-min description JSONs → day{D}.blocks.jsonl   (login node, CPU)
gen_narrative.py            blocks → day{D}_x48neg.corpus.txt   (48× + 15% deny-then-correct; vLLM Qwen3-VL-8B)
phase_d_driver.py --arm replay --replay-frac 0.30 --days <chain>
                            continue-CPT ONE life adapter night-by-night over the amp corpora,
                            +raw replay from prior days, snapshot adapter_s{K}_d{D} per night
judge_exact.py              Gemini-2.5-flash judge → judge.json (decay matrix + traps)
```

`phase_d_driver` was the experiment harness that **decided** the recipe (verdict: real-data
replay ties the joint-training ceiling; generative rehearsal and adapter-merge fail) **and**
is the mechanism that produced the serving life adapter (the sequential `replay` arm, chained
night-to-night). So it is not "experiment vs production" — the replay arm *is* the production
night. This maps cleanly onto our `cycle.py` stages:

| our cycle stage | engram production step |
|---|---|
| `daylog` (build derived view) | `build_day_corpus.py` (→ blocks.jsonl shape) |
| `amplify` | `gen_narrative.py --variants 48 --neg-frac 0.15` |
| `replay_mix` + `train` | one step of `phase_d_driver.py --arm replay` (internal replay-mix + continue-CPT + snapshot) |
| `gate` | `judge_exact.py` (+ decay/traps readouts) |

**Port design note:** `phase_d_driver` does replay-mixing *internally* (`--replay-frac`).
Two clean options for the seam — (a) our `replay_mix` stage builds the mixed corpus and the
engram `train()` does plain CPT on it, or (b) `train()` encapsulates a phase_d step and owns
the mix. Decide at port time; (a) keeps our stage journal honest, (b) is a thinner port.

## Lethal questions — ANSWERED (Gnandeep, 2026-07-22)

1. **32B chain — a statistical TIE with 8B.** `life32b_s0`: 32B micro **0.083** vs 8B
   **0.092** on identical probes (probes_merged_v2, 29d×30, same Gemini judge); per-day 12–10
   for 32B, high variance. Verdict: **consolidation is write-bound, not capacity-bound → 8B
   stays his serving substrate**; capacity work moves to the write side (amp quality, replay
   dose). Adapter snapshots: `/home/ubuntu/engram/results/phased/life32b_s0/adapter_s{K}_d{D}`,
   final `adapter_s28_d32`. Exact base verifiable by one command on the cluster:
   `python -c "import json;print(json.load(open('.../adapter_s28_d32/adapter_config.json'))['base_model_name_or_path'])"`.
   32B-specific sharding/lr changes live in the job-521 sbatch — **verify at the cluster, don't
   assume.** Results committed: `research/engram/results/refeval/life32Bfinal.judge.json`.
   → **Founder note (not ours to decide):** our BWM is 32B (D6) and the life adapter must sit on
   whatever base inference serves, so v0 trains **32B** adapters — the recipe is base-agnostic
   and the 32B chain is recipe-identical/proven. The tie just means we pay 32B compute
   (~2 h/night) for **serve-quality, not memory-quality**; an 8B memory substrate is a possible
   later optimization but a serve-model change → founders' call. Scaffold already pins
   `BASE_MODEL_HASH = qwen3-vl-32b-instruct`, consistent with D6.
2. **Production entrypoint — `phased_run.sh`/`submit_chain.sh` (the phased/replay chain), NOT
   `phase_d_driver` as a standalone experiment.** Detailed above. Stable snapshot to pin =
   **`b3c58e1`** (done).
3. **Blessed knobs.** replay-frac **0.30** (recipe) — code default is 0.15; *confirm against the
   actual chain invocation before inheriting*. Replay **source: raw is acceptable** — the
   raw-reservoir A/B came back a **tie**, so raw-only is simpler than amplified-source replay.
   **neg-boost: no measured value on record** — read it off the chain's `train_cpt` args; do NOT
   take the code default on faith.
   → **Design simplification unlocked:** raw-source replay = tie means v0 can replay from the
   **retained raw day logs** (which storage keeps forever as the faithful record) instead of a
   separate amplified reservoir. Our scaffold currently replays `amp` and raises on `rawlog`;
   wiring the rawlog sampler + flipping `recipe.replay.source` is a v0 candidate that shrinks the
   storage design. (Keep the amp reservoir option for audit/exact-repro.)
4. **Envs + judge.** Export `conda env export -n speedlora` and `-n vllm23` into
   `research/engram/envs/` (Gnandeep will, or anyone with cluster access). `speedlora` =
   train/serve (torch/transformers/peft/gradio); `vllm23` = litellm + Vertex (judge + planner).
   Judge = **Gemini-2.5-flash via litellm on Vertex**. His GCP project (`poetic-avenue-438401-a7`,
   hardcoded in `engram_phased.sbatch`) is **his billing** → get **our own GCP creds via IAM,
   never by copying key files** (they stay outside git by design). → platform action item.
5. *(Real-user nights only)* prompt + `valid()` anchor on "Day K of Speed's tour" → real users
   need dates/places. Still open; template hook upstream vs parameterize during port — decide
   when Phase 3 touches real users, not needed for the Speed reproduction.

## Environment / gotchas the chain assumes (from the sbatch)

- `USE_HF=1`, `HF_HOME=~/.cache/huggingface`, `PATH=/usr/local/cuda/bin:$PATH` (else
  flashinfer/TRT JIT "nvcc not found"), `SPEED_CODE=/home/ubuntu/speed_lora/code` (the
  `score_canaries` import — becomes local when ported), `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`.
- `phase_d_driver` runs on **`CUDA_VISIBLE_DEVICES=0` (single GPU)**; 32B needs its shard/grad-ckpt
  flags (job-521 sbatch).
- **`sbatch --export=NAME=a,b` SPLITS ON COMMAS** and silently truncates a comma list to its first
  element (a 12-day run became 1-day). Pass dot-separated (`DAYS=5.9.12...`) or `--export=ALL`.
- Chain nights via `sbatch --dependency`, never background pollers (house rule; pollers fail silent).

## File manifest (source → target → adaptations)

| Source (`research/…` @ b3c58e1) | Target | Adaptations |
|---|---|---|
| `engram/code/build_day_corpus.py` | `app/engram/corpus.py` | **two input modes**: Phase-1 reproduction reads the existing 5-min description JSONs as-is; Phase-2+ reads our day-log renderer output (blocks.jsonl). Keep `render_block` FIELDS, anchor weaving, heldout guards, QA-eval-only whitelist |
| `engram/code/gen_narrative.py` | `app/engram/amplify.py` | keep 6 STYLES + NEG_STYLE + ok-rate≥0.85 exit; **de-Speed the PROMPT + `valid()` day-number check** (Q5); backend = vllm/hf as shipped; Vertex amplify backend is DESIGN-ONLY upstream |
| `engram/code/phase_d_driver.py` (`--arm replay`: `sample_replay`, `train_on` matched-compute, per-step snapshot) | `app/engram/train.py` | the production night's train step; wire `--replay-frac 0.30`, **raw** source (tie), `replay_neg_boost` from the chain args; snapshot = our adapter artifact |
| `engram/code/train_cpt.py` | (folded into `train.py`) | `lm_lora_targets` + `chunk_corpus` + r128/α256 CPT core; paths from `config.py` |
| `engram/code/gen_selfstudy.py`, `build_ext_probes.py` | `app/engram/probes.py` | probe-generator ≠ corpus-generator; our probe bank under var/ |
| `engram/code/judge_exact.py` | `app/engram/judge.py` | Gemini-2.5-flash via litellm; `VERTEX_PROJECT` = ours (IAM) |
| `engram/code/eval_adapter_days.py`, `eval_final_adapter.py` | `app/engram/eval.py` | drives the gate's real scores (unlocks the 3 skipped checks) |
| `speed-lora/code/score_canaries.py` | `app/engram/scorers.py` | f1/contains/trap scorers — the `SPEED_CODE` import becomes local |
| `engram/code/{phased_run,submit_chain}.sh`, `sbatch/*` | **not ported** | replaced by `cycle.py` stage keys + M1 SLURM submission; idempotent-gate pattern already in-process |
| `engram/code/{engram_server,engram_worker,planner_cli,mneme,train_mneme_proto,ttt_probe}.py` | **not ported here** | serve-time memory harness — inference scope (now a **4-lane** stack incl. Council); mneme/reader-LoRA *training* returns here later as artifact jobs |

## Exit criterion

Phase-1 baseline reproduced (his code, our infra) AND Phase-2 parity (our ported seam matches
Phase-1 on the same days): judged recall ≈0.26–0.35, +≈0.33 seen−heldout separation, traps
≈0.50, heldout ≤0.05 → published via OUR C5 path → adapter loads in vLLM (charter M0 exit).

## Divergence log

| Date | File | Departure from source @ b3c58e1 | Why |
|---|---|---|---|
| — | — | *(none yet — log every deliberate change here)* | |
