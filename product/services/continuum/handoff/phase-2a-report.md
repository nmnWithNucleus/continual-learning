# Phase 2a — Morpheus core + parity harness

**Branch:** `svc/continuum-morpheus-2a` · **Status:** kernels + harness landed; E2E seed
ensemble running · **Cofounder review gate before 2b.**

Deliverable: the §3 kernels reimplemented cleanly under `app/morpheus/`, behind
`TRAINER_BACKEND=morpheus`, with a parity harness that differences every one of them against
the Phase-1 goldens. No kernel is "ported" without a green parity test.

---

## 1. What landed

| Module | Kernel | Lines |
|---|---|---|
| `morpheus/profiles/{base,speed}.py` | the Profile seam — prompt, styles, `valid()`, anchor scheme, calibration matcher, horizon | 78 + 133 |
| `morpheus/blocks.py` | day-log block shape + `render_block` transport (rendering itself is the profile's) | 74 |
| `morpheus/amplify.py` | 48× plan, deny-then-correct selection, validity gate, ok-rate abort | 152 |
| `morpheus/generate.py` | vLLM / HF / stub generator seam (kernel stays CUDA-free) | 108 |
| `morpheus/replay.py` | pooled-uniform rehearsal sampler, matched-compute budget, neg-boost knob | 96 |
| `morpheus/train.py` | `chunk_corpus`, LoRA target selection, the CPT loop, `LifeAdapter` (open/resume/save/answer) | 233 |
| `morpheus/scorers.py` | `TRAP_MARKERS`, f1 / contains / trap / order | 106 |
| `morpheus/probes.py` | suite loading, per-day pools, the probe ≠ corpus-generator rule | 106 |
| `morpheus/judge.py` | Gemini-2.5-flash via litellm/Vertex, suite-keyed summary | 130 |
| `morpheus/eval.py` | prediction log, decay matrix, readouts, seed-ensemble bands | 216 |
| `morpheus/pinned_env.py` | absolute-interpreter execution + import preflight | 96 |
| `backends/morpheus.py` | the three-verb seam over the kernels | 148 |

Plus `scripts/` (`morpheus_chain`, `judge_preds`, `parity_report`, `amplify_day`,
`capture_env_locks`) and `tests/parity/` + `tests/test_morpheus.py`.

**Rename done first, as specified:** `TRAINER_BACKEND` is now `mock` | `morpheus`;
`app/backends/engram.py` is gone; every `ws-engram-port` reference updated. No "engram" remains
in our surface (`grep -ri engram app/ tests/ recipes/` is empty).

**Scaffold gap closed on the way past:** `train()` now also receives the new-day-only corpus, so
the matched-compute budget is computed from the day rather than the mix. WS1 flagged this as a
known gap ("replay mixing appends; the budget cap ports with the trainer") — it is now wired
through `base.py` / `mock.py` / `cycle.py`.

## 2. Kernel parity — all green

`tests/parity/` runs in two tiers. Tier A needs no ML stack; tier B needs the pinned train env
(a `--system-site-packages` venv over `speedlora`, so the shared conda env is never mutated).

| Kernel | Assertion | Result |
|---|---|---|
| `render_block` | byte-identical text for **every** golden block, 6 days | **1427 / 1427** |
| `render_block` | rendered day corpus == `day{D}.txt` | exact, 6/6 days |
| anchors | profile's day/city == golden block columns | 6/6 days |
| amplify plan | job count == golden `ok + err` | exact, 6/6 days |
| amplify neg-frac | planned negatives vs **denial-phrased paragraphs counted in the reference corpus** | agree within 0.5 % on all 6 days (e.g. day 17: 1990 planned vs 1989 observed) |
| amplify neg-frac | realized fraction vs recipe 0.15 | inside 3σ binomial on all 6 days |
| amplify validity | our `valid()` accepts every paragraph the reference kept | **0 rejected of 68 440** |
| amplify rebuild | day-5 corpus rebuilt from reference generations | same paragraph multiset, `chars` exact, **ratio 1.0000** |
| amplify ok-rate | degraded generator aborts the night | raises `AmplifyBelowOkRate` |
| replay + chunking | per-night `(chunks, chunks_per_epoch, steps)` vs the reference `train_report` | **18/18 integers exact, on two independent reference runs** |
| LoRA config | `r`/`alpha`/`dropout`/`bias` vs golden `adapter_config.json` | 128 / 256 / 0.0 / none |
| LoRA targets | our selection vs the golden adapter's **tensor keys** | **252/252 modules**, 7 projections × 36 layers, zero vision-tower |
| scorers | `trap_score` re-run over reference transcripts vs published `D4_traps_by_step` | exact, 3 reference runs |
| judge | `summarize(stored verdicts)` vs published `judge.json` | **exact, 35 suites × 4 runs**, micro included |
| judge (live) | re-judged agreement on a fixed slice | opt-in `MORPHEUS_LIVE_JUDGE=1` |

The replay/chunking row is the strongest evidence in the harness. There is no dump of the
reference chain's rehearsal text to diff against, but each night's chunk counts are a
fingerprint of the whole deterministic half of the night: the rehearsal RNG stream seeded once
per chain and consumed across nights, paragraph eligibility and pooled order, the character
budget taken from the *new day's* length, the greedy fill's overshoot-by-one, tokenization,
chunk slicing, and the step schedule. Reproducing all 18 integers means all of it matches.

Counts: **149 tests green** (46 scaffold + 103 new), 7 skipped in tier A (they run in tier B).

```
.venv/bin/python -m pytest -q                 # tier A: 149 passed, 7 skipped
.venv-train/bin/python -m pytest -q           # tier B: + tokenizer/peft parity
```

## 3. E2E seed ensemble

Three chains (seeds 0/1/2), 6 nights each on days 5,9,12,13,17,21, through the real kernels:
continue-CPT the one adapter, 30 % matched-compute rehearsal, closed-book eval of every
consolidated day plus traps after each night, base-model floor and heldout controls at the end.
1908 predictions per chain — the same count the reference runs produced.

*Results table lands here when the chains finish; `scripts/parity_report.py` prints it and
writes `parity_report.json`.*

Two deliberate choices about what the E2E does **not** vary:

- **It trains on the reference amplified corpora**, not a fresh 48× generation. Re-amplifying
  would stack generator variance on top of the seed spread and confound exactly the comparison
  being made. Amplification is proven separately and more sharply — kernel-by-kernel above, and
  in situ through the real vLLM path (§4).
- **The rehearsal stream seed is fixed at 7 across the ensemble**, because that is how the
  reference ensemble was built (`set_seed(seed)` varied LoRA init; `random.Random(7)` did not
  move). The spread we are comparing against measures init variance, so ours must too.

**Base model for parity is Qwen3-VL-8B.** The goldens are 8B runs; 32B is the production serve
target (the adapter must match the served base) and 32B ≈ 8B on identical probes is a measured
tie — consolidation is write-bound, not capacity-bound. Parity is run where the numbers to match
exist; the 32B chain is 2b's concern.

## 4. Exec model

- Both environments are invoked by **absolute interpreter path**. `PinnedEnv.preflight()` imports
  the required modules in one second before any GPU work — the reference chain's `phased_run.sh`
  died precisely here (`conda activate` did not reorder PATH in a non-interactive shell, and the
  `python` that ran lacked peft, after the corpus was already built).
- `MORPHEUS_DEVICE` is a real knob and GPU-0 hardcoding is gone. The three chains run on GPUs
  7 / 2 / 4 concurrently.
- Lockfiles captured to `env/`: `train.pip.lock.txt` (186 pkgs), `judge.pip.lock.txt` (247),
  both conda exports, and `fingerprint.txt`:

  ```
  torch 2.12.1 · transformers 5.12.1 · peft 0.19.1 · accelerate 1.14.0 · safetensors 0.8.0
  litellm 1.92.0 · NVIDIA H100 80GB HBM3, driver 580.159.03
  ```

  peft 0.19.1 is the exact version stamped in the golden `adapter_config.json`.
- Judge credentials verified on our own Vertex access (`VERTEX_PROJECT=poetic-avenue-438401-a7`).

## 5. The `Profile` seam

`SpeedProfile` is the only place the domain is named. The kernels are checked by a test that
reads their own source and fails if any of them indexes an anchor directly, imports a concrete
profile, or contains a domain literal.

That test earned its keep immediately: the rehearsal sampler had inlined the denial-phrase regex
used by the neg-boost knob, which is the *inverse* of the profile's negative style. It now lives
on the profile as `is_calibration()`, and the sampler takes the predicate. Generalizing remains
one new file.

## 6. Open decisions for the cofounders

1. **`replay_source` stays `amp` in recipe v1.0.** The locked decision is raw day-logs. Both are
   implemented and selectable today, and the tie is confirmed on the node (`h12_rawres_s0` mean
   final recall **0.1250** vs `h12_replay_s0` **0.1250** — identical). But the goldens were
   produced with `amp`, so parity has to be run against `amp`, and flipping the knob forks
   `recipe_id`. The natural moment to fork to v1.1 is **2c**, when the day-log fetch client makes
   raw logs fetchable at all. Flag if you want it sooner — it costs one E2E ensemble to validate.
2. **Calibration is not over-tuned, and the data on the node backs the decision**:
   `h12_calib_s0` (40 % neg-boost) scores **0.0208** mean final recall against **0.1250** for the
   uncalibrated arm — a lobotomy. `replay_neg_boost` ships as a ≤10 % tunable, default 0.
3. **Research arms deliberately not ported** (`smart`, `dream`, `smartdream`, `olora`, `agem`,
   `joint`, `replay_floor`) — see the divergence log. None is recipe v1.0; reviving one is a
   research question, not a port gap.

## 7. Next (2b)

Wire the real backend into `cycle.py` end-to-end on a Speed day → gate → C5 publish → **adapter
loads in vLLM** (charter M0), on the 32B base. The backend already implements the three verbs;
what 2b adds is the real cycle run and the vLLM load proof.
