# HANDOFF — Data Processing Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** M0 + Processor seam + capture-M1 pair + **real audio pipeline beyond ASR**
(diarization · translation · acoustic-event captioning behind off-by-default backend switches;
mock headless + real pyannote/whisper/AST seams) + **real VIDEO pipeline landed (M3, WS-V):
keyframe extraction + captioning behind `VIDEO_BACKEND=mock|vlm` + per-keyframe timing hook + OCR
weave, verified with a genuine Qwen3-VL-8B run** — capture alpha still green (3 real clients) —
**72 tests** (38 + 19 audio + 11 video + 4 verification regressions) · **Last updated:** 2026-07-19 (integrator: independent verification round)

## Workstream index
| WS | What | Status | Working file | Owner session |
|---|---|---|---|---|
| B | M0 capture skeleton: C1 → ASR → C2 (`:8085`) | built, mock tests green | this dir (`app/`, `tests/`) | learn-loop M0 |
| B+ | Modality-agnostic **Processor seam** + image/video/text **stubs** | built, 24 tests green | `app/processing/`, `tests/test_processor_seam.py` | seam session |
| M1 | **Continuity detector** (`/continuity`) + **real ASR + VAD gate** + audio pipeline stubs | built + verified live 2026-07-18 | [handoff/ws-m1-continuity-asr.md](handoff/ws-m1-continuity-asr.md) | recording M1 lead |
| A | **Real audio pipeline beyond ASR**: diarization · translation · acoustic events (off-by-default `*_BACKEND` switches; mock headless + real pyannote/whisper/AST seams) | built, 57 tests green (38+19); real backends unrun seams | [handoff/ws-audio-pipeline.md](handoff/ws-audio-pipeline.md) | audio-pipeline lead |
| V | **Real VIDEO pipeline** (M3): ffmpeg keyframes → caption (`VIDEO_BACKEND=mock\|vlm`) + **per-keyframe timing hook** (OQ14a) + OCR weave (D8) | built + verified + reviewed; real **Qwen3-VL-8B** E2E; suite **68 green** (+11 video) | [handoff/ws-video-pipeline.md](handoff/ws-video-pipeline.md) | video-pipeline lead |

## Processor seam — how to add a modality (READ THIS before owning image/video/text)
The core (`app/main.py` `POST /ingest` + `app/pipeline.py` `build_c2`) is **modality-agnostic**:
validate C1 → dedup on `chunk_id` (now caches `chunk_id → [record_id,…]`) → pull blob →
**dispatch by `envelope.modality` to a Processor** → for **each** returned unit assemble a C2 and
`POST` it to `/context` → return `{ok, record_ids:[…]}`.

- **A modality = ONE disjoint file** in `app/processing/processors/` that subclasses
  `processing.base.Processor`, sets `modality` + `content_kind`, implements `pipeline_version(settings)`
  + `process(c1, blob, settings, span_seconds) -> list[ProcessedUnit]`, and is decorated with
  `@register` (`processing.registry`). The registry **auto-imports** every module in that package, so
  **you never edit a shared-core file** (not even a registry line) — just drop the file + a fixture.
- **`process` returns a LIST (≥1)**: audio/image/text → 1 unit; **video → many** (one keyframe →
  one unit, `discriminator = keyframe index`). `discriminator=''` is the 1:1 case.
- **`ProcessedUnit`** = `{content{kind,text,language?,segments?}, enrichments{speakers,faces,places,objects}, discriminator}`.
  `content.kind` ∈ frozen C2 enum `transcript|caption|ocr|text`. Emit `segments` already in C2 shape
  (absolute RFC3339); the core assembles content verbatim.
- **`record_id = sha256(chunk_id \0 pipeline_version [\0 discriminator])`** (discriminator folded in
  only when non-empty, so audio's 1:1 id is **byte-identical to the pre-seam v0 id** → reprocess is
  an idempotent upsert). Deterministic + distinct per keyframe.
- Stubs today (`image`/`video`/`text`) are **mock transforms** — real VLM/OCR/normalizer models
  replace only the plugin body. `audio` is the real mock-ASR path moved behind the seam unchanged.
- **`/ingest` response is `{ok, record_ids:[…]}`** (was `{ok, record_id}`; recording's
  capturer was updated + regression-tested 2026-07-10 — resolved).

## Current state
- **M0 built (`:8085`).** `POST /ingest` receives a pushed **C1** envelope → schema-validates it
  (frozen `c1_raw_stream_envelope.v0.json`, 422 on bad) → dedups on `chunk_id` (in-flight lock +
  processed map) → pulls the blob from storage `GET /raw/blobs?ref=` → runs ASR → builds a **C2** →
  `POST`s it to storage `/context/records` → returns `{ok, record_id}`. `GET /health` →
  `{ok, asr_backend}`.
- **ASR backend switch** `ASR_BACKEND=mock|faster_whisper`, **default mock** (no GPU, no torch).
  `faster_whisper` is LAZY-IMPORTED only when selected. `pipeline_version` stamped
  (`asr-mock-v0` / `asr-fw-v0`); `record_id = sha256(chunk_id \0 pipeline_version)` (hex, URL-safe,
  deterministic → idempotent `/context` upsert; version bump forks a new record).
- C2 provenance (`device_id/stream_id/chunk_id/blob_ref/modality`) + `t_start/t_end` carried from C1;
  `content.kind="transcript"`; segment offsets mapped to absolute RFC3339, clamped into the chunk
  span; `enrichments` present-but-empty; `speaker` null (no diarization in v0).
- Blob integrity: `blob_sha256` verified against pulled bytes (502 on mismatch); a missing/deleted
  blob → 502 and NOT marked done, so an at-least-once retry can still reprocess.
- **Tests: 9 passed** (isolated `.venv`, `ASR_BACKEND=mock`, storage faked via httpx `MockTransport`,
  FastAPI `TestClient` in-process — no real port bound). Covers: C1 validate + bad-C1 422; emitted
  C2 schema-valid + provenance carried; `record_id` determinism + version sensitivity; dedup
  (storage POSTed at most once); segment times within span; blob integrity/missing.
- **Capture M1 (2026-07-18, [handoff/ws-m1-continuity-asr.md](handoff/ws-m1-continuity-asr.md)):**
  - **Continuity detector** (`app/continuity.py`): every schema-valid `/ingest` (incl. dedup
    hits) is noted per `(stream_id, sequence, chunk_id)`; `GET /continuity` +
    `GET /continuity/{stream_id}` report max_sequence, merged seen-intervals, **missing**
    (incl. leading gap), duplicate_deliveries, sequence_conflicts. In-memory single-process
    (DedupStore posture). Recording's gap report cross-checks it live — "zero silent loss" is
    now checked on the C1 leg, not assumed.
  - **faster-whisper is STANDING** (in requirements.txt, lazy-imported; `ASR_BACKEND=mock`
    stays default). **VAD gate** (`ASR_VAD`, default on): Silero `vad_filter` before ASR —
    all-silence chunk → honest empty transcript (kills Whisper silence-hallucination).
    `PIPELINE_VERSION` → **`asr-fw-v1`** (version-forward fork; mock dialect untouched).
    **`ASR_LANGUAGE`** pins the ASR language (beta fleet: `en`) — auto-detect hallucinated
    other scripts on faint room audio in the first real phone session (runtime knob, no
    version fork).
  - **Audio pipeline shape** behind the seam: explicit stages asr → diarize → translate →
    acoustic_events; the last three are documented no-op stubs pinning their future contracts
    (speaker fill, `translation` unit, `acoustic` caption unit). Output today byte-identical.
  - Verified live by the lead session: real transcripts (`asr-fw-v1`) from phone-path segments
    in `/context`; empty transcript on silence; continuity reports consistent through
    clean/loss/dup drills. Tests: **38 passed** (24 + 14 new).
  - **Exercised end-to-end through the capture alpha (2026-07-19):** all three real capture
    clients (phone / Chrome extension / mac CLI) drove real media through `/ingest` — e.g. the
    extension run produced 7 real ASR transcripts of a captured tab's audio, the phone run 4 of
    room-mic audio — with `/continuity` cross-checked clean by recording's gap report each time.
    DP itself needed **no change** for the two new clients (they speak recording's client wire,
    which demuxes to the same C1 the phone already used). Suite unregressed at 38.

## Next
- **Async `/ingest` (ACK 202 + worker queue) — now the top architectural item.** DP processes
  chunks INLINE today; the 2026-07-19 verification round confirmed a fully-loaded chunk
  (real ASR + diarization + VLM captions) can lawfully exceed the producer's delivery
  timeout, making recording retry into DP's in-flight lock. Fleet-mitigated for now
  (`RECORDING_HTTP_TIMEOUT=120` in deploy/learn.env); the real fix — ack fast, process on a
  worker, rely on `chunk_id` dedup + `record_id` determinism for retry safety — was already
  sketched in the founders' M1 agenda and is ready to be its own slice.
- **Real audio pipeline stages — BUILT** (WS A, [handoff/ws-audio-pipeline.md](handoff/ws-audio-pipeline.md)):
  diarization / translation / acoustic-event captioning now fill their stubs behind
  off-by-default `DIARIZE_BACKEND` / `TRANSLATE_BACKEND`+`TRANSLATE_TARGET` / `ACOUSTIC_BACKEND`
  switches (`app/audio/`). Default output byte-identical (mock dialect untouched, 38-baseline
  green). Diarization forks the audio `pipeline_version` (`+diar-*`); translation + acoustic are
  additive `discriminator`-tagged sidecar records. Mock backends exercised headless; the real
  pyannote/whisper/AST backends are **correct-by-inspection seams, unrun here** — **remaining:
  smoke-test each on node-7** (GPU + HF-gated pyannote) before trusting a real run. VAD-cut
  chunk boundaries mean chunks arrive pause-aligned — revisit cross-chunk stitching after
  real-data experience.
- Continuity tracker durability (survives restart) + `sequence_conflicts` surfacing beyond the
  warning log, when multi-replica/serious-scale arrives.
- ~~Real video processor~~ **DONE (2026-07-19, WS-V)** — `processors/video.py` now runs a real
  keyframe pipeline (ffmpeg scene-change selection) behind `VIDEO_BACKEND=mock|vlm`; each
  keyframe gets its own C2 sub-span via the additive `ProcessedUnit.t_start/t_end` hook (OQ14a,
  honored in `build_c2`, no C2 schema change); OCR woven into the caption (D8). Mock stays the
  headless default; the `vlm` backend (httpx → OpenAI-compatible VL endpoint) was exercised
  genuinely against a locally-served Qwen3-VL-8B. See [handoff/ws-video-pipeline.md](handoff/ws-video-pipeline.md).
  **Independent verification round (2026-07-19, integrator session):** the headline claims of
  BOTH slices held under adversarial checking (audio default proven byte-identical by hash vs
  the pre-slice tree; video record_id stability, sub-span math, webm/mp4 decode all confirmed
  empirically); 4 confirmed video-side defects fixed + regression-tested (**DP suite 72**):
  vlm placeholder-emission on undecodable chunks now raises for redelivery, partition-invariant
  head/tail pinning, lenient vision-config numerics. Detail + accepted caveats in both ws
  worklogs; live E2E re-verified on the restarted fleet.
- Real **image** processor is still a mock stub (image build owns it, incl. the OQ14b bbox
  `content.regions[]` C2-additive field the video OCR pass will also want).
- text/image real pipelines per CHARTER M-order (video landed).
- **D9 (2026-07-09) ratified — centralized observability:** this service now owes a `/metrics` endpoint + Grafana dashboard JSON (throughput/queue depth, per-stage + C8 latency, enrichment counts). On the backlog — see CHARTER.md § Scope (Observability) + deliverable **M8**.
