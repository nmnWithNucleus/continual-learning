# WS-B — Phone web client (`clients/web/`)

> Recording-led capture M1, priority 1 (founders 2026-07-18). The bodycam stand-in AND the
> structured beta handover: a press-record URL for the tester. Static HTML/CSS/JS, **no build
> step**, served by the recording server and talking to it same-origin. Reference (not lift,
> D7): `poc/live_video_chat` — iOS capture/MediaRecorder/tunnel lessons.

**Status:** built + verified E2E server-side (synthetic driver, live ports, HTTPS tunnel) —
**the real-phone tap is the tester's step** · **Owner session:** recording M1 lead

---

## Decision D-M1-1 — edge chunking via segmented recorder (pinned)

MediaRecorder `timeslice` fragments are **not self-contained** (only the first carries the
container init segment; iOS additionally documented-unreliable with timeslice — POC lesson
"take the single Blob"). Durable per-unit upload + offline queue + clean server demux all want
self-contained blobs. So the client **restarts MediaRecorder every `SEGMENT_SECONDS` (default
10 s)** — each stop yields a standalone playable A/V blob = one upload unit.

Cost, stated honestly: a small (~tens of ms) capture gap at each restart. That is a capture
reality, not a loss: upload `seq` stays dense, per-segment `t_start`/`t_end` are stamped from
the device wall-clock, and `t_end[n] < t_start[n+1]` by the restart gap. (Exact-adjacency as a
continuity signal applies to server-carved continuous sources — WS-D — not to this client.)

## Client behaviour (what to build)

Files: `clients/web/index.html`, `clients/web/app.js`, `clients/web/style.css`. Vanilla JS,
same-origin fetch, no dependencies, phone-first layout (large record button).

- **Capture:** `getUserMedia({video: {facingMode:'environment', width:{ideal:640}}, audio:true})`
  after a user tap (iOS requires the gesture). Live `<video playsinline muted autoplay>` preview.
  A "camera" toggle (default on) allows audio-only capture (`video:false`) — the mic-only
  bodycam mode. Mime pick via `MediaRecorder.isTypeSupported` in order:
  `video/mp4;codecs=avc1.42E01E,mp4a.40.2` → `video/mp4` → `video/webm;codecs=vp8,opus` →
  `video/webm` (audio-only: `audio/mp4` → `audio/webm`). iOS Safari lands on MP4/H.264+AAC.
- **Segment loop:** start recorder (no timeslice); after `SEGMENT_SECONDS` stop it; on
  `dataavailable`+`stop` enqueue `{seq, blob, t_start, t_end, mime}` (wall-clock ms stamped at
  recorder start/stop) and immediately start the next segment from the SAME MediaStream (no
  re-prompt). **Pause** = stop current segment, don't start the next; **Resume** = start next.
  **Stop** = stop current, then after the queue drains POST the end marker.
- **Session identity:** on each record-press mint `session_id` (ULID-ish from
  `crypto.getRandomValues` + `Date.now()`); `seq` dense zero-based per session. `user_id` from a
  text input (default `beta-user`, localStorage-persisted). `device_id` = `phone-web-` + a
  localStorage-persisted random suffix (stable per browser).
- **Uploader — the offline queue:** ONE serialized queue; send segment `seq` only after
  `seq-1` acked (in-order arrival by construction; server ledger still catches anomalies).
  `fetch` POST, raw blob body. Retry forever on network error / 5xx with exponential backoff
  (1 s · 2^n, cap 30 s); a 4xx is a bug — surface it in the status area, don't retry. sha256 via
  `crypto.subtle.digest` (secure context; if unavailable send `sha256=`, server computes).
  Queue is in-memory: a page reload loses queued segments — the ledger flags exactly which
  (unterminated session / missing tail). IndexedDB persistence is a later hardening.
- **End marker:** on Stop (after drain) `POST /ingest/sessions/{id}/end {last_seq}`. On
  `pagehide`/`visibilitychange`-hidden, `navigator.sendBeacon` the end marker with the last
  *enqueued* seq so a killed page still terminates the ledger session.
- **Keep-alive:** `navigator.wakeLock.request('screen')` while recording (iOS 16.4+/Chrome),
  re-acquired on visibilitychange; best-effort try/catch.
- **Status UI (minimal, honest):** recording timer, current session_id (short form), segments
  captured / uploaded / queued, last upload error, and a 5 s poll of
  `GET /ingest/sessions/{id}/report` rendering the verdict (`clean` / `gaps` / `recording`)
  plus per-stream chunks-emitted counts. That poll is the tester's "it landed" signal.

## Wire (client ⇄ recording server) — internal to recording, NOT a C-contract

Pinned jointly with WS-C; WS-C owns the server side.

- `POST /ingest/segments?session_id=&seq=&user_id=&device_id=&t_start=&t_end=&mime=&sha256=`
  — body = raw segment bytes (`application/octet-stream`). `t_*` RFC3339 UTC (ms precision),
  `mime` URL-encoded. → `{ok, session_id, seq, status:"received"|"duplicate"}`; idempotent on
  `(session_id, seq)`.
- `POST /ingest/sessions/{session_id}/end` — JSON `{last_seq}` → `{ok}`; idempotent.
- `GET /ingest/sessions/{session_id}/report` — the continuity/gap report (shape in WS-C).

## Worklog
- 2026-07-18 — spec written (decisions above); handed to the build fan-out.
- 2026-07-18 — built as specced (`index.html` + `app.js` ~600 lines vanilla IIFE +
  `style.css`; no deps, no build step): segmented recorder off one shared MediaStream;
  serialized uploader (backoff, retry-forever on 5xx/network, 4xx surfaced + dropped so the
  queue keeps moving); end marker with `sendBeacon` pagehide fallback; wake lock; report poll
  with verdict badge + per-stream chunk counts + `segment_states` drain line. A found-in-build
  race (Stop tap between `rec.stop()` and its async `onstop`) is guarded with an
  awaiting-stop flag + finishing latch. Poll-stop now also requires
  `segment_states.received == 0` (a `gaps` verdict can appear while segments still drain).
- 2026-07-18 — review round fixed two client defects: a Pause→quick-Resume race could run
  TWO MediaRecorders concurrently (resume now defers to the pending `onstop`, which
  starts the next segment itself), and the DP-missing status line counted `[lo,hi]` runs
  as 1 each (now sums chunks, preferring the ack-reconciled `missing_unacked`).
- 2026-07-18 — **verified**: syntax (deno check), served through the recording server at
  `/client/` locally AND over the cloudflared HTTPS tunnel; the full upload wire exercised
  E2E by a synthetic driver mimicking this client byte-for-byte (segments → demux → C1 →
  real ASR transcripts in `/context`; clean/gap/dup drills all behaved). **NOT yet exercised
  by a real phone browser** — MediaRecorder/getUserMedia/wake-lock behavior on iOS Safari
  awaits the tester's press-record (the POC proved the primitives on this exact leg).
