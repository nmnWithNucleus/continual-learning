# HANDOFF — Data Processing Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** M0 built + modality-agnostic Processor seam landed (mock loop green) · **Last updated:** 2026-07-10

## Workstream index
| WS | What | Status | Working file | Owner session |
|---|---|---|---|---|
| B | M0 capture skeleton: C1 → ASR → C2 (`:8085`) | built, mock tests green | this dir (`app/`, `tests/`) | learn-loop M0 |
| B+ | Modality-agnostic **Processor seam** + image/video/text **stubs** | built, 24 tests green | `app/processing/`, `tests/test_processor_seam.py` | seam session |

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
- **`/ingest` response is now `{ok, record_ids:[…]}`** (was `{ok, record_id}`). NOTE for the
  integrator: recording's live `capturer.py` reads `ack.get("record_id")` — update it to
  `record_ids` (recording's own unit tests fake the old shape, so they don't flag this).

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
- Not run here: the `faster_whisper` path (scripted-but-unrun — mock is the only path exercised) and
  a live cross-service run against real storage `:8085`↔`:8083` (integrator owns live ports).

## Next
- Integrator: wire recording `:8084` → data-processing `:8085` → storage `:8083` and drive one real
  chunk end to end (exit criterion in `../../handoff/engineering.md`).
- M1+: full audio pipeline (denoise/diarize/translate), then text/image/video per CHARTER M-order.
- **D9 (2026-07-09) ratified — centralized observability:** this service now owes a `/metrics` endpoint + Grafana dashboard JSON (throughput/queue depth, per-stage + C8 latency, enrichment counts). On the backlog — see CHARTER.md § Scope (Observability) + deliverable **M8**.
