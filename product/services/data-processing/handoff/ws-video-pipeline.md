# WS — DP VIDEO pipeline: real keyframe extraction + captioning + per-keyframe timing

> The video modality's half of CHARTER M3 (VidProc): replace the mock keyframe-caption stub
> with a real pipeline behind a `VIDEO_BACKEND=mock|vlm` switch, add the sanctioned additive
> per-keyframe timing hook (CHARTER OQ14a), and weave OCR into the caption (D8). Ran concurrently
> with the AUDIO session (`svc/dp-audio-pipeline`) — only additive, non-overlapping touches to the
> two shared-core files.

**Status:** built + verified + adversarially reviewed — full suite **49 passed** (38 pre-existing
green + 11 new); mock loop verified headless E2E out-of-process; **real Qwen3-VL-8B run captioned
real keyframes end-to-end** (verbatim OCR woven, distinct per-keyframe sub-spans); a 6-dimension
adversarial review surfaced 3 real idempotency/robustness issues, all fixed + regression-tested ·
**Owner session:** video-pipeline lead · **Last updated:** 2026-07-19

---

## What shipped

### 1. Real keyframe pipeline (CHARTER priority 1) — `VIDEO_BACKEND=mock|vlm`
- **New `app/vision/` namespace** (the video analogue of `app/asr/`):
  - `config.py` — `VisionSettings` read from `os.getenv` (`VIDEO_*`), so the shared-core
    `app/config.py` is **untouched** (no cross-session merge surface). Knobs + defaults:
    `VIDEO_BACKEND=mock`, `VIDEO_SCENE_THRESHOLD=0.30`, `VIDEO_KEYFRAME_INTERVAL_S=3.0`,
    `VIDEO_MAX_KEYFRAMES=8`, `VIDEO_MIN_KEYFRAMES=1`, `VIDEO_SAMPLE_FPS=2.0`,
    `VIDEO_FRAME_MAX_WIDTH=768`, `VIDEO_VLM_URL`, `VIDEO_VLM_MODEL`, `VIDEO_VLM_API_KEY`,
    `VIDEO_VLM_TIMEOUT=120`, `VIDEO_VLM_MAX_TOKENS=256`, `VIDEO_OCR_RECORDS=0`.
  - `frames.py` — **backend-independent** keyframe extraction via the **single canonical decoder,
    ffmpeg** — scene-change detection (`select='gt(scene,thr)',metadata=print`) unioned with a
    duration-driven **uniform base grid**, then JPEG extraction + downscale. ffmpeg absent / bytes
    don't decode → returns `[]` → synthetic fallback (the SAME result on every worker). Selection is
    **deterministic** given bytes+settings, and record identity is the keyframe's selected-times
    index — both required by the C2 idempotency contract (see the review note below). No in-process
    OpenCV fallback: a second decoder's scene metric differs from ffmpeg's, so a heterogeneous fleet
    would select different keyframes for identical bytes under one `pipeline_version` — a silent
    non-idempotent upsert. One decoder on purpose.
  - `result.py` — neutral `Keyframe` (JPEG + chunk-relative `[t_offset, t_end_offset)` sub-span;
    `image_jpeg=None` marks a *synthetic* timing-less keyframe) and `KeyframeCaption`
    (caption + OCR text kept separate).
  - `__init__.py` `select(vs)` — the `mock|vlm` captioner switch. `mock` DEFAULT (no import cost);
    `vlm` **late-bound** (imported only when selected).
  - `mock.py` — canned captions, `PIPELINE_VERSION="vidproc-mock-v0"` (== the pre-existing stub's
    dialect, so the mock record_ids never fork). No GPU / no network / ignores pixels.
  - `vlm.py` — the real captioner, `PIPELINE_VERSION="vidproc-vlm-v0"`. **Pure httpx** against an
    OpenAI-compatible `/v1/chat/completions` (the same wire inference speaks to the Qwen3-VL on the
    GPU node) — so it needs NO heavy Python dep and no GPU in-process. Sends each keyframe as a
    base64 `image_url` data part + a prompt asking for a factual caption AND verbatim on-screen text
    (the D8 OCR pass folded into one VL call). `temperature=0` (greedy) keeps captions stable across
    reprocessings. A request error propagates → chunk not marked done → at-least-once retry.
- **`app/processing/processors/video.py` rewritten** (seam unchanged): extract keyframes → caption
  via the selected backend → weave OCR into the caption → one `ProcessedUnit` per keyframe, each
  with its own sub-span. `discriminator=str(idx)`; `video.PIPELINE_VERSION` still resolves to
  `vidproc-mock-v0` (the seam tests' handle). **Synthetic fallback**: undecodable bytes (the seam's
  47-byte fixture, or a box with no ffmpeg) → `SYNTHETIC_KEYFRAMES=3` timing-less keyframes
  carrying the chunk span verbatim — **byte-identical to the old stub**, so the 38 stay green with
  or without video tooling installed.

### 2. Per-keyframe timing hook (CHARTER priority 2 — the ONE sanctioned additive core edit)
- `app/processing/base.py`: `ProcessedUnit` gains optional `t_start: str|None = None`,
  `t_end: str|None = None`. Additive; every existing modality leaves them `None`.
- `app/pipeline.py` `build_c2`: `t_start = unit.t_start if not None else c1["t_start"]` (same for
  `t_end`). **No C2 schema change** — C2 already carries per-record timestamps. When a keyframe sets
  a sub-span, its record gets its own `[t_start,t_end)` instead of colliding with siblings on the
  shared chunk span (storage's `(user_id, t_start)` index). Defaulting to the C1 span keeps
  audio/image/text and the mock video fallback **byte-identical** — the 38 pre-existing tests are
  the proof. The outer boundaries reuse the C1 span strings verbatim, so the union of a chunk's
  keyframe sub-spans exactly equals the declared chunk span (no tz-format / float drift at edges).

### 3. OCR (CHARTER priority 3, stretch — D8)
- The captioner returns on-screen text separately; the processor **weaves it into the caption**
  written to /context (so the user model learns text from the description target, not by reading
  pixels at inference — D8). Optionally (`VIDEO_OCR_RECORDS=1`) also emits a distinct
  `content.kind="ocr"` record per keyframe (`discriminator="{idx}:ocr"`), default OFF.
  **Structured bbox geometry is out of frozen scope** — a later additive-C2 field owned by the
  image build (CHARTER OQ14b).

## Contract discipline
- **C1/C2 FROZEN — additive only.** The timing hook needed no C2 change. Every produced C2 (mock,
  vlm, ocr-record) validates against the frozen `c2_processed_record.v0.json`.
- **File ownership:** touched only `processors/video.py`, the new `app/vision/*`, additive
  `base.py` + `pipeline.py`, `requirements-video.txt`, and NEW `tests/test_video_pipeline.py` +
  `tests/fixtures/video_scenes.*`. Did **not** touch config.py, requirements.txt, main.py,
  registry.py, models.py, schemas.py, asr/, audio/, or the audio/image/text processors — no merge
  surface with the concurrent AUDIO session.

## Tests (tests/test_video_pipeline.py — 11 new, suite now 49)
- New fixture is valid C1; sha256/bytes self-consistent.
- Additive hook: `build_c2` carries C1 span when the unit sets none; honors a unit sub-span (still
  C2-valid, no schema change).
- Frame extraction: deterministic (same bytes → same offsets + same JPEG bytes), sub-spans strictly
  increasing + contiguous, partition `[0,span]`. (Skips if ffmpeg absent.)
- Stable keyframe identity: a transient per-frame extraction drop keeps survivors' indices (→
  discriminators → record_ids) stable, so reprocessing is an idempotent upsert, not a renumber.
- vlm malformed-response guard: a 200 with no `choices` raises a clear `ValueError` (chunk retried),
  never a silently-degraded caption in `/context`.
- E2E ingest (mock): real fixture fans out to N≥2 keyframe records with **distinct** per-keyframe
  sub-spans partitioning the chunk span, deterministic record_ids, distinct captions, OCR woven.
- Re-ingest idempotent (same ids, no dup /context writes, blob pulled once).
- Synthetic fallback: undecodable bytes → 3 shared-span caption units (`t_start/t_end=None`).
- `VIDEO_OCR_RECORDS=1` → one `kind='ocr'` record per keyframe, sharing its caption's sub-span.
- vlm backend wire (fake VL server via httpx MockTransport, no GPU): base64 `image_url` sent,
  `temperature=0`, caption+OCR parsed and woven, `pipeline_version=vidproc-vlm-v0`.

## Verification runs
- **Full suite:** `ASR_BACKEND=mock python3 -m pytest -q` → **47 passed** (38 pre-existing + 9 new).
- **Mock headless E2E (out-of-process):** real uvicorn DP (`VIDEO_BACKEND=mock`) + a real HTTP
  fake-storage; PUT the real `video_scenes.mp4` blob, POST its C1 → 4 keyframe C2 records in
  `/context` with distinct contiguous sub-spans (`00:00→02.667→05.333→06.0→08`, boundaries
  verbatim), OCR woven, idempotent re-ingest. No GPU/torch.
- **REAL VLM run (genuine, on this box):** served the cached **Qwen/Qwen3-VL-8B-Instruct** with
  **vLLM 0.24.0** (conda `vllm-cu13`, TP=1, one H100, `HF_HUB_OFFLINE=1`) on an OpenAI-compatible
  `:8100`; pointed `VIDEO_BACKEND=vlm VIDEO_VLM_URL=http://127.0.0.1:8100
  VIDEO_VLM_MODEL=Qwen/Qwen3-VL-8B-Instruct` and drove `video_scenes.mp4` through `/ingest`.
  Qwen3-VL-8B captioned all **4 real keyframes in ~3.2s**, reading each scene's on-screen text
  **verbatim** and weaving it in, e.g.:
  - kf0 `[00:00→02.667]` — "A solid blue background with centered white text describing a desk
    setup. On-screen text: 'DESK laptop and coffee'."
  - kf2 `[05.333→06.0]` — "A terminal window displays a command prompt with the text 'TERMINAL
    make deploy' in green on a solid red background. On-screen text: 'TERMINAL make deploy'."
  Records written to `/context` as `vidproc-vlm-v0`; server torn down afterwards (GPU freed).
  The node-7 Qwen3-VL-32B is the production endpoint — identical wire, just a different
  `VIDEO_VLM_URL`.

## Next / handoffs
- **bbox geometry (C2-additive)** for keyframe OCR is the image build's to freeze (OQ14b); when it
  lands, the vlm backend can populate `content.regions[]` or `enrichments.text_regions[]` (the vlm
  prompt would ask for boxes) — additive, no break.
- **Cost/operating point** (CHARTER research Q7): `VIDEO_KEYFRAME_INTERVAL_S` + `VIDEO_MAX_KEYFRAMES`
  are the cost dials; real per-screen-hour numbers want a pilot pass at the chosen VL tier.
- **Point at node-7:** for real fleet runs set `VIDEO_VLM_URL` at the standing Qwen3-VL-32B (see
  `services/inference/serve_vllm.sh`, adding the multimodal flags) instead of a local 8B.
- **Static-text dedup across frames** (CHARTER OQ10 residual): adjacent keyframes on a static screen
  re-OCR the same text; a cross-keyframe dedup pass is a later refinement.

## Worklog
- 2026-07-19 — built the `app/vision/` namespace + rewrote `processors/video.py`; added the additive
  `ProcessedUnit.t_start/t_end` hook + `build_c2` honoring; ffmpeg keyframe extraction with
  a deterministic uniform-grid∪scene-cut selector; mock + httpx-vlm captioners; D8 OCR weave +
  optional `kind='ocr'` records. New `tests/test_video_pipeline.py` (9) + `video_scenes.*` fixture.
- 2026-07-19 — verified: 47 passed; out-of-process mock headless E2E; and a **genuine Qwen3-VL-8B**
  run (vLLM 0.24.0, one H100, offline) captioning real keyframes E2E with verbatim OCR. GPU freed.
- 2026-07-19 — **6-dimension adversarial review** (contract-frozen / mock-byte-identical /
  headless-latebind / determinism / file-ownership / correctness; each finding independently
  refuted-or-confirmed). 4 dimensions clean; 3 confirmed real issues fixed + regression-tested:
  (1) **[medium]** a transient per-frame extraction drop re-indexed survivors → shifting record_ids
  → non-idempotent; fixed by keying identity to the deterministic selected-times index (a dropped
  frame no longer renumbers survivors). (2) **[low]** the OpenCV fallback selected a *different*
  keyframe set than ffmpeg under the same `pipeline_version` (empirically 3 vs 4 keyframes on the
  fixture) → fleet non-idempotency; fixed by **removing the second decoder** — ffmpeg is now the
  single canonical decoder, no-ffmpeg → synthetic (same everywhere). (3) **[low]** a 200 VLM
  response lacking `choices` raised an opaque KeyError; fixed to a clear `ValueError` (chunk
  retried, no degraded caption persisted). Suite → **49 passed**.
