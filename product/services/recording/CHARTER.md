# Recording Service — Charter

> Captures the user's continuous life stream — wearable body cam + computer — and lands it
> durably, losslessly, and consentfully on the backend. This is the stable doc; working state
> lives in [HANDOFF.md](HANDOFF.md); system-wide architecture + contracts in
> [../../ARCHITECTURE.md](../../ARCHITECTURE.md).

**Status:** chartered · **Last updated:** 2026-07-08

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
| **In** | On-device consent **enforcement**: pause / mute / delete-last-N-minutes; visible capture indicator | recording |
| **In** | Capture-health telemetry (per-device uptime, gaps, queue depth, battery) | recording |
| **In** | Observability: expose `/metrics` (request rate/latency/errors **+ ingest rate, capture-health, consent-gate rejections**) + own Grafana dashboard JSON in `dashboards/*.json`; Platform runs the shared Prometheus/Grafana — [../../ARCHITECTURE.md](../../ARCHITECTURE.md) §Observability | recording |
| **Out** | Interpreting/enriching the stream (ASR, diarization, timestamp injection, world data) | data-processing |
| **Out** | Interactive chat requests + their capture devices | input |
| **Out** | Consent policy + the consent-record store/gate ("no consent record ⇒ no ingest") — [ARCHITECTURE.md](../../ARCHITECTURE.md) §Ownership splits | platform (recording is fallback owner if platform isn't ratified) |
| **Out** | Durable custody of `/raw` and all stores, incl. purge primitives (we only write via ingest; platform orchestrates deletion) | storage |
| **Out** | Fine-tuning on the stream | continuum |
| **Out** | Shared infra (GCP project, identity, CI) | platform |
| **Out** | Mobile screen capture | deferred v0 decision (iOS forbids full-screen recording to private servers) |

---

## Position in the system

Head of the pipeline: nothing upstream but the user. Downstream, data-processing consumes
our output; everything past that is theirs.

| Contract | Our role | One-line role |
|---|---|---|
| **C1** recording → data-processing | **We own the producing side** | Raw stream envelope (user_id, device_id, modality, codec, wall-clock t_start/t_end, sequence no, optional device location, blob ref) per uploaded chunk; blobs land in storage `/raw` |
| C3 / C8 | none — boundary marker | Interactive requests go through input/QueryBuilder, never through us; we carry only the passive life stream |

Contract payloads are defined in [../../ARCHITECTURE.md](../../ARCHITECTURE.md) § Contracts —
reference by ID, never restate. Sibling scope lives in each sibling's charter under
`product/services/`.

---

## v0 deliverables

Ordered; each milestone ships client and/or ingest pieces together with its exit test.

| M | Deliverable | Exit criterion |
|---|---|---|
| M0 | **Ingest spine**: C1-conformant ingest endpoint; chunked upload → storage `/raw` blob + envelope to data-processing; idempotent retry (sequence-no dedupe); device auth token issuance | Synthetic client streams 24 h across forced disconnects/restarts: zero loss, zero dupes, all envelopes validate against C1 fixtures shared with data-processing |
| M1 | **Computer capture v0**: screen recording app + mic + webcam for the pilot desktop OS; local chunker, offline queue, pairing flow | One full pilot workday captured end-to-end (screen, mic, webcam frames — data-processing M2's input); blobs replayable from `/raw`; gap report empty or every gap explained |
| M2 | **Consent controls v0**: on-device enforcement — pause / mute / delete-last-N-minutes on every client; upload holdback buffer so deletes execute on-device; always-visible capture indicator; ingest backed by platform's consent-record gate (§Ownership splits) | Red-team test: delete-last-10-min leaves zero bytes server-side; pause takes effect ≤ 2 s and is visibly indicated; no capture path bypasses the controls; ingest refuses streams with no consent record |
| M3 | **Wearable body cam v0**: hardware pick (**camera + mic; no speaker** — speech output routes to the mobile app, §Ownership splits) + capture client, on-device buffer sized for offline hours, opportunistic Wi-Fi upload, pairing | Full-day wear test by a pilot user: footage lands with correct wall-clock timestamps; battery + thermal + gap numbers published |
| M4 | **Browser extension**: in-browser capture complementing the screen recorder (page/tab context the OS-level recorder can't attribute) | Extension stream flows through the same chunk/retry/consent path as M1; C1 envelopes carry the browser device_id/modality |
| M5 | **Fleet telemetry + pilot hardening**: capture-health dashboard, automatic gap/staleness alerting, crash watchdogs | Handful-of-users pilot fleet streaming for 7 consecutive days with measured per-device uptime; every gap auto-flagged, none discovered manually |
| M6 | **Metrics + dashboard** (D9, [../../ARCHITECTURE.md](../../ARCHITECTURE.md) §Observability): `/metrics` endpoint + `dashboards/*.json`; Platform owns the shared Prometheus/Grafana backbone | Service `/metrics` scraped by the shared Prometheus; dashboard shows request rate/latency/errors + ingest rate, capture-health, consent-gate rejections |

Consent (M2) intentionally lands **before** the wearable (M3): no always-on camera leaves the
lab without pause/delete working.

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
   battery, disk, and upload budgets. Joint decision with data-processing.
4. Chunk duration for C1: retry cost vs. ingest latency vs. blob count. Tune with
   data-processing against their pipeline granularity.
5. Pilot desktop OS: which OS(es) do the actual pilot users run? Pin the fleet; don't build
   three clients for a handful of users.
6. Device identity/auth: platform-owned identity with device-scoped tokens, or self-issued
   until platform exists? Needs platform charter alignment.
7. Bystander audio/video: policy is platform's call (§Ownership splits — platform decides,
   we enforce): two-party-consent jurisdictions, default mute zones, wearable indicator
   brightness/placement. Needs platform's decision before M3 wear tests.

**Research**
8. Capture-everything vs. activity-gated capture (VAD/motion gating): gating saves battery and
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

## Related work

- [poc/live_video_chat](../../../poc/live_video_chat/HANDOFF.md) — phone camera/mic → backend
  upload path: MediaRecorder quirks, HTTPS-only capture on iOS, ffmpeg clip normalization.
  Directly informs client capture + ingest normalization.
- [poc/live_stream_stability](../../../poc/live_stream_stability/HANDOFF.md) — what the
  downstream consumer of a life stream looks like (chunked 20-min video, manifests as the
  spine, GCS as bulk-data source of truth); our chunking should make that shape cheap.
- Outside precedents: Rewind/Limitless (continuous screen + pendant capture, local-first
  buffering), Meta Ray-Ban capture-indicator norms — useful bar for consent-signal design.
