# O-2 bake-off — can an OCR model read 13 pt macOS UI text at 1728 px through CRF 28?

**Workstream:** WS-C (OCR), tab C1 (sidecar). **Date:** 2026-07-24.
**Deliverable status:** this report gates the production `VIDEO_OCR_BACKEND=ppocr` default.

---

## TL;DR / verdict

- On a **204-frame synthetic-proxy corpus** (3,026 key strings), the PP-OCR det+rec ONNX engine the
  sidecar serves clears the O-2 gate at 1728 px: **key-string recall 0.988 (lenient substring,
  key-pooled) / 1.000 (fuzzy), CER 0.070** — and passes but weakens at 1152 px (recall 0.869).
- **CRF 28 is not the bottleneck.** A raw-vs-CRF-28 ablation at 1728 px shows the codec costs
  essentially nothing (substring recall 0.981 → 0.983, fuzzy 1.000 → 1.000). Where accuracy drops it
  is the **downscale** (resolution), not the encoder — direct evidence for `VIDEO_OCR_FRAME_WIDTH=1728`
  (no resample) over a smaller OCR width.
- **BUT this does NOT satisfy the O-2 gate**, which is defined over *200 hand-labelled real macOS
  frames*. This corpus is synthetic (a headless build cannot capture a real mac screen). It measures
  the real failure mechanism — small UI text surviving a real x264 CRF-28 encode — with exact ground
  truth, but it cannot stand in for real CoreText rendering and real screen content, and is likely
  **optimistic**.
- **Recommendation (unchanged from O-2's own): ship `VIDEO_OCR_BACKEND=mock` as the production
  default.** Flip to `ppocr@1728` only after this same harness is run over the real-frame corpus and
  clears ≥ 0.85 recall / ≤ 0.10 CER. The harness + scorer are validated and run on real frames with
  **zero code change** (drop frames + `ground_truth.json` in the same format).

---

## 1. The question (O-2)

> Can PP-OCRv6 (or any current OCR model) read 13 pt macOS UI text at 1728 px through CRF 28? Every
> candidate is trained on *documents*; a dark-theme code editor with sub-pixel antialiasing and x264
> ringing is out of distribution.
> **Gate:** ≥ 0.85 key-string recall of ≥5-char strings, **lenient substring** matching (not exact
> equality — the POC measured 0.000 strict on a model that was reading correctly but wrapping the
> answer in prose), ≤ 0.10 CER on the focused region. Ship `mock` until it passes.

## 2. Corpus — honest provenance

**This is a synthetic-proxy corpus, not the 200 hand-labelled real frames O-2 requires.** A headless
build cannot capture, encode and hand-label a real macOS screen recording. Instead the corpus is
generated deterministically (`gen_corpus.py`, no RNG, no clock) so it is byte-reproducible and the
ground truth is exact.

- **204 frames**, 6 app archetypes × 34 variants: `ide-dark`, `gmail-light`, `slack-dark`,
  `terminal-dark`, `sheet-light`, `browser-article`. Dark and light themes; mono and proportional text.
- **3,026 key strings** (~14.8/frame): the ≥5-char *meaning* strings (subjects, senders, code
  identifiers, cell values, article body, commands) — chrome (menu bars, buttons) is deliberately
  excluded from ground truth, per O-2's "meaning first".
- **13 pt-equivalent body text.** Frames are rendered at 2× Retina (3456×2160) with body glyphs at
  ~28 px, so after the pipeline's downscale to 1728 a body em lands at ~14–15 px — the O-2 hard case.
- **Real x264 CRF-28 encode.** Each frame is scaled to the target width, encoded H.264 **CRF 28,
  yuv420p 4:2:0** (the 4:2:0 chroma subsampling is included because it is a real degrader of coloured
  text on coloured backgrounds), then a JPEG is extracted at `-q:v 2` — faithful to production
  (recording captures at `min(1728, iw)` and encodes H.264; DP's Pass B extracts JPEG at `-q:v 2`, D-04).

**What the proxy captures:** small-glyph survival through the real encoder at the real resolution;
dark/light themes; mono vs proportional; JPEG extraction artefacts; the recognizer's real behaviour
on English UI strings.

**What it does NOT capture (why it cannot replace the real-frame gate):**

- macOS renders with **CoreText + SF Pro** and sub-pixel antialiasing; these frames use Liberation
  fonts under FreeType, which almost certainly compresses *more cleanly* than real CoreText output —
  so results here are likely **optimistic**.
- No real screen noise: photos, thumbnails, gradients, notification badges, animated cursors, emoji,
  ligatures, translucency/vibrancy, retina @2× blur.
- Content is drawn from fixed pools, not the natural distribution of a user's screen.
- The engine is **PP-OCRv4** (see §9), a Chinese-centric model; its English-on-real-captures behaviour
  is exactly what only real frames can measure.

## 3. Method

- **Scorer** (`score.py`, unit-tested): a ≥5-char key string is *recalled* if — after case-folding,
  whitespace removal, and full-width→ASCII punctuation folding (an engine artefact, not a mis-read) —
  it is a **substring** of the frame's OCR text (`substring` tier), or its best windowed edit
  similarity is ≥ 0.85 (`fuzzy` tier, tolerating a couple of scattered OCR substitutions). `fuzzy`
  implies `substring`.
- **Micro vs macro.** Recall is reported two ways: **micro** = key-pooled = keys-recalled / total-keys
  (3,026 keys), and **macro** = mean of per-frame recall ratios (one vote per frame). They differ when
  frame key-density varies: `sheet-light` alone carries 42.7 % of all keys but only 16.7 % of the
  frames, so macro over-weights sparse frames. **The gate is scored on the micro (key-pooled) metric**
  — the literal "fraction of key strings recalled"; macro is shown alongside for context.
- **CER** is computed over the focused region only: reference = the focused block's text, hypothesis =
  the OCR regions whose centre falls inside the (width-scaled) focus box, in reading order.
- **Why leniency matters, shown concretely:** the real engine returns `class Screentextstage(Stage):`
  (a case flip) and `build_c2（chunk_id,pipeline_version)` (a full-width `（`). Exact equality scores
  both as misses; lenient scoring correctly counts them as reads. This is the exact 0.000-strict /
  high-lenient gap the POC hit.
- **Arms:** `ppocr@1728`, `ppocr@1152` — the det+rec ONNX the sidecar serves, in-process (same engine
  as the HTTP service). Re-runnable via `run_bakeoff.py`.

## 4. Results (gate: key-string recall ≥ 0.85 [micro, lenient], CER ≤ 0.10)

| arm | recall micro-sub **(gate)** | recall macro-sub | recall fuzzy (micro) | CER (focus) | verdict |
|---|---|---|---|---|---|
| **ppocr @ 1728** | **0.988** | 0.983 | 0.999 | **0.070** | **PASS** |
| ppocr @ 1152 | 0.869 | 0.912 | 0.890 | 0.074 | PASS (weaker) |
| Qwen3-VL-32B @ 1536 | — | — | — | — | **not run** (§6) |
| Qwen2.5-VL-32B @ 1536 | — | — | — | — | **not run** (§6) |

Per-archetype (recall = micro-substring):

| archetype | 1728 recall | 1728 CER | 1152 recall | 1152 CER |
|---|---|---|---|---|
| browser-article | 1.000 | 0.115 | 0.857 | 0.003 |
| gmail-light | 1.000 | 0.073 | 0.971 | 0.002 |
| ide-dark | 1.000 | 0.045 | 1.000 | 0.105 |
| sheet-light | 0.992 | 0.022 | 0.769 | 0.046 |
| slack-dark | 1.000 | 0.113 | 0.921 | 0.008 |
| terminal-dark | 0.908 | 0.050 | 0.956 | 0.277 |

Reading the table honestly:

- **At 1728 the aggregate clears both gate halves** (recall 0.988 ≥ 0.85, CER 0.070 ≤ 0.10). But the
  gate is a conjunction, and **at the per-archetype level two of six archetypes breach the CER half**:
  `browser-article` (0.115) and `slack-dark` (0.113), both just above 0.10 — long light-on-dark body
  paragraphs where a few dropped spaces/'·' accumulate. Recall clears everywhere (≥ 0.908). So "1728
  passes" is true at the aggregate, not uniformly per archetype.
- **At 1152 the weaknesses are real and localized:** `sheet-light` recall drops to 0.769 and
  `terminal-dark` CER rises to 0.277 — dense small text (spreadsheets, monospace terminals) is where a
  smaller OCR width bites. The aggregate micro recall (0.869) only just clears 0.85, and is *below* the
  macro figure (0.912) precisely because the worst, densest archetype is key-heavy.

## 5. The CRF-28 ablation — is the *codec* the problem?

`ablate_crf.py` reads the identical corpus at 1728 px two ways: raw (PNG downscaled, JPEG q95, no
video codec) vs through H.264 CRF 28.

| 1728 px | recall (substring) | recall (fuzzy) |
|---|---|---|
| raw (no codec) | 0.981 | 1.000 |
| **through CRF 28** | 0.983 | 1.000 |
| **codec cost** | **+0.002** | **≈ 0.000** |

**CRF 28 is not the bottleneck at 1728 px.** The O-2 anxiety — "x264 ringing on sub-pixel-antialiased
UI text" — does not materialise on this proxy; the recognizer reads text through CRF 28 as well as it
reads it raw. The accuracy that *is* lost (the @1152 arm) is lost to **resolution**, not the encoder.
This is the strongest single piece of support for the design's `VIDEO_OCR_FRAME_WIDTH=1728`
(no resample) decision. *(Caveat: real CoreText output may ring more under x264 than Liberation-font
output; the real-frame run must confirm this.)*

## 6. The VLM arms were not run

O-2 also names `Qwen3-VL-32B@1536` and `Qwen2.5-VL-32B@1536` (the A/B arm and the quality ceiling —
the POC measured Qwen2.5-VL-32B at 0.857 on this task and Qwen3-VL-32B at **0.143**). They require the
GPU VLM serving endpoint, which is E-3(a): `serve_vllm.sh` is a text-only MVP without image flags, and
the handoff records the serve loop as routinely down during the learn loop. Running them here would be
fabrication. They are recorded `status="not_run"` in `results.json` and should be run as the ceiling
once E-3(a) lands.

## 7. Interpretation & the pilot-gate decision

**What this proxy establishes (real decision value):**

1. PP-OCR det+rec on CPU is a **viable candidate** worth the real-frame spend — it is not obviously
   OOD-broken on small UI text; on clean synthetic frames it is near-perfect at 1728.
2. **1728 is the right OCR width**; 1152 measurably degrades dense/mono content (sheet recall 0.769,
   terminal CER 0.277).
3. **CRF 28 alone is not a blocker** — resolution dominates.
4. The **harness and scorer are validated** and ready for real frames with no code change.

**What it cannot establish:** the O-2 gate itself, which is defined over real macOS frames. The proxy
is likely optimistic (§2). **The gate is therefore NOT satisfied.**

**Decision (matches O-2's recommendation and the WS-C exit criteria):**

> Ship **`VIDEO_OCR_BACKEND=mock`** as the production default. The sidecar's `ppocr` backend is built,
> wired and validated, but the flip to `ppocr@1728` as the production default is gated on running this
> harness over **200 hand-labelled real macOS frames** (IDE, Gmail, Slack, browser article, terminal,
> spreadsheet) and clearing **≥ 0.85 micro key-string recall and ≤ 0.10 focus CER**. Also run the
> VLM arms as the ceiling once E-3(a) lands.

## 8. Reproduce / run on real frames

```bash
cd sidecars/ocr
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt     # one-time
cd bakeoff
../.venv/bin/python gen_corpus.py 34      # regenerate the synthetic corpus (deterministic)
../.venv/bin/python run_bakeoff.py        # -> results.json + console table
../.venv/bin/python ablate_crf.py         # -> ablation_crf.json
python3 -m pytest test_score.py -q        # scorer unit tests
```

To run on **real** frames: drop the frames under `frames_src/` and write `ground_truth.json` in the
same shape — `[{id, archetype, png, key_strings:[...>=5 char...], focus_bbox_2x:[x0,y0,x1,y1],
focus_text}]` (bbox in the source image's own pixel coords; the runner scales it) — then run
`run_bakeoff.py`. No code change.

## 9. Provenance

- **Engine:** PP-OCRv4 mobile det+rec ONNX on CPU via `rapidocr-onnxruntime==1.4.4`, ONNX Runtime
  **1.27.0**, `CPUExecutionProvider`.
  - `model_sha_det` `d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9`
  - `model_sha_rec` `48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b`
  - The design names **PP-OCRv6**; rapidocr-onnxruntime ships **v4**. The v4 result is a family floor.
    The engine is a file swap (`OCR_DET_MODEL`/`OCR_REC_MODEL`) — drop v5/v6 ONNX in and re-score; the
    sidecar's `/health` re-hashes automatically.
- **Encode:** ffmpeg 7.1, libx264, CRF 28, yuv420p, JPEG extract `-q:v 2`.
- **Corpus:** synthetic, 204 frames, deterministic (`gen_corpus.py`). Frame binaries are not committed
  (regenerable; house rule: no binaries). `ground_truth.json` and `results.json` are committed.
- **Throughput (measured, CPU, 4 threads):** OCR alone **~0.93–0.97 s/frame at 1728** (204-frame wall
  189 s / an isolated 60-frame OCR-only timing of 0.97 s/frame), ~0.55 s/frame at 1152; end-to-end
  including the (non-production) x264 encode ~1.17 s/frame. This is **above** the design's §7.1
  assumption of 0.6 s/frame for PP-OCRv6 — worth folding into the pilot CPU-budget sizing, and a real
  reason to prefer a v5/v6 or a quantized rec model if OCR CPU becomes the constraint.
