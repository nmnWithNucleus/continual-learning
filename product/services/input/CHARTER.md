# Input Service — Charter

> The seam where a user request becomes model input: thin chat surfaces + the QueryBuilder.
> Stable doc; working state lives in [HANDOFF.md](HANDOFF.md); system-wide architecture +
> contracts in [../../ARCHITECTURE.md](../../ARCHITECTURE.md).

**Status:** chartered · **Last updated:** 2026-07-08

## Mission

Own user-INITIATED interaction end-to-end up to the model boundary: the chat surfaces
(computer app, browser extension, wearable push-to-talk voice), one request payload
envelope across all four modalities (text, speech, image, video), and the QueryBuilder —
which normalizes the raw payload through data-processing's synchronous pipeline (C8) and
assembles the UserPrompt (chat template, tags, the structure the model attends to) handed
to inference (C3). Surfaces stay thin; the QueryBuilder is the well-specified, most-iterated
component — every prompt-shape evolution lands there, nowhere else.

## Scope — v0

| | Item | Owner |
|---|---|---|
| ✅ In | Chat surfaces: computer app, browser extension, **mobile app**, wearable push-to-talk voice | input |
| ✅ In | Request payload envelope — one shape across text / speech / image / video | input |
| ✅ In | QueryBuilder: normalize via C8 → assemble UserPrompt per C3 (template, tags, structure) | input |
| ✅ In | Session/turn bookkeeping at request creation (mint session/turn ids, turn ordering) | input |
| ✅ In | Clarification-answer leg of the mentor relay: an envelope variant bound to the pending turn id, emitted as the C3 clarification-answer variant (questions reach the user as C9 mid-turn frames via output) | input |
| ✅ In | Recent-context read (C11): QueryBuilder pulls same-day grounding from storage's recency/semantic index into the UserPrompt — weights only know up to the last nightly cycle (split pinned in [../../ARCHITECTURE.md](../../ARCHITECTURE.md) § Ownership splits) | input |
| ✅ In | People-registry curation + consent UX — the small v0 surface where users review/confirm known people; data-processing matches, storage persists (split pinned in [../../ARCHITECTURE.md](../../ARCHITECTURE.md) § Ownership splits) | input |
| ✅ In | **Mobile app** (v0, CTO-ratified 2026-07-09): an interaction chat surface **and** the device that plays output's synthesized speech to connected BT headphones/earbuds (mobile is the speech-output sink, §Ownership splits). Only mobile *screen capture* is deferred — that's recording's scope (iOS restriction), not ours | input |
| ❌ Out | Passive life capture / continuous stream uplink | Recording Service |
| ❌ Out | Normalization internals (ASR, diarization, enrichment, timestamp injection) — we only call them | Data Processing Service |
| ❌ Out | Model serving, agentic harness, mentor-model calls (C7) | Inference Service |
| ❌ Out | Rendering/streaming the response back to the user | Output Service |
| ❌ Out | Persisting context/sessions (C2, C4 storage side) | Storage Service |
| ❌ Out | Fine-tuning cadence, adapter lifecycle (C5) | Continuum Service |
| ❌ Out | Infra, identity/auth, deploy | Platform Service |

## Position in the system

Upstream: the user, through our four surfaces (computer app, browser extension, mobile app,
wearable push-to-talk). Downstream: inference. Payloads for all
contracts are defined once in [../../ARCHITECTURE.md](../../ARCHITECTURE.md) § Contracts.

| Contract | Direction | Our role |
|---|---|---|
| **C8** | QueryBuilder ↔ data-processing (sync API) | QueryBuilder submits the raw request payload and gets the normalized result back — the SAME code path that processes the life-stream, exposed synchronously |
| **C3** | input → inference | We produce the UserPrompt: chat-templated multimodal request + session/turn ids + client capabilities; plus the **clarification-answer variant** binding a user reply to its pending turn (return leg of the mentor relay — questions arrive as C9 frames) |
| **C11** | storage → input (QueryBuilder) | We consume the recent-context read for same-day grounding; the index lives in storage, QueryBuilder decides what enters the UserPrompt |
| C4 | inference → storage (reference only) | Turn records are keyed by the session/turn ids we mint at request creation; we define id semantics, storage owns persistence |

Shared devices, separate paths: the wearable and the computer also feed the recording
service (C1), but interactive requests never ride that pipe — they enter through our
envelope and hit data-processing only via C8.

## v0 deliverables

| M | Deliverable | Exit criterion |
|---|---|---|
| M0 | **Interface freeze.** Pin C3 (UserPrompt schema, template/tag/capability vocab, versioning) and C8 (call shape, latency budget) with inference + data-processing leads; envelope v1 spec | Both sibling leads sign off; schemas versioned in ARCHITECTURE.md § Contracts |
| M1 | **Text thin slice.** Computer-app chat box → envelope → QueryBuilder (C8 text pass) → C3 UserPrompt accepted by inference; session/turn ids minted | A pilot user sends a text turn and gets a model response end-to-end on the dev stack |
| M2 | **QueryBuilder v1.** All four modalities normalized via C8 (speech→transcript, image, video clip); chat template + tags v1; client capabilities populated; template version stamped into every C3 payload | Golden-payload fixture suite green for all 4 modalities; interactive C8 round-trip inside the M0 latency budget (p95) |
| M3 | **All surfaces.** Browser extension chat, mobile app (chat + speech-output playback surface for output), and wearable push-to-talk voice, all emitting the identical envelope; surface-specific code limited to capture + display/playback | The same turn succeeds from all four surfaces against one unchanged backend |
| M4 | **Pilot hardening.** Idempotent request creation, payload size limits, retries, auth via platform, envelope/template version telemetry | 7 consecutive pilot days with zero failures attributable to envelope or prompt assembly |

## Open questions

**Engineering**
1. C8 interactive latency: the stream pipeline is throughput-shaped. What p95 budget do we
   pin at M0, and if it can't be met, does data-processing add a fast path with identical
   semantics (their build, our requirement)?
2. Push-to-talk ASR placement: browser/on-device speech-to-text vs server-side via C8
   (poc/live_video_chat used server-side faster-whisper). Who owns endpointing/barge-in?
3. Session semantics: what opens/closes a session, and does multi-turn history travel inside
   the C3 UserPrompt or get fetched by inference from storage /sessions? Directly bounds C3 size.
   Includes clarification-answer binding: how a reply attaches to its pending turn id, and what
   happens on timeout/abandon — joint with inference's clarification-relay OQ
   ([their charter](../inference/CHARTER.md), Engineering) and output's C9 frames.
4. Client-capabilities vocabulary in C3: exact consumer (inference vs output, for response
   modality choice) and how it versions without breaking either.
5. Wearable interactive audio transport: shared uplink with recording (C1 path) or a
   dedicated low-latency channel to our envelope endpoint?

**Research / product**
6. Template-vs-weights split: as the per-user adapter distills life context, how much
   recent/retrieved context still belongs in the UserPrompt? C11 is the concrete instance:
   what same-day retrieval (recency window, semantic top-k) QueryBuilder injects by default,
   and how that shrinks as nightly cycles absorb more. Co-iterated with inference +
   continuum evals; every change is a template version bump.
7. Turn provenance: should interactive turns carry tags distinguishing them from passive
   stream data so continuum can weight them differently at fine-tune time?

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| C8 sync path too slow for interactive use | Sluggish chat; users abandon surfaces | Latency budget pinned in the contract at M0; degraded mode = minimal normalization + explicit flag in the C3 payload, never a silent divergent code path |
| Prompt-template churn vs trained adapters (adapter trained on template vN, request built with vN+1) | Per-user quality regressions that look like model bugs | Template version stamped into C3 and into training data; rollouts coordinated with continuum + inference |
| Surface sprawl: 3 clients × 4 modalities each grow bespoke logic | Divergent payloads, unmaintainable clients | One envelope, thin clients (capture + display only), all assembly logic in the QueryBuilder |
| Interactive normalization drifts from stream normalization | Model sees request data shaped differently from its training data — defeats the C8 premise | C8 is the only normalization entry point; no local re-implementations |
| Session/turn id loss or collision | Broken conversation history, corrupt C4 turn records | Ids minted server-side at request creation; idempotency keys on the envelope |

## Team shape

v0: one lead session + on-demand workstream agents (per HANDOFF.md workstream index).
Eventual sub-teams:

| Sub-team | Owns |
|---|---|
| Surfaces | Computer app, browser extension, wearable interaction client (capture + display UX) |
| QueryBuilder / prompt | Envelope, template + tags, C8/C3 integration; works daily with inference + continuum |
| Backend reliability | Session/turn service, idempotency, limits, telemetry, CI/CD with platform |

## Related work

- [poc/live_video_chat](../../../poc/live_video_chat/HANDOFF.md) — nearest ancestor: multipart
  turn envelope (`/api/turn`), phone web surface, server-side ASR, streamed response, and the
  clip-normalization lesson (client media must be normalized server-side before the model sees it).
- [poc/live_stream_stability](../../../poc/live_stream_stability/HANDOFF.md) — the stream
  pipeline + timestamp conventions our C8 calls inherit.
- Outside precedent: OpenAI-style chat-completions multimodal message parts — the shape the
  UserPrompt's chat template ultimately serializes to for the BWM.
