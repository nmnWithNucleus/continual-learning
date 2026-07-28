# ws-video-clip-eval.md — the offline eval harness and the quality gates (WS-H)

**Workstream:** WS-H (`§11 → WS-H` of `handoff/ws-video-clip.md`).
**Scope:** the offline A/B, the ground-truth corpora, the Gemini oracle, the mechanical scorers.
**Status:** built, green, and *honest about what it could not measure in a headless build*.
**Suite:** 502 passed (baseline 465 + 37 new) with `ASR_BACKEND=mock`.

---

## 0. What this is, in one paragraph

`scripts/prompt_ab.py` runs a corpus of video chunks through the **real** video stage graph —
`resolve()` + `run_graph()` + `pipeline.build_c2`, imported directly — once per experimental
arm, and prints the arms side by side under mechanical scorers. It exists to settle the one
premise the design argued instead of measuring (**O-8**: does the captioner actually get
better when it is shown the OCR text, and does it get worse when that text is wrong?), and
to be cheap enough to run on every push, because *an eval expensive enough to skip will be
skipped*.

---

## 1. Files

| file | what it is |
|---|---|
| `scripts/capture_chunkset.py` | builds a **chunkset** (C1 + optional blobs + ground truth). Four modes: `synth` (labelled ffmpeg-drawn screens), `headless` (JSON-only, committable), `slice` (cut a real recording), `wrap` (a dir of clips). Plus `ocr-truth`, the O-2 bridge (§7). |
| `scripts/prompt_ab.py` | the A/B driver + the arm worker + every scorer + the O-8 gate + the pre-push `--check` gates. |
| `scripts/oracle_gemini.py` | the blind pairwise LLM judge (O-4's rubric) and the frontier-model oracle. Needs a Vertex/Gemini credential; exits 4, loudly, without one. |
| `app/vision/prompts/experimental/screen-clip-blind-v1.prompt.md` | arm **B** — the captioner sees frames only. |
| `app/vision/prompts/experimental/screen-clip-hint-v1.prompt.md` | arm **D** — OCR usable for the app *name* only (the ratified fallback). |
| `tests/fixtures/chunksets/smoke-v1/` | the committed 12-chunk headless corpus (no binaries). |
| `tests/test_eval_scorers.py` | 37 tests: the scorers, the O-8 decision table, the arm fork, and the two safety guards. |
| `app/main.py` (one guard) | `DP_OFFLINE_EVAL=1` ⇒ `create_app()` raises. |

---

## 2. Run it

```bash
cd product/services/data-processing

# the pre-push form — headless, offline, ~2.5 s, four hard gates
DP_OFFLINE_EVAL=1 ./.venv/bin/python scripts/prompt_ab.py \
    --chunkset tests/fixtures/chunksets/smoke-v1 --check

# a labelled corpus with real pixels (no capture needed, no binaries in git)
./.venv/bin/python scripts/capture_chunkset.py synth --out /tmp/cs --count 200 --span 10

# the O-8 gate against a served captioner
DP_OFFLINE_EVAL=1 VIDEO_BACKEND=vlm VIDEO_VLM_URL=http://127.0.0.1:8000 \
  ./.venv/bin/python scripts/prompt_ab.py --chunkset /tmp/cs --gate o8 \
      --rows-dir /tmp/rows --json /tmp/report.json

# O-4: per-frame (legacy keyframe graph) vs per-clip, same model
DP_OFFLINE_EVAL=1 VIDEO_BACKEND=vlm ./.venv/bin/python scripts/prompt_ab.py \
    --chunkset /tmp/cs --arms keyframe,injected --rows-dir /tmp/rows

# the blind judge / the oracle (costs money; --yes is mandatory)
export VERTEX_API_KEY=...
./.venv/bin/python scripts/oracle_gemini.py judge --rows-dir /tmp/rows \
    --arms injected,blind --chunkset /tmp/cs --yes --json /tmp/judge.json
```

Exit codes: `0` ok · `1` a `--check` gate failed · `2` misuse (missing `DP_OFFLINE_EVAL`,
unknown arm) · `3` the O-8 gate returned `UNDECIDED` · `4` (oracle) no credential.

---

## 3. Why it cannot write to `/context`, and why an arm cannot leak into production

Three independent mechanisms, none of which is a comment:

1. **Below the writer.** The harness enters at `resolve()`/`run_graph()`/`build_c2`.
   `ingest_core.py`'s per-unit loop is the only `/context` writer in the system and it lives
   *above* the processor seam. No FastAPI, no `StorageClient`.
2. **Enforced, not asserted.** `_forbid_storage()` poisons `StorageClient.__init__` **and**
   `ingest_core.process_chunk` in the arm worker before a single stage is imported.
   - The class object *is* reachable — `executor` imports `ingest_core` for `ProcessingError` —
     so "we simply never call it" was not good enough.
   - A future refactor that reaches for storage from below the seam fails here, in an eval,
     instead of quietly minting
   experimental records in a real corpus.
3. **The mirror guard.** `DP_OFFLINE_EVAL=1` is required by the harness and makes
   `app/main.py:create_app()` raise. *The flag that enables experiments is the flag that
   prevents serving.* Asserted both ways in `tests/test_eval_scorers.py`.

---

## 4. Why arms cannot collide (and a correction to §11's file placement)

### The mechanism

Each arm is assembled into its **own complete prompt registry** in a temp dir — the six
packaged packs + `schemas.json` + the arm's experimental pack + a rewritten `routes.json`
whose `family_defaults.clip` is the arm's pack + an `arm.json` — and runs in its **own
subprocess** with `VIDEO_PROMPT_DIR` pointed at it. (Packs load once per process at import,
D-13's TOCTOU discipline, so one process physically cannot hold two arms.)

The fork is then automatic and two-fold:

* `prompt_dir_fingerprint` is `OUTPUT_AFFECTING`, so the arm dir's **contents** fold into
  `cfg_tag` — arms fork under **every** backend, including `mock`, where `prompt_tag` is
  `""` by design and would not fork on its own;
* `PACK_DIGEST` + the pack id fork `prompt_tag` under `vlm`/`vertex`.

`arm.json` is never read by anything. Its whole job is to be hashed: it is what makes
`injected` and `injected-corrupt` — whose pack text is byte-identical — fork anyway.
`LOCK.json` is deliberately **not** copied into an arm dir, so an eval arm cannot claim the
production pack's locked human version.

Measured, four arms, mock backend and vlm backend:

```
injected          vidclip-vlm-v1@screen-clip-v1.p1.565066a0#37f957b6+cp-v1+ocr-mock-v1
blind             vidclip-vlm-v1@screen-clip-blind-v1.p1.653dfd11#84ce2f3b+cp-v1+ocr-mock-v1
hint              vidclip-vlm-v1@screen-clip-hint-v1.p1.8e8ed082#33f132e5+cp-v1+ocr-mock-v1
injected-corrupt  vidclip-vlm-v1@screen-clip-v1.p1.565066a0#69103dc6+cp-v1+ocr-mock-v1
=> 4 distinct dialect(s) across 4 arm(s)
```

Note `injected` and `injected-corrupt` share the pack digest `565066a0` and differ only in
`cfg_tag` — exactly the `arm.json` effect. The report prints these strings and the resulting
`record_id`s side by side on every run; `--check` fails the run if two arms ever share one.

### The correction — the experimental packs live in a SUBDIRECTORY

`§11 → WS-H` names the arm packs as `app/vision/prompts/screen-clip-blind-v1.prompt.md`, on
the reasoning that "new paths ⇒ no conflict with D's production packs". **Verified against
WS-D's shipped registry, that reasoning does not hold**, and following it literally would be
a production incident:

* `PACK_DIGEST = compute_digest(_PACKS, _ROUTES)` is a digest over **every loaded pack**
  (`app/vision/prompts/__init__.py`), and `load_registry` globs `*.prompt.md` in the source
  dir. Two extra files in the flat directory change the aggregate digest → change
  `version_tag(vs)` → change the clip primary's `version_fragment` → **fork `record_id` for
  every production caption**, for an experiment that never ran.
* WS-D's `tests/test_prompt_pack.py:47,200,452` asserts the loaded pack set is exactly the
  six shipped ids, so the same drop-in reddens the suite — in a file WS-H does not own.

`load_registry`'s glob is **non-recursive**, and so is `test_prompt_pack._copy_pack`'s, so
`app/vision/prompts/experimental/` is completely inert to the packaged registry and to every
WS-D test, while remaining a committed git path (which is what "a pack is only reproducibly
defined by a git state" needs). `tests/test_eval_scorers.py::test_experimental_packs_exist_but_are_not_in_the_production_registry`
is the regression guard, and it says what moving them would cost.

**No WS-D file was edited.** The registry module, `routes.json`, `LOCK.json`, the six packs
and `tests/test_prompt_pack.py` are untouched.

---

## 5. The scorers

All mechanical. All pure functions in `scripts/prompt_ab.py`, all unit-tested.

| scorer | definition |
|---|---|
| dialect fork | the arms' `pipeline_version` strings + the resulting `record_id`s, printed side by side, each re-derived from `compute_record_id(chunk_id, pv, discriminator)` |
| records/chunk · chars/record · chars/caption | means over the arm's C2 records |
| caption cap · truncation rate | `budget.caption_cap(span, vs)` recomputed from env; truncated iff the rendered caption reached the cap or the raw reply exceeded it |
| day-log chars/block | every record rendered through **continuum's own `build_daylog`** (imported from `product/services/continuum`, never reimplemented), against the live `EXCERPT_CHARS` from `morpheus/profiles/speed.py` |
| parse-fallback rate | fraction of chunks whose `ClipDesc.parsed` is false |
| `app != "unknown"` rate | fraction with a non-empty, non-`unknown` `app` |
| change-verb rate | fraction of captions containing a verb from a frozen 40-word vocabulary — the mechanical proxy for "did this reason across frames" (O-4's headline) |
| **`ungrounded_quote_rate`** | the `NARROW` measure the shipped counter implements: double-quoted spans absent from the chunk's OCR text |
| **`ungrounded_named_rate`** | the **`WIDENED`** measure (addendum edit #2): *all* named ≥4-char strings |
| `named_entity_recall` | ground-truth entities recovered by the caption (lenient substring, casefolded) |
| `app_correct` | the caption's `app` matches the chunk's truth app (either containing the other) |
| `propagation_rate` | fraction of chunks whose caption contains a string the corrupted-OCR arm **falsified** |
| tokens / node-seconds / USD | measured `usage` off the wire, costed at §7's 12k prefill / 2k decode tok/s and $16/node-hour |

### The widened grounding scorer, precisely

A **named string** is either a double-quoted span, or a maximal run of adjacent *namelike*
tokens joined by a plain space. A token is namelike when it is ≥4 chars, not a stopword,
and either mixes letters with digits (`node-7`, `Q3`), carries an internal capital
(`StageContext`), has a path/dot/`@` shape (`executor.py`, `arxiv.org`), or is capitalised.
A run breaks at punctuation, so `"…in Gmail. Sarah replied"` yields `Gmail` and `Sarah`, never
the invented phrase `Gmail Sarah`. Grounding is a **lenient substring** test against the
chunk's OCR text (whole phrase first, then every constituent token) — deliberately lenient,
because O-2 measured 0.000 *strict* recall on a model that was reading correctly.

The stopword list is the whole defence against sentence-initial capitalisation being scored
as an invention; it includes the change-verb vocabulary and common English, and deliberately
**excludes** app-shaped common nouns (`Terminal`, `Safari`, `Numbers`, `Mail`, `Preview`) —
those are real macOS application names, and noticing when a caption states one the OCR pass
never read is the scorer's entire job.

**Why the widening matters, measured here:** across every run in §6 — mock captioner and
stub captioner, 6 to 40 chunks, four arms — the corpus contained **0 double-quoted spans**
and 5–24 named strings per arm. A quote-only counter had literally nothing to measure. The
design's 32.6 % is its own figure; this harness's contribution is that the counter which is
supposed to make injection safe was, on every caption we produced, measuring an empty set.

---

## 6. Results

### 6.1 What actually ran

| | status |
|---|---|
| mechanical scorers, headless corpus | **run** — 12 chunks × 2 arms, 2.5 s, all four gates `PASS` |
| mechanical scorers, labelled corpus with real pixels | **run** — 40 chunks × 4 arms, 14.6 s |
| the O-8 gate, end to end, against a captioner whose output is a function of its prompt | **run against a stub endpoint** (see 6.3) — the gate discriminates and rules |
| the O-8 gate against the real Qwen3-VL | **NOT run** — no served endpoint in this build (E-3(a)) |
| O-4 blind judge / the ~$70 Gemini oracle | **NOT run** — no Vertex credential in this build |
| O-2 real-frame bake-off | **NOT run** — no real macOS capture; the bridge is built (§7) |

### 6.2 The plumbing, on the committed corpus

```
-- gates --
[PASS] no chunk errors: clean
[PASS] arms fork (no dialect collision): 2 distinct pipeline_version over 2 arm(s)
[PASS] record_id recomputes: all arms
[PASS] day-log blocks within EXCERPT_CHARS: all blocks within budget

records/chunk 2.000 (both arms)   — D-05's fixed set: one caption + one ocr, always
day-log       1 block, 1,960 chars, EXCERPT_CHARS=6,000, 67.3 % headroom, 0 over budget
```

The day-log projection is the D-11 claim measured rather than argued, through continuum's
own renderer: at `segment_seconds=60, block_segments=2` the block sits well inside the
amplifier's excerpt window, so the OCR line — which renders **last**, and which truncation
therefore kills **first** — survives.

### 6.3 The O-8 gate, validated against a stub captioner

**This is a validation of the harness, not an O-8 result.** With no GPU endpoint available,
the gate was run against a local stand-in whose reply is a deterministic *function of the
rendered prompt* (it names what the OCR block told it, and nothing when it was told nothing).
That is enough to prove the gate discriminates and that the pre-registered rule fires:

```
6 labelled synth chunks, 4 arms
  app != unknown        injected 1.000   blind 0.000   hint 1.000   corrupt 1.000
  named-entity recall   injected 0.236   blind 0.000   hint 0.236   corrupt 0.194
  propagation rate                                                  corrupt 0.600
  VERDICT: SHIP D (screen-clip-hint-v1)
  why: recall_lift=0.2361 <= 0.25 and propagation_rate=0.6000 >= 0.10
```

Both halves of the rule bit, in opposite directions, on the same run — which is exactly the
property a gate needs to be worth running. **These numbers say nothing about Qwen3-VL.**

Under `VIDEO_BACKEND=mock` the gate refuses to rule at all (`UNDECIDED`), because a mock
captioner reads no prompt and every arm is identical by construction. A gate that would
"decide" from a corpus that cannot answer is worse than no gate.

### 6.4 Cost and wall — and a correction to the exit criterion

| | measured |
|---|---|
| headless, mock captioner | ~21 s per 200-chunk arm |
| real ffmpeg prep, mock captioner | ~18 s per 200-chunk arm (40 chunks × 4 arms in 14.6 s) |
| real ffmpeg prep, stub endpoint | ~50 s per 200-chunk arm |
| projected GPU cost, 10 s spans | **~$0.139 per 200-chunk arm** |

The ~40 s wall target holds (chunks run concurrently behind a semaphore, `--concurrency 8`;
at the design's 1.6 s single-stream decode, 200 chunks ÷ 8 ≈ 40 s).

**The ~$0.02 target does not reconcile with §7.3's own arithmetic.** 200 chunks × 1,517
prefill + 60 output tokens, at 12k/2k tok/s and $16/node-hour, is **$0.139** — which is just
§7.3's own `$0.250/screen-hour ÷ 360 chunks/hour × 200`. The stated $0.02 is ~7× low. **The
conclusion is unaffected** (14 cents is emphatically pre-push cheap), but the number in §11
should be restated as ~$0.14, or the pre-push corpus scoped to ~30 chunks. The harness prints
this reconciliation on every run rather than quietly reporting a figure that misses its
stated target.

---

## 7. O-2 — consuming WS-C's labelling pass (the bridge)

WS-C shipped the bake-off harness and a synthetic-proxy verdict (`sidecars/ocr/bakeoff/REPORT.md`:
ppocr@1728 recall 0.988 / CER 0.070 on a 204-frame proxy; **gate NOT satisfied**, because it is
defined over ~200 hand-labelled *real* macOS frames; production default stays
`VIDEO_OCR_BACKEND=mock`). Their report promises the harness "accepts real frames with zero
code change".

`capture_chunkset.py ocr-truth` is the other half of that sentence — **one labelling pass,
two gates**:

```bash
python scripts/capture_chunkset.py ocr-truth --chunkset /path/to/labelled-cs --out /tmp/o2
# -> /tmp/o2/ground_truth.json + /tmp/o2/frames_src/*.jpg, in the bake-off's exact schema
```

It exports the **high-resolution OCR renditions the `screentext` stage would actually have
read** (`jpeg_hi` at `VIDEO_OCR_FRAME_WIDTH`, via `prepare_clip` — not a fresh differently-scaled
extraction), and writes `focus_bbox_2x` in the 3456-wide canvas `run_bakeoff.py:SRC_2X_W`
hard-codes. So the same `truth.ocr_regions` that lets `prompt_ab.py` run the O-8 injection
gate re-runs the O-2 sidecar gate, without labelling the corpus twice and without the two
gates drifting onto two different notions of what the screen said.

**Operator note:** `run_bakeoff.py` re-encodes each input at CRF 28. Frames pulled from a real
capture are already CRF-28 degraded, so a straight re-run measures a *double* encode — a
conservative lower bound. Use `ablate_crf.py`'s raw arm for the honest single-encode number.

**No real macOS capture exists in this build, so O-2 remains where WS-C left it: unsatisfied,
`mock` is the production default.**

---

## 8. O-4 — per-frame vs per-clip

The control arm is `keyframe`: the retained legacy graph, driven through the same
`resolve()`/`run_graph()`, so the comparison is per-frame captions vs one clip description
**on the same model, same corpus, same assembly**, which is precisely the A/B the design
notes does not exist anywhere in the repo.

`--arms keyframe,injected` gives the mechanical half today (records/chunk, chars/record, the
day-log projection, change-verb rate, tokens). The *qualitative* half — the POC's
frame-grounded rubric — is `oracle_gemini.py judge`, which is built and unrun for want of a
credential. The judge is blinded by construction: presentation order is
`sha256(chunk_id, arms)[0] % 2`, deterministic and uncorrelated with the arms, and the
mapping is unblinded only after the verdict is read.

One honest note on the keyframe arm: the legacy dialect is a frozen `""` exemption (D-14),
so it cannot fork and gets no `VIDEO_PROMPT_DIR`. Its `pipeline_version` is literally
`vidproc-mock-v0` / `vidproc-vlm-v0`, the same string production would use. Nothing is
written, so this is safe — but it is the one arm whose identity is not experiment-specific,
and `--check`'s collision gate is what would catch a second keyframe arm.

---

## 9. Caveats — what a reader must not over-read

**H-1 — The eval dialect names a backend it did not use.** The truth/corrupt OCR injector
replaces only the *reader* (`ocr.select`), so `version_fragment` still reports
`+ocr-mock-v1` while the OCR content is oracle-injected. Harmless offline (arms fork on
`arm.json`, and nothing is written) and it is exactly why the eval dialect must never reach
production — which is what the `DP_OFFLINE_EVAL` boot guard enforces.

**H-2 — A `headless` chunkset leaves the OCR channel dark.** With no blob, `clipprep` takes
its synthetic-frames fallback, which returns `ocr_times=()`; no OCR frame is read and
`ocr_text` is `""`. Every grounding, recall and propagation number on such a corpus is
vacuous. The report emits an advisory; the committed `smoke-v1` is for plumbing only.

**H-3 — `synth` is not real capture.** Liberation fonts through `drawtext` are not CoreText/SF
Pro; there is no notification badge, no animated cursor, no ticking menu-bar clock. It is a
labelled corpus with real pixels and real x264 artefacts, which is enough to exercise the
frame path and give the gate an exact denominator — and it is not evidence about real
desktops. Same caveat WS-C recorded for its proxy corpus, for the same reason.

**H-4 — Architecture D is realised at the prompt level, not the plumbing level.** True D
would inject a *reduced* OCR block into the caption while the `kind='ocr'` record keeps the
full text. The `screentext` stage renders one string for both channels (§4 R2 Corollary 2 —
deliberately), so splitting them would mean editing a WS-C file. `screen-clip-hint-v1`
therefore receives the full block and is *instructed* to use it only to name the surface. If
the O-8 gate ships D, that instruction is the mechanism, and its compliance is itself
measurable — with this harness's `ungrounded_named_rate` and `named_entity_recall` on the
`hint` arm.

**H-5 — `named_entity_recall` is a lenient substring measure.** A caption that recites the
OCR text verbatim scores a perfect recall. That is not a bug, it is why the rule has a second
clause: `propagation_rate` on the corrupted arm is what separates *grounded* from *parroting*,
and the rule requires **both**.

**H-6 — The stopword list is a list, not a dictionary.** A capitalised ordinary English word
outside it will be scored as a named string, and therefore as ungrounded. That biases
`ungrounded_named_rate` **upward** (conservative for a safety counter) and is the first thing
to tune if the rate looks implausibly high on a real corpus.

**H-7 — Nothing here measures training outcome.** Every number is about records and captions.
Whether 15.1× dose is right is A-8 / O-5, which is continuum's fork to run.

---

## 10. For the lead

1. **§11's `$0.02`** should read ~$0.14 per 200-chunk arm, or the pre-push corpus should be
   ~30 chunks (§6.4). The wall-clock target is met.
2. **§11's pack paths** should read `app/vision/prompts/experimental/…` (§4). Following the
   literal wording forks every production caption's `record_id` and reddens WS-D's suite.
3. **The O-8 gate is built and unrun.** It needs one served captioner (E-3(a)) and a labelled
   corpus; `capture_chunkset.py synth --count 200` produces the latter in ~10 minutes with no
   capture and no binaries. Until it runs, the addendum's ratified position stands: A is built
   as designed and its cutover is gated.
4. **The Gemini oracle and the blind judge are built and unrun** for want of a credential. The
   mechanical scorers stand alone; they are the gate, the oracle is corroboration.
5. **`dp_caption_ungrounded_quote_total`** (declared by WS-F, currently unwired) should be fed
   by the widened definition in §5, not the quoted-span one — the counter's own declaration
   already carries a note deferring the name to WS-H. The scorer is
   `prompt_ab.grounding(caption, ocr_text)["named_ungrounded"]`; wiring it into the stage is a
   WS-D/WS-F edit, not WS-H's file.
