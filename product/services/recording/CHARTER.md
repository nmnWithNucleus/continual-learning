# Recording Service — Charter

> Captures the user's continuous life stream — wearable body cam + computer — and lands it
> durably, losslessly, and consentfully on the backend. This is the stable doc; working state
> lives in [HANDOFF.md](HANDOFF.md); system-wide architecture + contracts in
> [../../ARCHITECTURE.md](../../ARCHITECTURE.md).

> ### ⚠️ STAGE: PROTOTYPE (pre-dev, pre-production) — D19, 2026-07-27
> This charter is written in a production voice. **It is aspirational, not a commitment.** We are
> building one end-to-end product that genuinely works, as fast as we can honestly get there.
> **Licensed:** re-cutting contracts rather than versioning them (a pinned shape is stable enough
> to build against today, never immutable — which is why we no longer call one *frozen*); wiping
> and re-collecting stored data rather than migrating it; deferring durability work with the
> reason written down.
> **Not licensed:** skipping [ORG.md](../../ORG.md)'s contract-edit order, leaving a decision
> unrecorded, silent breakage, or calling a thing BUILT when it is only ratified.
> Full posture + what changes at dev/prod: [ARCHITECTURE.md](../../ARCHITECTURE.md) §Stage.


**Status:** chartered; **capture M1 + computer surfaces alpha-complete** (see §v0 deliverables
milestone-progress note) · **Last updated:** 2026-07-19

---

## Mission

Own everything between the user's senses and our backend: the wearable body-cam client
(camera + mic), computer capture (screen recording app, browser extension, microphone,
webcam), and the ingest endpoints they stream to. Deliver the raw life stream to data-processing as
C1 envelopes with zero silent loss — every gap known, every byte timestamped and attributable
to a device. We are the privacy front line: on-device consent controls (pause / mute /
delete-last-N-minutes) are a first-class deliverable with the same bar as capture itself.
No capture fidelity is worth a consent violation.

---

## Scope — v0

| | Item | Owner |
|---|---|---|
| **In** | Wearable body-cam client: camera + mic capture, on-device buffering, opportunistic upload | recording |
| **In** | Computer capture: screen recording app, browser extension, microphone, webcam | recording |
| **In** | Backend streaming/ingest endpoints; blob landing in storage `/raw` (via ingest) + C1 envelope emission | recording |
| **In** | Chunking, retry, offline queueing on all clients | recording |
| **In** | Device pairing + device auth | recording |
| **In** | Device location capture where hardware allows — fills C1's optional location field (data-processing's geo-enrichment source) | recording |
| **In** | **Civil-time capture** — every client reports the device's zone and UTC offset alongside each chunk ([↓](#civil-time-capture)) | recording |
| **In** | On-device consent **enforcement**: pause / mute / delete-last-N-minutes; visible capture indicator | recording |
| **In** | Capture-health telemetry (per-device uptime, gaps, queue depth, battery) | recording |
| **In** | Observability: expose `/metrics` and own the Grafana dashboard JSON; Platform runs the shared backbone ([↓](#observability)) | recording |
| **Out** | Interpreting/enriching the stream (ASR, diarization, timestamp injection, world data) | data-processing |
| **Out** | Interactive chat requests + their capture devices | input |
| **Out** | Consent policy + the consent-record store/gate ("no consent record ⇒ no ingest") — [ARCHITECTURE.md](../../ARCHITECTURE.md) §Ownership splits | platform (recording is fallback owner if platform isn't ratified) |
| **Out** | Durable custody of `/raw` and all stores, incl. purge primitives (we only write via ingest; platform orchestrates deletion) | storage |
| **Out** | Fine-tuning on the stream | continuum |
| **Out** | Shared infra (GCP project, identity, CI) | platform |
| **Out** | Mobile screen capture | deferred v0 decision (iOS forbids full-screen recording to private servers) |

---

### Civil-time capture
> `built` 2026-07-26 · [D17](../../DECISIONS.md)

**In one line.** Every capture client reports the device's IANA timezone (`device_tz`) and its UTC
offset in minutes (`device_utc_offset_minutes`) alongside each chunk's UTC `t_start`/`t_end`.

**Rules**

- One line per client — `Intl.DateTimeFormat().resolvedOptions().timeZone` in the browser clients.
- Downstream nobody derives or guesses it: data-processing passes it through, storage persists it,
  and continuum renders from it.

**Why it's this way**

- **We are the only service that can know these.** The zone is a fact about where the user
  physically was at that instant, and our clients already compute the local time and discard the
  zone converting to UTC.

### Observability
> `built` (M6) · [D9](../../DECISIONS.md)

**In one line.** We expose `/metrics` and own the Grafana dashboard JSON in `dashboards/*.json`;
Platform runs the shared Prometheus/Grafana backbone.

**Rules**

- Emit request rate, latency and errors, **plus** ingest rate, capture-health, and consent-gate
  rejections.
- Shape: [../../ARCHITECTURE.md](../../ARCHITECTURE.md) §Observability.

### C1 — the producing side
> `built` · v0 pinned by [D11](../../DECISIONS.md) · `device_tz` additive 2026-07-26 ([D17](../../DECISIONS.md))

**In one line.** We write the bytes to storage first, then push an envelope telling
data-processing where they landed.

**Shape** — two legs.

- **Blob leg** — we `PUT` the raw bytes to storage `/raw` **first**, and storage mints an opaque
  `blob_ref`.
- **Envelope leg** — we **push** the C1 envelope to data-processing: `user_id`, `device_id`,
  `stream_id`, `sequence`, `chunk_id`, modality, codec, wall-clock `t_start`/`t_end`, `blob_ref`
  plus sha256 and bytes, optional device location and clock, and the optional `device_tz` +
  `device_utc_offset_minutes` added by D17.

**Rules**

- Delivery is **at-least-once**; dedup on `chunk_id`.
- Gaps are detected via dense `(stream_id, sequence)`. The `sequence` is zero-based and dense per
  `stream_id`, and it is for gap/continuity only — it is *not* the dedup key.
- **Blob-first**, always: the bytes are durable before anything claims they exist.

## Position in the system

Head of the pipeline: nothing upstream but the user. Downstream, data-processing consumes
our output; everything past that is theirs.

| Contract | Our role | One-line role |
|---|---|---|
| **C1** recording → data-processing | **We own the producing side** | **v0 pinned (D11).** Blob first, then the envelope ([↓](#c1--the-producing-side)) |
| C3 / C8 | none — boundary marker | Interactive requests go through input/QueryBuilder, never through us; we carry only the passive life stream |

Contract payloads are defined in [../../ARCHITECTURE.md](../../ARCHITECTURE.md) § Contracts —
reference by ID, never restate. Sibling scope lives in each sibling's charter under
`product/services/`.

---

## v0 deliverables

Ordered; each milestone ships client and/or ingest pieces together with its exit test.

| M | Deliverable | Exit criterion |
|---|---|---|
| M0 | **Ingest spine**: chunked upload → blob to storage `/raw` first → push the C1 envelope; idempotent retry; device auth token issuance | a synthetic client streams 24 h across forced disconnects and restarts: zero loss, zero dupes, every envelope validates against the C1 schema |
| M1 | **Computer capture v0**: screen recording app + mic + webcam for the pilot desktop OS; local chunker, offline queue, pairing flow | One full pilot workday captured end-to-end (screen, mic, webcam frames — data-processing M2's input); blobs replayable from `/raw`; gap report empty or every gap explained |
| M2 | **Consent controls v0**: on-device pause / mute / delete-last-N-minutes on every client; an upload holdback buffer; an always-visible capture indicator | a red-team test: delete-last-10-min leaves zero bytes server-side; pause takes effect ≤ 2 s; no capture path bypasses the controls |
| M3 | **Wearable body cam v0**: hardware pick (camera + mic, no speaker) plus capture client, on-device buffer, opportunistic Wi-Fi upload, pairing | a full-day wear test by a pilot user: footage lands with correct wall-clock timestamps; battery, thermal and gap numbers published |
| M4 | **Browser extension**: in-browser capture complementing the screen recorder (page/tab context the OS-level recorder can't attribute) | Extension stream flows through the same chunk/retry/consent path as M1; C1 envelopes carry the browser device_id/modality |
| M5 | **Fleet telemetry + pilot hardening**: capture-health dashboard, automatic gap/staleness alerting, crash watchdogs | Handful-of-users pilot fleet streaming for 7 consecutive days with measured per-device uptime; every gap auto-flagged, none discovered manually |
| M6 | **Metrics + dashboard** (D9): a `/metrics` endpoint plus `dashboards/*.json`; Platform owns the shared backbone | `/metrics` scraped by the shared Prometheus; the dashboard shows request and ingest metrics ([↓](#observability)) |

~~Consent (M2) intentionally lands **before** the wearable (M3)~~ — **re-sequenced 2026-07-18
(D13, founders):** consent controls move to the back-burner while the capture surfaces + learn
loop mature; they land **before any non-team pilot user** (beta testers are consenting
teammates). The M2 red-team exit bar is unchanged whenever it lands. Milestone numbers keep
their names (M-numbers are identifiers, not a fixed order — sequencing is owned by the
founders' board + this note).

**Milestone progress — capture M1 + computer surfaces (ALPHA COMPLETE 2026-07-19):** the
recording service was wrapped to the alpha bar (detail: [HANDOFF.md](HANDOFF.md)). Delivered:
the M0 ingest spine hardened into a **checked "zero silent loss" guarantee** (continuity
ledger + DP break/dup detector + two-leg gap report), the **fuller ASR pipeline** (faster-whisper
standing + VAD gate), **VAD-cut chunking** (OQ4 → D-M1-2), and **three capture clients** on one
`/capture/*` wire, each alpha-verified `clean` on real hardware:
- **Phone web** (`clients/web/`) — mic + camera over HTTPS/tunnel; the bodycam stand-in + the
  beta press-record surface. (Not an M-milestone itself; M3 wearable hardware swaps in later.)
- **Browser extension** (`clients/extension/`) — **M4 essentially met** (consent path deferred,
  D13): passive active-tab capture (video+audio via `tabCapture`, D-E7), flows through the same
  chunk/retry path, C1 carries the browser `device_id`/modality.
- **Mac CLI** (`clients/mac/`) — **partial M1**: screen + mic capture (ffmpeg avfoundation) with
  the offline queue, real-avfoundation verified. **Still open for full M1:** webcam, the pairing
  flow, and a full-workday soak (alpha was minutes, not a workday); a mac menu-bar/GUI app
  (ScreenCaptureKit, visible capture indicator) is a later surface — capability exists via the CLI.
Client transport pinned **segmented-HTTP for all v0 surfaces** (D-M1-5; streaming ingest a
deferred additive leg — recorded on the founders' board as D14).

---

## Open questions

**Engineering**
1. Wearable hardware: off-the-shelf body cam with an SDK vs. small custom build (RPi-class) —
   drives buffering, codec, and upload design. The device is **camera + mic only (no
   speaker)** — speech output is routed to the mobile app (§Ownership splits). Split: we own
   device + capture firmware; input owns interaction UX. Decide before M3.
2. Delete-last-N-minutes after upload: v0 lean is an on-device **holdback buffer** (chunks
   upload only after the delete window expires) so deletion never needs downstream cooperation.
   If holdback latency is unacceptable for data-processing, server-side deletes fall through
   to storage's `/raw` purge primitives (platform orchestrates) — raise with CTO/ARCHITECTURE.md.
3. Codec/bitrate ladder: what fidelity does data-processing actually need per modality? Sets
   battery, disk, and upload budgets. Joint decision with data-processing. **Alpha datapoint
   (2026-07-19):** mac screen video at the CLI default (`--max-width 1728`, CRF 28) is readable
   but soft on fine text — `--max-width 2560+` is the current user lever; per-modality fidelity
   targets remain this open joint decision, not a per-client flag.
   **Resolved per-modality (2026-07-19, joint recording × data-processing)**, with the real
   pipelines standing: faster-whisper ASR, pyannote diarization, AST acoustic and Qwen3-VL
   keyframe captioning plus OCR, all node-7-verified.

   **Why it's this way**

   - **Audio → 16 kHz mono is the fidelity ceiling that matters.** ASR (Whisper), diarization
     (pyannote) and acoustic tagging (AST) are all 16 kHz-native, so a higher sample rate or
     bitrate buys the models nothing.
   - Recording already demuxes to `audio/wav` 16 kHz mono s16le, which is ASR-native and exactly
     right, so **no audio bitrate ladder is needed**. Capture can use whatever codec the device
     prefers — webm/opus, m4a/aac — because the demux normalizes it.
   - **Video → resolution-bound, not bitrate-bound**, and data-processing wants container-copy at
     capture quality. Keyframe VLM captioning downscales frames to `VIDEO_FRAME_MAX_WIDTH=768`
     before the caption, so body-cam and webcam video is caption-bound and 768-px-sufficient.
   - The **exception is OCR-heavy screen capture**: the OCR-strong VL pass reads on-screen text
     from keyframes, and fine text needs enough *capture* resolution. The alpha's
     `--max-width 1728` is soft on small text, so **`--max-width ~2560` for text-dense screens**.
   - So data-processing's ask is **no re-encode** — container-copy, avoiding generational loss —
     **plus high capture resolution for screens**. The per-user-day cost dial is keyframe
     **cadence** (`VIDEO_KEYFRAME_INTERVAL_S` / `VIDEO_MAX_KEYFRAMES`), not video bitrate.
   - Net: no multi-rung "ladder", just one sensible per-modality target — 16 kHz mono audio,
     container-copy screen at ≥2560-px, body-cam at capture default.

   **Watch out for**

   - Revisit only if a real OCR-quality-vs-cost measurement on pilot screen-hours moves the screen
     resolution target.
4. ~~Chunk duration for C1~~ **Ratified 2026-07-18** (D-M1-2, recording × data-processing —
   [handoff/ws-d-vad-carve.md](handoff/ws-d-vad-carve.md)). It is per client and source.

   **Rules**

   - Continuous audio the server owns: **variable-length chunks cut at VAD speech pauses within
     [5 s, 30 s]**.
   - Phone web client: **fixed ~10 s edge segments**, because a recorder restart means
     MediaRecorder fragments are not self-contained.
   - Video and screen streams: **fixed windows**.
   - Data-processing's side of the pair is a VAD gate before ASR.

   **Why it's this way**

   - Pause-aligned cuts supersede the 2026-07-09 "20–30 s + overlap" lean, and exact
     `t_end[n]==t_start[n+1]` adjacency becomes a second continuity signal.
   - C1 is untouched: the pinned shape already supports variable length.
5. Pilot desktop OS: which OS(es) do the actual pilot users run? Pin the fleet; do not build
   three clients for a handful of users.

   **Watch out for**

   - **The alpha (2026-07-19) ran on macOS** (mac CLI), **Chromium/Comet** (extension) and **iOS
     Safari** (phone) — the current tester's stack.
   - That is *not* yet pinned as the pilot fleet. A Windows desktop client, if pilots need one, is
     unbuilt.
6. Device identity/auth: platform-owned identity with device-scoped tokens, or self-issued
   until platform exists? Needs platform charter alignment.
7. Bystander audio/video: policy is platform's call (§Ownership splits — platform decides,
   we enforce): two-party-consent jurisdictions, default mute zones, wearable indicator
   brightness/placement. Needs platform's decision before M3 wear tests.
8. Raw-blob upload path to `/raw`. Storage owns the bucket and custody; we are the writer. Settle
   the mint→upload→confirm handshake with storage and platform.

   **Rules**

   - **M0** proxies bytes through storage's `PUT /raw/blobs`.
   - The wearable sends **combined A/V** on the device→backend link, and **we demux** into
     per-modality C1 streams — each its own `stream_id`, same `device_id`, wall-clock-aligned.
   - C1's `modality` is per-envelope, so that split happens **here**, before emission.

   **Why it's this way**

   - **Prod lean (2026-07-09):** storage mints a **signed GCS URL**, we upload the bytes directly
     to GCS, and `blob_ref` points at that object. That is the POC's "GCS is source of truth,
     signed URLs" pattern.
   - Direct upload keeps storage from being a bandwidth bottleneck for tens-of-GB/day/user.
   - Uploads run **async and concurrent**: a new chunk starts uploading immediately, and the C1
     push fires on that chunk's **upload-complete callback**, so capture is never blocked on an
     upload.
   **STATUS (2026-07-19):** the **demux half is BUILT + proven** (`app/demux.py`, ffmpeg;
   exercised by all three alpha clients — muxed mp4/webm → separate audio + video C1 streams).
   The **transport is decided (D-M1-5 / founders' D14): segmented HTTP upload** for all v0
   surfaces (each client posts self-contained ~10 s segments to `/capture/segments`; the server
   spools → demuxes → emits). **Still open:** the **direct-to-GCS signed-URL** upload path (M0 +
   all three clients still proxy bytes through storage's `PUT /raw/blobs`); the
   mint→upload→confirm handshake with storage + platform is the remaining OQ8 work, needed before
   tens-of-GB/day/user scale.

**Research**
9. Capture-everything vs. activity-gated capture (VAD/motion gating): gating saves battery and
   volume but may starve the training signal continuum depends on. Needs a joint experiment
   with continuum once fine-tuning on real streams begins.

---

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Silent capture gaps (crash, disk full, battery, permission revoked) | Holes in the life stream; un-trainable days discovered too late | Watchdogs on-device, sequence-no continuity checks at ingest, gap alerting (M5) |
| Consent-control defect (capture while paused, delete misses bytes) | Trust destroyed, legal exposure; worst failure this service can have | Consent lands M2 before wearable pilot; red-team exit tests; holdback buffer keeps deletes on-device |
| Wearable battery/thermals can't sustain a full day | Stream truncated daily; product premise weakened | Quantify in M3 wear test; bitrate ladder; spare-battery protocol for pilot |
| Upload volume vs. pilot networks (all-day video = tens of GB/day/user) | Queues grow unbounded; data arrives too late for nightly fine-tune | On-device transcode, opportunistic Wi-Fi bulk upload, prioritize audio/text chunks |
| C1 churn while ARCHITECTURE.md settles | Rework across client fleet + ingest | Versioned envelope; shared conformance fixtures with data-processing from M0 |
| OS/browser breakage (screen-capture permission changes, extension API churn) | Fleet-wide capture outage on auto-update | Pin OS/browser versions for pilot fleet; telemetry catches regressions same-day |

---

## Team shape

v0 = one lead session + on-demand workstream agents (per HANDOFF.md workstream index).
Eventual sub-teams as the fleet grows:

| Sub-team | Covers |
|---|---|
| Wearable client | body-cam hardware bring-up, capture firmware/app, power |
| Desktop clients | screen recording app, browser extension, mic + webcam capture |
| Ingest backend | streaming endpoints, blob landing, C1 emission, device auth |
| Privacy & trust | consent controls, red-teaming, legal-posture engineering |
| Fleet ops / QA | telemetry, device provisioning, pilot support |

---

## Glossary (capture path)

Pinned so docs and sessions stay unambiguous. Moved here from the canvas 2026-07-27: a glossary is
stable reference, and the canvas is rewritten every session.

**segment** = client→server
upload unit (~10 s self-contained clip; `seq` dense per capture session) · **chunk** =
server→DP single-modality unit (one `/raw` blob + one C1 envelope; `sequence` dense per
stream) · **stream** = one continuous single-modality flow from one device session
(`stream_id` — the identity that crosses service boundaries) · **capture session** = one
start→stop on a device (press-record→stop / CLI run→Ctrl-C / extension click→click);
first-class in the ledger, **never travels past C1** (C1 carries `stream_id`, not
`session_id`) · **record** = one `/context` row conforming to the C2 contract.
Disambiguation: a **capture session** (recording) is NOT the serve-loop **chat session**
(`session_id` in C3/C4, storage `/sessions`) — qualify the word when both are in frame.

## Related work

- [poc/live_video_chat](../../../poc/live_video_chat/HANDOFF.md) — phone camera/mic → backend
  upload path: MediaRecorder quirks, HTTPS-only capture on iOS, ffmpeg clip normalization.
  Directly informs client capture + ingest normalization.
- [poc/live_stream_stability](../../../poc/live_stream_stability/HANDOFF.md) — what the
  downstream consumer of a life stream looks like (chunked 20-min video, manifests as the
  spine, GCS as bulk-data source of truth); our chunking should make that shape cheap.
- Outside precedents: Rewind/Limitless (continuous screen + pendant capture, local-first
  buffering), Meta Ray-Ban capture-indicator norms — useful bar for consent-signal design.
