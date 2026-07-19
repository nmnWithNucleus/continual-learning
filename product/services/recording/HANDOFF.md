# HANDOFF — Recording Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** **COMPUTER CAPTURE SURFACES slice — ALPHA COMPLETE: all three surfaces verified
clean end-to-end on real devices** (2026-07-19). **Phone web** (mic+camera → mp4/wav),
**mac CLI** (avfoundation screen+mic), **browser extension** (Chrome MV3 → **direct tab
capture**, video+audio) — each ran a real capture that landed verdict `clean`, blobs
sha256-verified + ffprobe-decoded in storage, and real ASR transcripts in `/context`. The
extension **PIVOTED 2026-07-19 (D-E7)** off the desktop-screen-picker (which failed 3× on the
tester's Comet browser) to direct tab capture; passed on the first real run. Client wire renamed
**`/ingest/*` → `/capture/*`** (founders; alias removed). All prior data purged 2026-07-19;
fleet fresh. Runbook: [handoff/alpha-runbook.md](handoff/alpha-runbook.md) · recording suite
**110 tests** · **Last updated:** 2026-07-19 (recording computer-capture lead)

## Workstream index
| WS | What | Status | Working file | Owner session |
|---|---|---|---|---|
| A | M0 ingest spine (capturer + `/capture/run` + CLI) | done — integrated E2E 2026-07-09 | `app/`, `tests/` | learn-loop M0 fan-out |
| B | **Phone web client** (camera+mic → segments → upload) | **real-phone verified** (M1 + again 2026-07-19 on the `/capture/*` wire, verdict `clean`) | [handoff/ws-b-phone-web-client.md](handoff/ws-b-phone-web-client.md) | recording M1 lead |
| C | **Ingest server**: segment upload, A/V demux, continuity ledger, gap report | built + verified live (loss/dup drills) | [handoff/ws-c-ingest-demux-ledger.md](handoff/ws-c-ingest-demux-ledger.md) | recording M1 lead |
| D | **VAD-cut chunking** (charter OQ4 → D-M1-2) | built + verified on real speech | [handoff/ws-d-vad-carve.md](handoff/ws-d-vad-carve.md) | recording M1 lead |
| — | DP-side pair (continuity detector + real ASR + VAD gate) | built + verified | [../data-processing/handoff/ws-m1-continuity-asr.md](../data-processing/handoff/ws-m1-continuity-asr.md) | recording M1 lead |
| E | **Browser extension** (MV3 passive: **direct tab capture**, D-E7) | **REAL-BROWSER VERIFIED** (Comet, verdict `clean`, real transcripts) | [handoff/ws-e-extension.md](handoff/ws-e-extension.md) | computer-capture lead |
| F | **Mac capture CLI** (ffmpeg avfoundation → segments → wire) | **live-verified** (real avfoundation + `--source test`, verdict `clean`) | [handoff/ws-f-mac-cli.md](handoff/ws-f-mac-cli.md) | computer-capture lead |

## Current state
- **M0 spine unchanged and green** (`:8084`, `/capture/run`, blob-first PUT → C1 push,
  at-least-once, dedup on `chunk_id`). See the M0 notes in git history / ws-A tests.
- **Phone web client** (`clients/web/`, static, no build step; served at `/client/`, `GET /`
  redirects): getUserMedia + segmented MediaRecorder (**~10 s self-contained segments via
  recorder restart** — D-M1-1; timeslice fragments aren't self-contained), record/pause/stop,
  camera-off mic-only mode, serialized offline upload queue with retry/backoff, end marker
  (+ `sendBeacon` on pagehide), wake lock, live gap-report poll with verdict badge.
- **Capture-wire server** (`app/capture_web.py` + `ledger.py` + `demux.py` + `emitter.py`):
  **client wire renamed `/ingest/*` → `/capture/*` 2026-07-18 (founders)** so `/ingest` is
  uniquely data-processing's C1 receiver; the transitional alias was **removed 2026-07-19**
  (CTO: single tester — refresh loaded pages instead of versioning routes; a test asserts
  recording serves nothing under `/ingest`).
  `POST /capture/segments` (idempotent on `(session_id, seq)`, sha-verified, spool→ledger ack),
  per-session FIFO emit worker: ffmpeg **demux into per-modality chunks** (audio → `audio/wav`
  16 kHz mono; video → container copy mp4/webm) → per modality get-or-create stream
  (**own `stream_id`, same `device_id`**) → chunk identity minted + persisted BEFORE first
  emit (crash-safe; restart re-emits the same `chunk_id`s) → blob-first PUT → validated C1
  push → acks recorded. `RECORDING_INGEST_SYNC=1` for inline processing (tests/small ops).
  Spool deleted after emit (`RECORDING_KEEP_SPOOL=1` keeps it — the D13 consent-holdback seam).
- **Gap detection is now a CHECKED guarantee** (was emit-side-only affordance in M0):
  the SQLite **continuity ledger** (`var/ledger.db`) tracks both legs;
  `GET /capture/sessions/{id}/report` joins client leg (missing seqs, dups, unterminated),
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
- **Browser extension** (`clients/extension/`, Chrome MV3 — ws-E): a PASSIVE capture
  surface (no content scripts, no page/DOM access, no static host permissions — runtime
  origin grant instead of server CORS). Screen video (`desktopCapture` picker) + tab audio
  (`tabCapture`, AudioContext passthrough keeps the tab audible) as **one ingest session
  per source** → separate C1 streams, same `device_id` (`ext-chrome-<suffix>`). D-M1-1
  segmented recorder + the phone client's serialized uploader as DI'd ES modules
  (deno-tested); popup mirrors the phone status panel with per-source verdict badges.
  Server URL is a popup setting (tunnel or localhost). **Not run in a real Chrome here
  (headless box) — load-unpacked runbook in ws-E §Human test steps.**
- **Mac capture CLI** (`clients/mac/nucleus_capture.py` — ws-F): single-file stdlib-only
  python3 + ffmpeg; avfoundation screen+mic muxed → ~10 s self-contained mp4 segments
  (forced keyframes) → serialized uploader on the same wire; duration-chained wall-clock
  stamps (exact adjacency); graceful Ctrl-C (drain → end marker → report poll → verdict
  exit code); `--source test` (lavfi) drives the identical path headless. macOS
  Screen-Recording permission grant + device-index discovery documented in ws-F.
  **avfoundation leg itself needs a human mac** (ws-F §Runbook); everything else
  live-E2E-verified on this box.
- **Wire conformance** (`tests/test_wire_conformance.py`): the client-shape matrix —
  video-only webm/vp8 (extension screen), audio-only webm/opus (extension tab), muxed mp4
  h264+aac (mac CLI) — each demuxes to exactly the right C1 streams with spans preserved;
  the D-E3 two-session-same-device pattern and client-agnostic gap detection proven
  against the real app.
- **Adversarial review round #2** (computer-capture slice: 5-lens find → 2-skeptic verify,
  19 findings → 10 confirmed) — all fixed + regression-tested; detail in ws-E/ws-F
  worklogs. Headliners: extension tab-capture stream-id expiry behind the human-paced
  screen picker (acquisition reordered + failed sources surfaced), offscreen
  drained-vs-restart races, mac CLI Ctrl-C stamp corruption (idempotent duration slotting),
  stale-spool reuse refusal, HTTPException escaping the retry pump.
- **Tests:** recording **109** (72 M1 + 37 new: 27 mac CLI + 3 extension assets + 5
  wire conformance + 2 deprecated-alias) · data-processing 38 · storage 26 (both re-run,
  unregressed) · extension deno suite 17.
  Live E2E this slice: mac CLI test-source → verdict `clean` + 12 C2 records with exact
  spans; deprecated-alias drill clean.
- E2E driver (synthetic phone, clean/gap/dup modes) lives in the session scratchpad —
  rewrite-on-demand; the unit suite covers the same paths hermetically.

## Pinned decisions & glossary (capture path)

- **D-M1-5 — client transport (founders × recording lead, 2026-07-19): segmented HTTP
  upload for ALL v0 surfaces** (phone / extension / mac CLI). Rationale: our capture path
  is the *archive/training* job, not live viewing — loss-intolerant, offline-resilient,
  latency-tolerant — which maps onto segmented upload (the Axon-bodycam/dashcam pattern),
  not persistent-socket streaming (the Ring/Nest *live-view* pattern; note those products
  run BOTH paths separately). **Continuous streaming ingest is a deferred ADDITIVE leg**:
  a socket receiver (WebSocket/RTSP/SRT per device) → per-stream continuity buffer →
  server-side segmenter, terminating in the EXISTING spool→demux→carve→emit machinery —
  C1/C2 unchanged by design (C1 deliberately begins *after* transport: "chunks exist").
  Build it only when a surface needs sub-segment latency (live-view is out of v0 scope) or
  the bodycam firmware demands it; cheaper latency lever first: shrink `SEGMENT_SECONDS`.
- **Glossary** (pinned so docs/sessions stay unambiguous): **segment** = client→server
  upload unit (~10 s self-contained clip; `seq` dense per capture session) · **chunk** =
  server→DP single-modality unit (one `/raw` blob + one C1 envelope; `sequence` dense per
  stream) · **stream** = one continuous single-modality flow from one device session
  (`stream_id` — the identity that crosses service boundaries) · **capture session** = one
  start→stop on a device (press-record→stop / CLI run→Ctrl-C / extension click→click);
  first-class in the ledger, **never travels past C1** (C1 carries `stream_id`, not
  `session_id`) · **record** = one `/context` row conforming to the C2 contract.
  Disambiguation: a **capture session** (recording) is NOT the serve-loop **chat session**
  (`session_id` in C3/C4, storage `/sessions`) — qualify the word when both are in frame.

## Next
- ~~Real-phone verification~~ **DONE 2026-07-18** — CTO's iPhone (Safari, tunnel): two
  sessions 7/7 + 9/9 clean; UI leaks + an ASR auto-language hallucination found and fixed
  same day (ws-B worklog). *The learn fleet (faster_whisper, `ASR_LANGUAGE=en` via
  `deploy/learn.env`) + tunnel remain UP on node-7 — restarted 2026-07-18 by the
  computer-capture lead onto the renamed `/capture/*` wire (tunnel URL unchanged,
  `/health` + `/client/` + alias re-verified through it). The URL rotates per tunnel
  restart, so ALWAYS read it from `var/tunnel_url.txt`; `run_learn.sh --status` checks
  the fleet.*
- ~~Computer capture surfaces~~ **BUILT + REVIEWED + (test-mode) LIVE-VERIFIED
  2026-07-18** (this slice — ws-E extension, ws-F mac CLI; server needed nothing new, as
  designed).
- **ALPHA TEST (in progress 2026-07-19)** — the CTO drives all three surfaces (phone web,
  extension, mac CLI) per **[handoff/alpha-runbook.md](handoff/alpha-runbook.md)**
  (launch steps, per-step expected signals, nuance drills, server-side cross-checks,
  pass bar). All previously captured data was **purged** (recording ledger+spool,
  storage `dev.db`+`raw_store`; DP state is in-memory) and the fleet restarted fresh so
  alpha results read from zero. This box is headless Linux — the Chrome and avfoundation
  legs can only be claimed from the CTO's machines, never from here.
- **Later capture surfaces, explicitly recorded**: system/desktop audio for the extension
  (kept OUT of this slice by scope); a mac menu-bar/GUI app (ScreenCaptureKit, visible
  capture indicator, autostart) — capability exists today via the CLI, UX later.
- **Metrics emission (D9)** across recording + DP is the founders' sequenced NEXT once the
  capture surfaces are human-verified solid.
- Retry ergonomics: `/capture/sessions/{id}/retry` is manual; consider an automatic periodic
  re-drive of `failed` segments once real-world failure modes are seen.
- Continuity ledger growth: rows are permanent (fine at beta scale); add retention/compaction
  before fleet scale (M5 telemetry work).
- Consent gate (M2) stays **back-burner (D13)** — the spool+ledger is the designed holdback
  point; nothing here forecloses it.
- `/metrics` + dashboard JSON (M6/D9) still owed once Platform's shared backbone lands.
