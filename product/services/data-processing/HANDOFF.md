# HANDOFF — Data Processing Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** built · **765 tests** (+21 skipped) · *Last updated:* 2026-07-25 (WS-VC
integration, branch `svc/video-clip`)

**Where we are.** The service ingests C1 chunks and emits C2 records for every modality, behind a
stage-graph pipeline where each processing step is a drop-in file. Audio is real end to end. Video
has two paths: the shipped per-keyframe legacy default, and the screen-video clip path built
alongside it and waiting on cutover gates. The capture alpha is green on three real clients.

- **Audio** — ASR with a VAD gate, plus diarization, translation and acoustic-event captioning
  behind off-by-default backend switches. All three smoke-tested green on node-7.
- **Video** — keyframe extraction and captioning (`VIDEO_BACKEND=mock|vlm`), plus the clip path
  behind `VIDEO_PIPELINE=clip`; the `keyframe` default is byte-identical to what shipped.
- **Ingest** — async behind `INGEST_ASYNC`, off by default and byte-identical inline; a durable
  journal closing kill-recovery and restart-amnesia; opt-in subprocess isolation.
- **Observability** — D9 `/metrics` plus a Grafana dashboard (M8).

Detail per workstream is in the index below; finished work is in
[handoff/worklog.md](handoff/worklog.md).

## Workstream index
| WS | What | Status | Working file | Owner session |
|---|---|---|---|---|
| B | M0 capture skeleton: C1 → ASR → C2 (`:8085`) | `built`, mock tests green | this dir (`app/`, `tests/`) | learn-loop M0 |
| B+ | Modality-agnostic **Processor seam** + image/video/text stubs | `built`, 24 tests green | `app/processing/`, `tests/test_processor_seam.py` | seam session |
| M1 | **Continuity detector** (`/continuity`) + real ASR + VAD gate + audio pipeline stubs | `built`, verified live 2026-07-18 | [ws-m1-continuity-asr](handoff/ws-m1-continuity-asr.md) | recording M1 lead |
| A | **Real audio pipeline beyond ASR**: diarization · translation · acoustic events | `built`, 57 tests green; real backends since smoked | [ws-audio-pipeline](handoff/ws-audio-pipeline.md) | audio-pipeline lead |
| V | **Real video pipeline** (M3): ffmpeg keyframes → caption, per-keyframe timing, OCR weave | `built`, reviewed; real Qwen3-VL-8B E2E; superseded for screen ([↓](#ws-v--superseded-for-screen-content)) | [ws-video-pipeline](handoff/ws-video-pipeline.md) | video-pipeline lead |
| AO | **Async `/ingest`** (M7-early) + D9 `/metrics` and dashboard (M8) + node-7 audio smoke | `built`, reviewed; DP 98 green; recording seam updated | [ws-async-observability](handoff/ws-async-observability.md) | async-observability lead |
| SG | **DP v1**: the durable ingest journal + the stage-graph pipeline | `built`, reviewed; DP 127 green; real backends re-validated on node-7 | [ws-dp-stage-graph](handoff/ws-dp-stage-graph.md) | async-observability lead (v1) |
| VC | **Screen-video clip path** behind `VIDEO_PIPELINE=clip` ([↓](#ws-vc--the-screen-video-clip-path)) | `built` + integrated; 8 workstreams merged to `svc/video-clip`; DP suite 765 (+21 skip) | [ws-video-clip](handoff/ws-video-clip.md) | WS-VC lead + 8 build sessions |
| P3 | **Phase-3 dogfood support** (continuum-led): `app/stages/audio/injected_caption.py`, off unless `INJECT_CAPTION_BACKEND=index` | landed via continuum's Phase-3 build (`388ae32`); DP default byte-identical | [ws-phase3-dogfood](../continuum/handoff/ws-phase3-dogfood.md) | continuum Phase-3 session |
| H | **Hardening**: findings #3/#6/#7 closed, plus `INGEST_ISOLATION=subprocess` ([↓](#ws-h--hardening)) | `built`, workflow-reviewed; DP 163 green; merged to `main` (`5350f7a`) 2026-07-21 | [ws-dp-hardening](handoff/ws-dp-hardening.md) | DP hardening session |

### WS-V — superseded for screen content

**In one line.** The real video pipeline (M3) still ships as the default, but its screen-content
half has been superseded.

- **Shape** — ffmpeg keyframes → caption (`VIDEO_BACKEND=mock|vlm`), plus a per-keyframe timing
  hook (OQ14a) and an OCR weave (D8). Suite *68 green* (+11 video).
- **Watch out for** — its §3 D8 caption/OCR weave and the keyframe-per-record shape are
  *superseded for screen content by WS-VC*.
- They are kept as the shipped, gated `VIDEO_PIPELINE=keyframe` legacy path.

### WS-VC — the screen-video clip path

**In one line.** A screen-video chunk becomes one caption record and one OCR record, instead of
four to eight per-keyframe records.

**Shape** — three stages, behind `VIDEO_PIPELINE=clip` (default `keyframe`, byte-identical).

- `clipprep(5)` — VFR-safe ffmpeg frame prep plus a delta gate.
- `screentext(15)` — the CPU OCR sidecar, emitting its own `kind='ocr'` record.
- `clipcap(20)` — one multi-image VLM call, OCR injected per D-09, emitting one `kind='caption'`
  record.

**Rules**

- The **prompt pack** is versioned (`app/vision/prompts/`) and its digest feeds `pipeline_version`.
- `cfg_tag` means a knob change forks `record_id`.
- The **record-vs-mutation law** is enforced in CI (`tests/test_emission_law.py`) and at
  registration (`stage.py`'s R1 raise).
- The **offline eval harness cannot write `/context`**, by construction.
- The **OCR sidecar** is co-located (`sidecars/ocr/`) with its own venv.

**Watch out for**

- Every workstream was lead-verified: the law was mutation-tested, discovery/resolve/E2-raise were
  independently rerun, and a masked order/registration bug in the OCR seam was caught and returned
  before merge.

### WS-H — hardening

**In one line.** The three tracked stage-graph review findings are closed by construction, and a
poison chunk can no longer take down the service.

- **Findings #3/#6/#7 closed.** SlotView capability slot-ownership; mutate `writes` and overlap
  chaining with a chain-order dialect; permit-at-dispatch fairness, with head-of-line blocking
  dead.
- `INGEST_ISOLATION=subprocess` — a killable per-chunk child, so a poison chunk's blast radius
  is 1 chunk, and a drain SIGKILL reclaims ghosts.
- Also carries the milestone eval and the sync-retirement recommendation: **keep inline for C8**.

## Reference material in this service
- **[readings/](readings/)** — founder-supplied research notes, cited by the WS-VC design:
  *Choosing Frame Rate and Resolution for Screen Recording of Desktop Use* (the 5 fps floor /
  10–15 fps balanced operating point for desktop activity, and the 1080p legibility argument) and
  *OCR processing — thoughts* (the event-driven, region-level, temporally-aggregated OCR design
  that [handoff/ws-video-clip.md](handoff/ws-video-clip.md) D-07 implements).
- Read both before touching sampling rates or the OCR gate.

## Processor seam — how to add a modality (READ THIS before owning image/video/text)
The core (`app/main.py` `POST /ingest` + `app/pipeline.py` `build_c2`) is **modality-agnostic**:
validate C1 → dedup on `chunk_id` (now caches `chunk_id → [record_id,…]`) → pull blob →
**dispatch by `envelope.modality` to a Processor** → for *each* returned unit assemble a C2 and
`POST` it to `/context` → return `{ok, record_ids:[…]}`.

- **A modality is one disjoint file** in `app/processing/processors/` that subclasses
  `processing.base.Processor`, sets `modality` + `content_kind`, implements `pipeline_version(settings)`
  + `process(c1, blob, settings, span_seconds) -> list[ProcessedUnit]`, and is decorated with
  `@register` (`processing.registry`). The registry *auto-imports* every module in that package, so
  *you never edit a shared-core file* (not even a registry line) — just drop the file + a fixture.
- **`process` returns a list** (≥1): audio/image/text → 1 unit; *video → many* (one keyframe →
  one unit, `discriminator = keyframe index`). `discriminator=''` is the 1:1 case.
- `ProcessedUnit` = `{content{kind,text,language?,segments?}, enrichments{speakers,faces,places,objects}, discriminator}`.
  `content.kind` ∈ the C2 enum `transcript|caption|ocr|text`. Emit `segments` already in C2 shape
  (absolute RFC3339); the core assembles content verbatim.
- `record_id = sha256(chunk_id \0 pipeline_version [\0 discriminator])` (discriminator folded in
  only when non-empty, so audio's 1:1 id is **byte-identical to the pre-seam v0 id** → reprocess is
  an idempotent upsert). Deterministic + distinct per keyframe.
- Stubs today (`image`/`video`/`text`) are **mock transforms** — real VLM/OCR/normalizer models
  replace only the plugin body. `audio` is the real mock-ASR path moved behind the seam unchanged.
- **`/ingest` response is `{ok, record_ids:[…]}`** (was `{ok, record_id}`; recording's
  capturer was updated + regression-tested 2026-07-10 — resolved).

## Current state
- **M0 built (`:8085`).** `POST /ingest` receives a pushed *C1* envelope → schema-validates it
  (`c1_raw_stream_envelope.v0.json`, 422 on bad) → dedups on `chunk_id` (in-flight lock +
  processed map) → pulls the blob from storage `GET /raw/blobs?ref=` → runs ASR → builds a *C2* →
  `POST`s it to storage `/context/records` → returns `{ok, record_id}`. `GET /health` →
  `{ok, asr_backend}`.
- **ASR backend switch** `ASR_BACKEND=mock|faster_whisper`, *default mock* (no GPU, no torch).
  `faster_whisper` is lazy-imported only when selected. `pipeline_version` stamped
  (`asr-mock-v0` / `asr-fw-v0`); `record_id = sha256(chunk_id \0 pipeline_version)` (hex, URL-safe,
  deterministic → idempotent `/context` upsert; version bump forks a new record).
- C2 provenance (`device_id/stream_id/chunk_id/blob_ref/modality`) + `t_start/t_end` carried from C1;
  `content.kind="transcript"`; segment offsets mapped to absolute RFC3339, clamped into the chunk
  span; `enrichments` present-but-empty; `speaker` null (no diarization in v0).
- Blob integrity: `blob_sha256` verified against pulled bytes (502 on mismatch); a missing/deleted
  blob → 502 and NOT marked done, so an at-least-once retry can still reprocess.
- **Tests: 9 passed** (isolated `.venv`, `ASR_BACKEND=mock`, storage faked via httpx
  `MockTransport`, FastAPI `TestClient` in-process — no real port bound).
- Covers: C1 validate + bad-C1 422; emitted C2 schema-valid + provenance carried; `record_id`
  determinism + version sensitivity; dedup (storage POSTed at most once); segment times within
  span; blob integrity/missing.
- **Capture M1 (2026-07-18, [handoff/ws-m1-continuity-asr.md](handoff/ws-m1-continuity-asr.md)):**
  - **Continuity detector** (`app/continuity.py`): every schema-valid `/ingest` (incl. dedup hits)
    is noted per `(stream_id, sequence, chunk_id)`; `GET /continuity` + `GET
    /continuity/{stream_id}` report max_sequence, merged seen-intervals, *missing* (incl. leading
    gap), duplicate_deliveries, sequence_conflicts.
  - In-memory single-process (DedupStore posture).
  - Recording's gap report cross-checks it live — "zero silent loss" is now checked on the C1 leg,
    not assumed.
  - **faster-whisper is standing** (in requirements.txt, lazy-imported; `ASR_BACKEND=mock` stays
    default).
  - *VAD gate* (`ASR_VAD`, default on): Silero `vad_filter` before ASR — all-silence chunk →
    honest empty transcript (kills Whisper silence-hallucination).
  - `PIPELINE_VERSION` → `asr-fw-v1` (version-forward fork; mock dialect untouched).
  - `ASR_LANGUAGE` pins the ASR language (beta fleet: `en`) — auto-detect hallucinated other
    scripts on faint room audio in the first real phone session (runtime knob, no version fork).
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
  - DP itself needed *no change* for the two new clients (they speak recording's client wire,
    which demuxes to the same C1 the phone already used).
  - Suite unregressed at 38.

## Next
- **The screen-video clip path (WS-VC) is BUILT and integrated** (2026-07-25) —
  [handoff/ws-video-clip.md](handoff/ws-video-clip.md).
- All 8 workstreams landed and merged to `svc/video-clip`; DP suite *765* (+21 skip).
- Shape: behind `VIDEO_PIPELINE=clip`, a video chunk yields *exactly 2 C2 records* (`caption` +
  `ocr`, fixed discriminators, C1 span verbatim) instead of 4–8 per-keyframe records —
  `clipprep(5)` → `screentext(15)` → `clipcap(20)`. The default (`keyframe`) stays byte-identical
  to the shipped legacy path (`vidproc-*-v0`).
- Every lead-verified (mutation-tested the emission law; a masked order/registration bug in the
  OCR seam was caught and returned before merge).
- *The build is done; what remains are the cutover gates (below) and small follow-ups — none block
  the merge to main.*
- **Gates before flipping `VIDEO_PIPELINE=clip` on for a real user** (all documented in
  [handoff/ws-video-clip.md](handoff/ws-video-clip.md); the code ships `VIDEO_OCR_BACKEND=mock` +
  `keyframe` default until these clear):
  - **O-2** — ~200 hand-labelled real macOS frames clearing the OCR bar (≥0.85 recall / ≤0.10 CER)
    before `VIDEO_OCR_BACKEND=ppocr`. The bake-off harness ships (`sidecars/ocr/bakeoff/`) and cleared
    a *synthetic* proxy; the real capture doesn't exist in a headless build.
  - **O-8** — the blind-vs-injected A/B against a real VLM (`scripts/prompt_ab.py`, pre-registered:
    ship injection iff named-entity-recall lift > 0.25 ∧ propagation < 0.10, else the hint arm). Built
    + stub-validated; returns *undecided* under a mock captioner (needs E-3(b)).
  - **E-2** (storage retraction) *or* a fresh `user_id` — the dev store holds only mock-dialect
    records, so a fresh-user cutover is free once; after the pilot runs, E-2 is a hard prerequisite.
  - **E-3(b)** — a captioner endpoint off the user-facing `:8000` (founders' call, closes OQ3).
- **Escalations (in [handoff/ws-video-clip.md](handoff/ws-video-clip.md) §10; the two `cutover` ones
  are also on the founders' board):** *E-1* recording `--segment-seconds 10→60` (largest cost lever;
  joint with DP-audio); *E-2* storage kind-aware `DELETE /context/records` (*cutover*); *E-3(a)* —
  *demoted to "recommended, not required":* WS-A's probe verified vLLM 0.24.0 defaults
  `--limit-mm-per-prompt` to 999 (not 1) and clamps nothing at 768×480, so the K≤12 multi-image call
  validates on the *unmodified* `serve_vllm.sh`; the flags are determinism pins now; *E-3(b)*
  distinct captioner endpoint (*founders' call*, OQ3); *E-4* continuum per-fragment local timestamps
  in `_render_block` (*"at 13:04 the user was writing…" is unreachable without it, C1 carries no
  timezone*) + OCR-dedup + renderer ordering + recipe fork; *E-5* the parked additive C2 edit
  (`enrichments.text_regions[]` + root `quality{}`), diff written, *not taken* (no consumer); *E-6*
  recording auto-retry `failed` segments.
- **Small follow-ups (non-blocking):** *wire* the production `dp_caption_ungrounded_quote_total`
  counter from double-quoted spans to all named ≥4-char strings (WS-VC/H found mock captions carry
  *zero* double-quotes, so the quote-only counter measures an empty set; the widened scorer already
  lives in `scripts/prompt_ab.grounding`); collapse `app/vision/ocr/assemble.ocr_cap`'s local stub to
  import `app/vision/budget.ocr_cap`; the `acoustic` R1 latent hole (retro-fit `+ac-*` when a real
  backend provides a slot — audio owner's call, pinned red by the law test).

Finished items that used to sit on this board are retired verbatim in
[handoff/worklog.md](handoff/worklog.md) — the async `/ingest` slice, the durable journal, the
stage-graph pipeline, the D9 metrics slice, the real audio and video pipelines, and the hardening
slice, each with its dates, commits and residuals intact.
