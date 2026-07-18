# WS-C — Ingest server: segment upload, A/V demux, continuity ledger, gap report

> Recording-led capture M1, priorities 1+2 (founders 2026-07-18). The server half of the
> phone client (WS-B): receive self-contained A/V segments, demux into per-modality C1
> streams, emit through the existing blob-first path, and make "zero silent loss" a
> **checked** guarantee via a per-stream continuity ledger + gap report. Lives in `app/`
> (the M0 service); the M0 `/capture/run` surface is untouched.

**Status:** built + verified live (unit + E2E incl. loss/dup drills) · **Owner session:** recording M1 lead

---

## Decisions

- **D-M1-3 — ledger = SQLite** (`var/ledger.db`, WAL). This is *operational continuity
  metadata* (sessions, segment receipt, minted chunk ids, ack state), not durable user-content
  custody — content custody stays with storage `/raw` (blobs transit the spool and are deleted
  after emit by default). Crash-safe: `chunk_id`s are minted + persisted BEFORE the first emit
  attempt, so a restart retries with the SAME ids (idempotent downstream).
- **D-M1-4 — upload wire** as pinned in WS-B (internal to recording, not a C-contract).
  **Route rename 2026-07-18 (founders):** client-facing prefix `/ingest/*` → `/capture/*`
  (file `app/ingest_web.py` → `app/capture_web.py`) so `/ingest` is uniquely
  data-processing's C1 receiver. `/ingest/*` stays mounted as a hidden
  (`include_in_schema=False`) deprecated alias of the same router — one code path, two
  mounts — until already-loaded phone pages refresh. Shapes and semantics unchanged.
- **Demux is recording's job** (charter OQ8 pattern: the muxed device link is split HERE,
  before emission): phone segments arrive A/V-muxed; ffmpeg demuxes per segment into
  audio → `audio/wav` 16 kHz mono s16le (ASR-native) and video → container copy
  (`video/mp4` / `video/webm`, no re-encode). 1 segment → 1 audio chunk + 1 video chunk
  (either may be absent — probe decides). Both chunks carry the segment's wall-clock span.
- **Two C1 streams per session** (audio, video): own `stream_id` (ULID) each, same
  `device_id`, wall-clock aligned. C1 `sequence` = per-stream emit counter — dense by
  construction. The client→server leg has its own continuity domain (`seq`); the ledger joins
  the two legs. A client-side loss (dropped segment) appears in the CLIENT leg of the report,
  never as a fabricated C1 gap.
- **Consent-gate compatibility (D13, not built):** the spool+ledger is the natural holdback
  point — a future consent gate delays the emit step per session; the upload path is unchanged.
  `RECORDING_KEEP_SPOOL=1` keeps spooled segments after emit (default: delete).

## Server pieces (all new files unless noted)

- `app/ledger.py` — SQLite ledger. Tables:
  - `sessions(session_id PK, user_id, device_id, started_at, ended INT, expected_segments INT NULL)`
  - `segments(session_id, seq, sha256, bytes, mime, t_start, t_end, received_at, state, spool_path, PRIMARY KEY(session_id, seq))` — state: `received|emitted|failed`
  - `streams(stream_id PK, session_id, modality, codec, next_sequence)`
  - `chunks(stream_id, sequence, session_id, seq, modality, chunk_id, codec, bytes, sha256, blob_ref, dp_acked INT, record_ids TEXT, emitted_at, PRIMARY KEY(stream_id, sequence))`
- `app/demux.py` — ffprobe track probe + ffmpeg demux of one spooled segment into per-modality
  chunk files (subprocess; binaries from `FFMPEG_BIN`/`FFPROBE_BIN`, default PATH).
- `app/emitter.py` — per-session in-order worker: for each received segment, demux → per
  modality: get-or-create stream → mint+persist `chunk_id` → PUT `/raw/blobs` → validated C1
  push → record `blob_ref`/`record_ids`/acks in the ledger → mark segment `emitted`, delete
  spool file. Reuses `clients.StorageClient`/`DataProcessingClient` (their retry = the
  at-least-once semantics) and `contracts.validate_c1`. Terminal failure marks the segment
  `failed` (visible in the report); `POST /capture/sessions/{id}/retry` re-enqueues failures.
- `app/capture_web.py` — the router: segment upload (idempotent on `(session_id, seq)`; sha256
  verified when provided), end marker, sessions list, gap report, retry. Async ack by default;
  `RECORDING_INGEST_SYNC=1` processes inline before ack (tests + small-scale ops).
- `app/main.py` (edit) — include the router; mount `clients/web/` at `/client` (static,
  html=True); `GET /` redirects to `/client/`.
- `app/config.py` (edit) — add `var_dir` (`RECORDING_VAR_DIR`, default `<service>/var`),
  `ffmpeg_bin`, `ffprobe_bin`, `ingest_sync`, `keep_spool`.

## Gap report — `GET /capture/sessions/{id}/report`

```jsonc
{
  "session_id": "...", "user_id": "...", "device_id": "...",
  "started_at": "...", "ended": true, "expected_segments": 7, "received_segments": 7,
  "client_leg": { "missing_seqs": [], "duplicate_deliveries": 0, "unterminated": false },
  "emit_leg": [
    { "modality": "audio", "stream_id": "...", "codec": "audio/wav",
      "chunks_emitted": 7, "last_sequence": 6, "pending": 0, "failed": 0,
      "dp": { "checked": true, "max_sequence": 6, "missing": [], "duplicate_deliveries": 0 } },
    { "modality": "video", "...": "..." }
  ],
  "verdict": "clean" | "gaps" | "recording"
}
```

`dp` comes from querying data-processing `GET /continuity/{stream_id}` live (skipped →
`checked:false` when DP is unreachable). **`verdict:"clean"` = session ended ∧ no client-leg
missing ∧ every received segment emitted ∧ every DP-checked stream shows no missing** — the
"zero silent loss" guarantee, checked end-to-end across both legs. `GET /capture/sessions`
lists per-session summaries.

## Tests (extend the M0 suite; 34 existing tests stay green)

TestClient + httpx MockTransport fakes (existing pattern). ffmpeg-dependent tests generate a
tiny real segment via ffmpeg (`testsrc2` + `sine`, ~2 s) at session scope and skip cleanly if
ffmpeg is absent. Cover at least: segment idempotency (re-POST same seq → `duplicate`, no
double emit); sha mismatch → 400; demux → two dense C1 streams with correct spans + codecs;
audio-only segment → single stream; ledger persistence of minted chunk_ids (restart-safety:
re-emit reuses them); end marker → expected_segments; missing seq → report `gaps` verdict +
`missing_seqs`; failed emit (fake 500s) → `failed` count + retry endpoint re-emits; async mode
ack-then-poll; report merges a fake DP `/continuity` response.

## Worklog
- 2026-07-18 — spec written; handed to the build fan-out.
- 2026-07-18 — built as specced: `app/ledger.py` (SQLite WAL, connection-per-call,
  `BEGIN IMMEDIATE`; chunk identity minted atomically + persisted pre-emit; restart drill
  tested), `app/demux.py` (ffprobe decides tracks; audio re-encode to wav16k mono, video
  container copy; 120 s subprocess timeout so a pathological upload can't wedge a worker),
  `app/emitter.py` (per-session FIFO worker, loop-affine, startup `reenqueue_pending`,
  failures parked for `/retry`), `app/capture_web.py` (upload idempotent on (session, seq);
  same-sha dup counted, different-sha 409; end marker; sessions list; two-leg gap report
  + live DP `/continuity` merge; report gained session-level `segment_states` — per-stream
  `pending` can't see pre-demux segments, so this is the drain signal). 16 new tests;
  recording suite 66 passed.
- 2026-07-18 — **verified live** (run_learn fleet, real ports): clean session → verdict
  `clean` with DP cross-check; client-loss drill → verdict `gaps`, `missing_seqs:[1]`,
  C1 streams stay dense (no fabricated gap — exactly the two-continuity-domain design);
  dup redelivery → acked `duplicate`, counted, not re-emitted, verdict `clean`. Uploads
  also exercised through the cloudflared HTTPS tunnel.
- 2026-07-18 — **adversarial review round** (multi-agent find → 2-skeptic verify over the
  diff) confirmed 5 server defects, all fixed + regression-tested (+6 tests → 72):
  (1) *ack-before-spool*: the ledger row committed before the spool write, so a crash
  between them made the client's retry ack bytes that existed nowhere — now the spool is
  written FIRST (content-addressed `seq.sha[:12].ext` names so a conflicting sha can
  never clobber the original), and a duplicate whose segment is still `received`
  re-enqueues (self-heals the lost-spool/lost-enqueue windows);
  (2) *unbounded gap walk*: one huge `seq` made the report materialize every missing seq —
  `seq` is now bounded (≤ 9 999 999), the walk is O(received), `missing_seqs` is capped at
  1000 with an exact `missing_count` alongside;
  (3) *unbounded body*: `/capture/segments` now streams the body against a cap
  (`RECORDING_MAX_SEGMENT_MB`, default 64 → 413);
  (4) *DP-amnesia false alarm*: a mid-session DP restart made its in-memory tracker
  report already-acked chunks as a permanent leading gap → verdict `gaps` forever; the
  report now reconciles DP-missing against the ledger's ack receipts
  (`dp.missing_unacked` drives the verdict; raw `missing` kept for transparency);
  (5) *stale end marker*: the client beacons `end` on every page-hide, so a resumed
  session could read `clean` against a stale expected count — `mark_ended` is now
  monotonic on `expected_segments` and a segment arriving past the marker REOPENS the
  session; plus *sequence-order-vs-retry*: all of a segment's chunks are allocated
  before any emits, so a mid-emit failure + `/retry` slots back in capture order
  (a fully-demux-failed segment retried after later ones still emits late sequences —
  documented residual, visible as `failed` in the report; `t_start` stays the time axis).
  Also from the round: demux subprocess timeout (120 s) so a hung ffmpeg can't wedge a
  session worker, and blocking read/hash moved off the event loop in the emitter.
  Re-verified live end-to-end after the fixes (clean + gap drills, real ASR).
- 2026-07-18 (computer-capture lead) — **route rename executed** (see D-M1-4 note):
  `app/ingest_web.py` → `app/capture_web.py`, prefix moved to the include site
  (`main.py` mounts `/capture` + hidden `/ingest` alias). Handlers, shapes, ledger,
  emitter untouched. Test module renamed `test_capture_web.py` (+2 alias tests: same wire
  through `/ingest`, OpenAPI hides the alias); suite green; alias + canonical drilled
  live on the fleet. Two NEW client surfaces now speak this wire unchanged —
  [ws-e](ws-e-extension.md) (extension) and [ws-f](ws-f-mac-cli.md) (mac CLI) — plus
  `tests/test_wire_conformance.py` proving the client-shape matrix (video-only webm/vp8,
  audio-only webm/opus, muxed mp4 h264+aac) demuxes to the right C1 streams.
