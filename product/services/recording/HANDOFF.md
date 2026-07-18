# HANDOFF — Recording Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** capture **M1 slice built + verified E2E + adversarially reviewed** (2026-07-18):
phone web client + ingest server (demux → two C1 streams) + checked gap detection + VAD-cut
chunking (OQ4 decided) + real ASR standing on the DP side · recording suite **72 tests** ·
**Last updated:** 2026-07-18 (recording M1 lead session)

## Workstream index
| WS | What | Status | Working file | Owner session |
|---|---|---|---|---|
| A | M0 ingest spine (capturer + `/capture/run` + CLI) | done — integrated E2E 2026-07-09 | `app/`, `tests/` | learn-loop M0 fan-out |
| B | **Phone web client** (camera+mic → segments → upload) | built + verified server-side; **real-phone tap = tester's step** | [handoff/ws-b-phone-web-client.md](handoff/ws-b-phone-web-client.md) | recording M1 lead |
| C | **Ingest server**: segment upload, A/V demux, continuity ledger, gap report | built + verified live (loss/dup drills) | [handoff/ws-c-ingest-demux-ledger.md](handoff/ws-c-ingest-demux-ledger.md) | recording M1 lead |
| D | **VAD-cut chunking** (charter OQ4 → D-M1-2) | built + verified on real speech | [handoff/ws-d-vad-carve.md](handoff/ws-d-vad-carve.md) | recording M1 lead |
| — | DP-side pair (continuity detector + real ASR + VAD gate) | built + verified | [../data-processing/handoff/ws-m1-continuity-asr.md](../data-processing/handoff/ws-m1-continuity-asr.md) | recording M1 lead |

## Current state
- **M0 spine unchanged and green** (`:8084`, `/capture/run`, blob-first PUT → C1 push,
  at-least-once, dedup on `chunk_id`). See the M0 notes in git history / ws-A tests.
- **Phone web client** (`clients/web/`, static, no build step; served at `/client/`, `GET /`
  redirects): getUserMedia + segmented MediaRecorder (**~10 s self-contained segments via
  recorder restart** — D-M1-1; timeslice fragments aren't self-contained), record/pause/stop,
  camera-off mic-only mode, serialized offline upload queue with retry/backoff, end marker
  (+ `sendBeacon` on pagehide), wake lock, live gap-report poll with verdict badge.
- **Ingest server** (`app/ingest_web.py` + `ledger.py` + `demux.py` + `emitter.py`):
  `POST /ingest/segments` (idempotent on `(session_id, seq)`, sha-verified, spool→ledger ack),
  per-session FIFO emit worker: ffmpeg **demux into per-modality chunks** (audio → `audio/wav`
  16 kHz mono; video → container copy mp4/webm) → per modality get-or-create stream
  (**own `stream_id`, same `device_id`**) → chunk identity minted + persisted BEFORE first
  emit (crash-safe; restart re-emits the same `chunk_id`s) → blob-first PUT → validated C1
  push → acks recorded. `RECORDING_INGEST_SYNC=1` for inline processing (tests/small ops).
  Spool deleted after emit (`RECORDING_KEEP_SPOOL=1` keeps it — the D13 consent-holdback seam).
- **Gap detection is now a CHECKED guarantee** (was emit-side-only affordance in M0):
  the SQLite **continuity ledger** (`var/ledger.db`) tracks both legs;
  `GET /ingest/sessions/{id}/report` joins client leg (missing seqs, dups, unterminated),
  emit leg (per-stream dense sequences, pending/failed, `segment_states` drain signal), and a
  **live DP cross-check** (`GET /continuity/{stream_id}` on data-processing) into a
  `clean|gaps|recording` verdict. Client-side loss appears in the client leg and NEVER as a
  fabricated C1 gap (two continuity domains, joined by the ledger). Verified live: clean,
  loss-drill (`gaps` + `missing_seqs`), dup-drill (acked `duplicate`, not re-emitted).
- **Chunking (OQ4) DECIDED — D-M1-2** (charter §Open questions 4 updated): VAD-cut variable
  chunks [5–30 s] where the server owns a continuous feed (`app/carve.py`, now the audio
  ChunkSource **default**; explicit `chunk_seconds`/`CHUNK_SECONDS` = fixed); phone = fixed
  ~10 s edge segments; video = fixed windows. Verified live on real speech: cut lands in the
  natural pause, exact `t_end[n]==t_start[n+1]` adjacency.
- **DP-side pair landed** (their ws file above): `/ingest` break/dup **continuity tracker** +
  `/continuity` endpoints; **faster-whisper standing** (`asr-fw-v1`, mock stays default) with
  **VAD gate** (all-silence chunk → honest empty transcript, no hallucination). Verified live
  on real speech through the whole phone path.
- **Tunnel**: `run_tunnel.sh` (`--bg/--stop/--url`) exposes `:8084` over HTTPS
  (cloudflared quick tunnel; URL rotates per restart, written to `var/tunnel_url.txt`).
  Full upload path verified through the tunnel. **Beta handover = that URL + `/client/`.**
- **Adversarial review round** (multi-agent find → 2-skeptic verify) confirmed 7 defects
  (5 server, 2 client) — all fixed + regression-tested, detail in
  [ws-c](handoff/ws-c-ingest-demux-ledger.md) §Worklog: ack-before-spool loss window,
  unbounded gap walk / body size, DP-restart false `gaps` verdict (report now reconciles
  DP-missing against ledger ack receipts — `dp.missing_unacked`), stale pagehide end
  marker (monotonic + reopen), retry sequence-order, demux subprocess timeout, and a
  client Pause→Resume double-recorder race. Live E2E re-verified after the fixes.
- **Tests:** recording 72 · data-processing 38 · storage 26 (unregressed) — all re-run by the
  lead session. E2E drills ran on the live run_learn fleet (mock AND faster_whisper backends).
- E2E driver (synthetic phone, clean/gap/dup modes) lives in the session scratchpad —
  rewrite-on-demand; the unit suite covers the same paths hermetically.

## Next
- **Real-phone verification** (the one unexecuted leg): tester opens
  `bash run_tunnel.sh --url` + `/client/`, presses record, speaks ~1 min → check the on-page
  verdict goes `clean` and transcripts appear in `/context` (storage
  `GET /context/records?user_id=&from=&to=`). iOS Safari MediaRecorder quirks are the risk
  the POC already de-risked; fixes (if any) belong in `clients/web/app.js`.
  *As of 2026-07-18 the learn fleet (ASR_BACKEND=faster_whisper) + tunnel were left UP on
  node-7 for exactly this hand-off — the URL rotates per tunnel restart, so ALWAYS read it
  from `var/tunnel_url.txt` (never from a doc); `run_learn.sh --status` checks the fleet.*
- **Browser extension** (slice priority 5, not built — time-boxed out): `clients/extension/`,
  screen-share video + `tabCapture` tab audio as separate C1 streams; NO system audio.
  Same segment-upload wire; the server side is ready for it (any self-contained A/V upload).
- Retry ergonomics: `/ingest/sessions/{id}/retry` is manual; consider an automatic periodic
  re-drive of `failed` segments once real-world failure modes are seen.
- Continuity ledger growth: rows are permanent (fine at beta scale); add retention/compaction
  before fleet scale (M5 telemetry work).
- Consent gate (M2) stays **back-burner (D13)** — the spool+ledger is the designed holdback
  point; nothing here forecloses it.
- `/metrics` + dashboard JSON (M6/D9) still owed once Platform's shared backbone lands.
