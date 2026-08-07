# HANDOFF — Recording Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** built · recording suite **144 tests** (110 + 7 async-seam/redrive/migration +
3 metrics) · *Last updated:* 2026-08-07 (the DP rebuild cut over: DP now runs async by default and
the durable-journal + in-flight-kill recovery were witnessed against our capture ledger at the
Stage F soak; the E-6 retry-window is the one live gap that soak surfaced)

**Where we are.** The client wire speaks `capture_id` + `segment_num` as of 2026-07-29 (were `session_id` + `seq`; routes `/capture/sessions/*` → `/capture/captures/*` — reasoning in [CHARTER.md](CHARTER.md) §Glossary, record in [handoff/worklog.md](handoff/worklog.md)). The computer-capture-surfaces slice is alpha-complete: all three surfaces were
verified `clean` end to end on real devices on 2026-07-19. Each ran a real capture that landed
verdict `clean`, with blobs sha256-verified and ffprobe-decoded in storage, and real ASR
transcripts in `/context`. Runbook: [handoff/alpha-runbook.md](handoff/alpha-runbook.md).

- **Phone web** — mic and camera → mp4/wav.
- **Mac CLI** — avfoundation screen and mic.
- **Browser extension** — Chrome MV3, direct tab capture, video and audio.
- Also landed 2026-07-19: async-ingest seam tolerance for DP's `INGEST_ASYNC`, plus D9 `/metrics`
  and a dashboard (M6).

**Watch out for**

- **The extension pivoted 2026-07-19 (D-E7)** off the desktop-screen-picker, which failed 3× on
  the tester's Comet browser, to direct tab capture. It passed on the first real run.
- The client wire was renamed `/ingest/*` → `/capture/*` by the founders, and the alias is
  removed.
- All prior data was purged 2026-07-19, so the fleet is fresh.

## Workstream index
| WS | What | Status | Working file | Owner session |
|---|---|---|---|---|
| A | M0 ingest spine (capturer + `/capture/run` + CLI) | done — integrated E2E 2026-07-09 | `app/`, `tests/` | learn-loop M0 fan-out |
| B | **Phone web client** (camera+mic → segments → upload) | **real-phone verified** (M1 + again 2026-07-19 on the `/capture/*` wire, verdict `clean`) | [handoff/ws-b-phone-web-client.md](handoff/ws-b-phone-web-client.md) | recording M1 lead |
| C | **Ingest server**: segment upload, A/V demux, continuity ledger, gap report | built + verified live (loss/dup drills) | [handoff/ws-c-ingest-demux-ledger.md](handoff/ws-c-ingest-demux-ledger.md) | recording M1 lead |
| D | **VAD-cut chunking** (charter OQ4 → D-M1-2) | built + verified on real speech | [handoff/ws-d-vad-carve.md](handoff/ws-d-vad-carve.md) | recording M1 lead |
| — | DP-side pair (continuity detector + real ASR + VAD gate) | built + verified | [../data-processing/handoff/ws-m1-continuity-asr.md](../data-processing/handoff/ws-m1-continuity-asr.md) | recording M1 lead |
| E | **Browser extension** — MV3 passive direct tab capture (D-E7) | verified on a real browser (Comet, verdict `clean`, real transcripts) | [handoff/ws-e-extension.md](handoff/ws-e-extension.md) | computer-capture lead |
| F | **Mac capture CLI** (ffmpeg avfoundation → segments → wire) | **live-verified** (real avfoundation + `--source test`, verdict `clean`) | [handoff/ws-f-mac-cli.md](handoff/ws-f-mac-cli.md) | computer-capture lead |
| AO | **Async-ingest seam** (tolerate DP's 202-accept) plus D9 `/metrics` and dashboard (M6) ([↓](#the-async-ingest-seam)) | built + tested (120 green) | [../data-processing/handoff/ws-async-observability.md](../data-processing/handoff/ws-async-observability.md) | async-observability lead |

### The async-ingest seam
> `built` 2026-07-19 · [D16](../../DECISIONS.md) · 120 tests green

**In one line.** We tolerate data-processing accepting a chunk with a `202` before it has
processed it, without ever reporting a lost chunk as `clean`.

**Rules**

- Track `dp_state` per chunk, and reconcile the report on *confirm-on-processed* rather than on
  accept.
- `dp_acked=1` means C2 was durably written, never merely accepted.

**Why it's this way**

- A `202` opens a silent-loss window. Confirming only on `processed` is what keeps an
  accepted-then-lost chunk reading `recording` or `gaps` instead of a silent `clean`.
- Detail, including DP's side of the wire:
  [the DP charter's ingest-processing-mode entry](../data-processing/CHARTER.md) (open
  question 13), which states the `202` / `200` / `503` replies as a joint contract.

## Current state
- **M0 spine unchanged and green** (`:8084`, `/capture/run`, blob-first PUT → C1 push,
  at-least-once, dedup on `chunk_id`). See the M0 notes in git history / ws-A tests.
- **Phone web client** (`clients/web/`) — static, no build step, served at `/client/`, with
  `GET /` redirecting. getUserMedia plus a segmented MediaRecorder.
- It uses **~10 s self-contained segments via recorder restart** (D-M1-1), because timeslice
  fragments are not self-contained.
- Record/pause/stop, camera-off mic-only mode, a serialized offline upload queue with retry and
  backoff, an end marker (plus `sendBeacon` on pagehide), a wake lock, and a live gap-report poll
  with a verdict badge.
- **Capture-wire server** (`app/capture_web.py` + `ledger.py` + `demux.py` + `emitter.py`).
  `POST /capture/segments` is idempotent on `(capture_id, segment_num)`, sha-verified, spool→ledger ack.
- Per capture a FIFO emit worker runs: ffmpeg **demuxes into per-modality chunks** (audio →
  `audio/wav` 16 kHz mono; video → container copy mp4/webm) → per modality get-or-create stream
  (*own `stream_id`, same `device_id`*) → chunk identity minted and persisted *before* the
  first emit → blob-first PUT → validated C1 push → acks recorded.
- Minting identity before the first emit is what makes it crash-safe: a restart re-emits the same
  `chunk_id`s.
- `RECORDING_INGEST_SYNC=1` gives inline processing, for tests and small ops. The spool is deleted
  after emit; `RECORDING_KEEP_SPOOL=1` keeps it, which is the D13 consent-holdback seam.
- **The client wire was renamed `/ingest/*` → `/capture/*` 2026-07-18** (founders), so `/ingest`
  is uniquely data-processing's C1 receiver.
- The transitional alias was *removed 2026-07-19* (CTO: a single tester, so refresh loaded pages
  instead of versioning routes).
- A test asserts recording serves nothing under `/ingest`.
- **Gap detection is now a checked guarantee** (was emit-side-only affordance in M0): the SQLite
  *continuity ledger* (`var/ledger.db`) tracks both legs; `GET /capture/captures/{id}/report`
  joins client leg (missing seqs, dups, unterminated), emit leg (per-stream dense sequences,
  pending/failed, `segment_states` drain signal), and a *live DP cross-check* (`GET
  /continuity/{stream_id}` on data-processing) into a `clean|gaps|recording` verdict.
- Client-side loss appears in the client leg and never as a fabricated C1 gap (two continuity
  domains, joined by the ledger).
- Verified live: clean, loss-drill (`gaps` + `missing_segment_nums`), dup-drill (acked `duplicate`, not
  re-emitted).
- **Chunking (OQ4) ratified — D-M1-2** (charter §Open questions 4 updated): VAD-cut variable
  chunks [5–30 s] where the server owns a continuous feed (`app/carve.py`, now the audio
  ChunkSource *default*; explicit `chunk_seconds`/`CHUNK_SECONDS` = fixed); phone = fixed ~10 s
  edge segments; video = fixed windows.
- Verified live on real speech: cut lands in the natural pause, exact `t_end[n]==t_start[n+1]`
  adjacency.
- **DP-side pair landed** (their ws file above): `/ingest` break/dup *continuity tracker* +
  `/continuity` endpoints; *faster-whisper standing* (`asr-fw-v1`, mock stays default) with
  *VAD gate* (all-silence chunk → honest empty transcript, no hallucination). Verified live
  on real speech through the whole phone path.
- **Tunnel**: `run_tunnel.sh` (`--bg/--stop/--url`) exposes `:8084` over HTTPS
  (cloudflared quick tunnel; URL rotates per restart, written to `var/tunnel_url.txt`).
  Full upload path verified through the tunnel. *Beta handover = that URL + `/client/`.*
- **Adversarial review round** (multi-agent find → 2-skeptic verify) confirmed 7 defects (5
  server, 2 client) — all fixed + regression-tested, detail in
  [ws-c](handoff/ws-c-ingest-demux-ledger.md) §Worklog: ack-before-spool loss window, unbounded
  gap walk / body size, DP-restart false `gaps` verdict (report now reconciles DP-missing against
  ledger ack receipts — `dp.missing_unacked`), stale pagehide end marker (monotonic + reopen),
  retry sequence-order, demux subprocess timeout, and a client Pause→Resume double-recorder race.
- Live E2E re-verified after the fixes.
- **Browser extension** (`clients/extension/`, Chrome MV3 — ws-E): a passive capture surface (no
  content scripts, no page/DOM access, no static host permissions — runtime origin grant instead
  of server CORS).
- *Records the active tab — video and audio in one muxed stream via `chrome.tabCapture` (D-E7,
  pivoted 2026-07-19 off the fragile desktopCapture screen-picker after it failed 3× on the
  tester's Comet browser).* One tab = ONE capture = one muxed-webm (vp8+opus) segment loop; the
  *server demuxes each segment into audio + video C1 streams*, the same muxed-A/V path the
  phone/mac clients use, zero server changes.
- AudioContext passthrough keeps the tab audible; `device_id` = `ext-chrome-<suffix>`. D-M1-1
  segmented recorder + the phone client's serialized uploader as DI'd ES modules (17 deno tests);
  popup mirrors the phone status panel.
- Server URL is a popup setting (tunnel or localhost); a *Discard-unsent* escape hatch unlocks a
  drain stuck on a bad URL.
- *Verified on a real browser 2026-07-19* (CTO on Comet, verdict `clean`, 7 real ASR transcripts
  of the captured tab's audio).
- Trade-off: the extension captures a browser *tab*, not the whole desktop — full-screen capture
  is the mac CLI's job.
- **Mac capture CLI** (`clients/mac/nucleus_capture.py` — ws-F): single-file stdlib-only
  python3 + ffmpeg; avfoundation screen+mic muxed → ~10 s self-contained mp4 segments
  (forced keyframes) → serialized uploader on the same wire; duration-chained wall-clock
  stamps (exact adjacency); graceful Ctrl-C (drain → end marker → report poll → verdict
  exit code); `--source test` (lavfi) drives the identical path headless. macOS
  Screen-Recording permission grant + device-index discovery documented in ws-F.
  *avfoundation leg itself needs a human mac* (ws-F §Runbook); everything else
  live-E2E-verified on this box.
- **Wire conformance** (`tests/test_wire_conformance.py`): the client-shape matrix —
  video-only webm/vp8 (extension screen), audio-only webm/opus (extension tab), muxed mp4
  h264+aac (mac CLI), each demuxes to exactly the right C1 streams with spans preserved;
  the D-E3 two-captures-same-device pattern and client-agnostic gap detection proven
  against the real app.
- **Adversarial review + real-browser alpha reshaped the extension** (detail in ws-E/ws-F
  worklogs): the pre-alpha review round (5-lens → 2-skeptic, 19 → 10 confirmed) hardened the
  then-current screen-picker path; then two real-chrome runs on Comet exposed the
  desktopCapture picker as fundamentally fragile (worker-context refusal, same-tab capture
  collision), so *D-E7 pivoted to direct tab capture* — deleting the picker, the
  `desktopCapture` permission, and the multi-capture bookkeeping. mac CLI review headliners
  stand: Ctrl-C stamp corruption (idempotent duration slotting), stale-spool reuse refusal,
  HTTPException escaping the retry pump.
- **Tests:** recording *110* (72 M1 + 38 new across the computer-capture slice: mac CLI,
  extension assets, wire-conformance incl. the muxed-webm vp8+opus shape, deprecated-alias
  absence) · data-processing 38 · storage 26 (both re-run, unregressed) · extension deno
  suite 17. All three suites re-verified green after the slice merged.
- E2E driver (synthetic phone, clean/gap/dup modes) lives in the session scratchpad —
  rewrite-on-demand; the unit suite covers the same paths hermetically.

## Pinned decisions & glossary

Moved 2026-07-27, so this canvas can be a board: the two pinned decisions are now [DECISIONS.md](DECISIONS.md)
(`R-1`/`R-2`, citing founders' [D14](../../DECISIONS.md)/[D16](../../DECISIONS.md)), and the capture-path glossary is
[CHARTER.md](CHARTER.md) §Glossary.

## Next

Open items only. Anything finished moves to [handoff/worklog.md](handoff/worklog.md) in the same
session — it does not stay here struck through.

- **Later capture surfaces, explicitly recorded**: system/desktop audio for the extension (out
  of this slice by scope); multi-tab simultaneous capture (feasible; deferred — ws-E); a mac
  menu-bar/GUI app (ScreenCaptureKit, visible capture indicator, autostart), capability exists
  today via the CLI, UX later; continuous streaming ingest (D-M1-5 additive leg).
- Retry ergonomics: `/capture/captures/{id}/retry` is manual; consider an automatic periodic
  re-drive of `failed` segments once real-world failure modes are seen.
- Continuity ledger growth: rows are permanent (fine at beta scale); add retention/compaction
  before fleet scale (M5 telemetry work).
- Consent gate (M2) stays **back-burner (D13)** — the spool+ledger is the designed holdback
  point; nothing here forecloses it.
- **Client live-stream testing (the next phase, seeded).** With the DP rebuild cut over, the fleet
  is pointed at a real captured day flowing recording → DP → storage → continuum on real hardware —
  the live pilot-day shape the Stage F soak proved synthetically. Our capture clients are the front
  door for it; nothing new to build, but this is what the fleet is next pointed at.
- **`INGEST_ASYNC=1` is now the fleet default** — resolved at the DP-rebuild Stage F cutover: drill 1
  paid the [D16](../../DECISIONS.md) re-drive gate and async is the operating mode. Our
  confirm-on-processed reconciliation held through the soak's in-flight kill (DP hard-killed with a
  non-empty journal, 27/27 chunks recovered; our 25 transport-failed segments retried clean).
- **E-6 — auto-retry of downstream-declined segments** got its live witness at that soak: the ~66 s
  DP downtime outran our bounded 4-attempt retry, so 25 segments went `failed` and needed a manual
  `/retry` — exactly the recoverable-becomes-terminal-in-1.5 s gap this escalation is about. The
  two-phase design below is the fix ([↓](#e-6--the-rejected-path-two-phase-design)).
- **E-1 — `--segment-seconds 10 → 60`** ([../../HANDOFF.md](../../HANDOFF.md) §Escalations): the single largest cost lever (5.8×). It moves the audio leg too, so it is a joint call with DP-audio.

### E-6 — the rejected path, two-phase design
> refined 2026-07-30 (CTO × recording session) · supersedes the one-line ask · escalation row:
> [../../HANDOFF.md](../../HANDOFF.md) §Escalations

**In one line.** A DP 503 is recoverable backpressure but becomes terminal in ~1.5 s (4 attempts ×
0.25 s backoff); the fix is machine-owned retry for segments the *neighbor declined*, human-owned
recovery for everything that failed *inside our walls*.

**Phase 1 — an `error_class` column (small blast radius, build first)**

- Ledger `segments` gains `error_class` ∈ `downstream_backpressure` · `media` · `internal`,
  stamped when a segment is marked `failed`.
- Only the [D16](../../DECISIONS.md)-pinned 503 maps to `downstream_backpressure`. Timeouts, 5xx
  and poison-shaped errors stay `internal` — the gate is deliberately narrow, widened only on
  evidence from real failures.
- A periodic re-drive loop re-enqueues `failed` segments whose class is `downstream_backpressure`,
  exponentially spaced with jitter, capped at N rounds.
- No report-shape, verdict or metrics change; the class is visible in the row for debugging.

**Phase 2 — promote to a fourth state `rejected` (immediate follow-up)**

- State machine: `received → emitted | failed | rejected`, with `rejected → emitted` (weather
  cleared) or `rejected → failed` (budget exhausted).
- Verdict semantics: `rejected` with budget remaining counts as in-flight (`recording`); budget
  exhausted demotes to `failed` → `gaps`. Neither hides a wedged DP nor cries wolf during
  ordinary backpressure.
- The report's `segment_states` gains a `rejected` count, so "waiting on DP" reads separately
  from "needs a human".
- The human is never deleted from the loop, only moved to the budget's end.

**Watch out for**

- A wedged DP emits 503s indefinitely — the budget-then-demote rule is what keeps that visible
  instead of retrying forever under a calm verdict.
- `/retry` stays the manual verb and becomes the daemon's verb for `rejected`; `/redrive` remains
  the separate verb for async-`accepted` chunks. Two stuck states, two verbs — do not merge them.
- Demux/ffprobe failures are *recording-internal* (`app/demux.py` runs here); DP-side processing
  failures already have their own channel (inline error replies · async dead-letter via
  `/continuity`) and are out of this design's scope.

