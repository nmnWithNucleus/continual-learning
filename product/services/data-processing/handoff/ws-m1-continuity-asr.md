# WS — DP capture M1: ingest continuity detector + real-ASR pipeline

> Data-processing's half of the recording-led capture M1 slice (founders 2026-07-18; the
> recording M1 lead session coordinates — see
> `../../recording/handoff/ws-c-ingest-demux-ledger.md` for the paired recording work).
> Two deliverables: (1) a break/dup detector on `/ingest` making "zero silent loss" a checked
> guarantee; (2) the fuller audio pipeline — real faster-whisper as the STANDING backend with
> a VAD gate, plus the pipeline shape stubbed behind the Processor seam.

**Status:** built + verified (38 unit tests; real faster-whisper + VAD run live E2E) ·
**Owner session:** recording M1 lead

---

## 1. Continuity detector (`/ingest` break/dup) — priority pair item

- `app/continuity.py` (new) — `ContinuityTracker`, updated on EVERY schema-valid `/ingest`
  (fresh, dedup-hit, and in-flight-dup paths alike; after C1 validation, before any return).
  Per `stream_id`: `first_seen`/`last_seen` (RFC3339), `max_sequence`, received count, seen
  sequences as merged intervals `[[lo,hi],…]` (memory-bounded for dense streams),
  `duplicate_deliveries` (same `(sequence, chunk_id)` re-seen), and `sequence_conflicts`
  (same `sequence`, DIFFERENT `chunk_id` — an anomaly worth flagging loudly).
  `missing` = the gaps below `max_sequence` (+ the leading gap when first-seen > 0, per C1:
  a non-zero first-seen value is lost chunks).
- Endpoints in `app/main.py`: `GET /continuity` → `{streams:[{stream_id, modality, user_id,
  device_id, max_sequence, received, missing, duplicate_deliveries, sequence_conflicts,
  first_seen, last_seen}, …]}`; `GET /continuity/{stream_id}` → that stream's entry (404
  unknown). Recording's gap report queries this to close the loop across both legs.
- In-memory, single-process (exactly the `DedupStore` posture; documented). Restart resets
  the observation window; the durable backstop remains `/context` provenance.

## 2. Audio pipeline — real ASR standing + VAD gate + shape stubs

- **faster-whisper becomes a standing backend**: added to `requirements.txt` (no torch —
  ctranslate2/onnxruntime/av/tokenizers). `ASR_BACKEND` default STAYS `mock` (GPU-less dev,
  tests unchanged); `faster_whisper` is the flip for real runs.
- **VAD gate** (kills Whisper silence-hallucination; ambient-only chunks come out honest):
  `asr/faster_whisper.py` passes `vad_filter=settings.asr_vad` (new env `ASR_VAD`, default
  ON, `vad_parameters={"min_silence_duration_ms": 500}`). All-silence chunk → the model
  yields no segments → `AsrResult(text="", segments=[])` → a valid C2 with an empty
  transcript (C2 allows `text:""`; the record still documents the span). **PIPELINE_VERSION
  bumps `asr-fw-v0` → `asr-fw-v1`** (output dialect changed; version-forward reprocessing).
  Mock backend untouched — no record fork on the mock path, M0 tests stay green.
- **Pipeline shape stubs** (`app/processing/processors/audio.py` restructure): the processor
  becomes an explicit staged pipeline `asr → diarize → translate → acoustic_events` over a
  small pipeline-state carrier. `diarize`/`translate`/`acoustic_events` are documented
  no-op stubs pinning their future contracts (diarize fills `segments[].speaker` +
  `enrichments.speakers`; translate adds a `discriminator="translation"` unit;
  acoustic_events adds a `discriminator="acoustic"` caption unit for non-speech audio —
  captioned, not dropped). Output for today's two backends is byte-identical to the current
  processor (existing tests prove it).
- `learn.env.example` documents `ASR_BACKEND=faster_whisper`, `ASR_VAD`, `ASR_MODEL`,
  `ASR_DEVICE`, `ASR_COMPUTE_TYPE`.

## Tests

Continuity: unit tests on the tracker (gap / leading-gap / dup / conflict / interval-merge)
+ endpoint tests through the existing `/ingest` TestClient fixtures (drive dense, gapped,
duplicated, conflicting sequences; assert reports; dedup-hit path still counts). ASR: mock
path unchanged (existing suite); fw specifics carry a model-download cost so live
verification happens in the lead session's E2E, not unit tests — pipeline-stage refactor is
covered by the existing mock-output tests (byte-identical assertion).

## Worklog
- 2026-07-18 — spec written by the recording M1 lead (paired-priority coordination); handed
  to the build fan-out.
- 2026-07-18 — built as specced: `app/continuity.py` ContinuityTracker (merged seen-intervals,
  leading-gap detection, duplicate_deliveries vs sequence_conflicts via a per-sequence
  first-chunk_id map — dev-scale memory posture documented like DedupStore; conflict samples
  capped at 20, exposed via accessor + warning log, NOT in the endpoint payload since the spec
  pins that field list); one `note()` call site in `/ingest` right after the C1 schema gate so
  fresh/dedup-hit/in-flight paths all count and invalid C1s never do; `GET /continuity` +
  `GET /continuity/{stream_id}`. faster-whisper standing in requirements.txt (lazy import
  kept); `vad_filter` + `min_silence_duration_ms=500` behind `ASR_VAD` (default on);
  `PIPELINE_VERSION` → `asr-fw-v1`; audio processor restructured into the staged
  asr→diarize→translate→acoustic_events pipeline with documented no-op stubs — mock output
  byte-identical (existing suite is the proof). DP suite: 38 passed (24 + 14 new).
- 2026-07-18 — **verified live** (lead session E2E on real ports): recording's gap report
  cross-checks `/continuity` per stream (clean + gap + dup drills all consistent); real
  faster-whisper (base/int8/CPU, VAD on) transcribed phone-path segments correctly
  (`asr-fw-v1` C2s in `/context`), and an all-silence segment produced an honest EMPTY
  transcript (VAD gate kills the Whisper silence-hallucination).
