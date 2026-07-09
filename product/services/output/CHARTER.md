# Output Service — Charter

> The last hop of every turn: take the grounded response stream that inference produces and land
> it on the right device in the right form. This is the stable doc; working state lives in
> [HANDOFF.md](HANDOFF.md); system-wide architecture + contracts in
> [../../ARCHITECTURE.md](../../ARCHITECTURE.md).

**Status:** chartered · **Last updated:** 2026-07-08

---

## Mission

Deliver every response the system generates to the user — streaming text on the computer
surfaces, synthesized speech to the mobile app (which plays it to connected headphones/earbuds,
since the v0 wearable has no speaker) — with the right render format per surface,
a delivery ack per turn, and sane failure handling when a device drops mid-stream. Output is
**deliberately the thinnest v0 service**: it generates nothing and captures nothing; it moves
what inference produced to where the user is. Its future is the **proactive channel**
(notifications, nudges, coach-mode interventions) — sketched below, explicitly not built in v0.

---

## Scope — v0

| | Item | Notes |
|---|---|---|
| **In** | Streaming text delivery to computer surfaces (browser extension, computer app) | token-by-token relay of the inference stream |
| **In** | Speech delivery to the **mobile app** (→ connected BT headphones/earbuds) | TTS synthesis + audio streaming; the default speech sink until a speaker-equipped wearable exists (§Ownership splits) |
| **In** | Mid-turn clarification-frame delivery | mentor clarification questions arrive as C9 frames and render as a **distinct message type** on the origin surface (the relay loop is inference's OQ); answers return via input's C3 clarification-answer variant — not through us |
| **In** | Render formats | markdown for text surfaces; audio to the mobile app |
| **In** | Delivery acks + failure handling | per-turn ack, retry/timeout, device-offline behavior |
| **In** | Delivery-side observability — **`/metrics` + Grafana dashboard JSON** (D9) | Exposes `/metrics` (Prometheus) on :8082 — baseline request rate/latency/errors **+** delivery latency, C9 stream-relay throughput, delivery ack/failure rates, TTS latency (when the speech path lands); owns `dashboards/*.json`. Platform runs the ONE shared Prometheus/Grafana + scrapes us — see [../../ARCHITECTURE.md](../../ARCHITECTURE.md) §Observability |
| **Future (sketch only, NOT v0)** | Proactive channel | notifications, nudges, coach-mode interventions — the service's growth path |
| **Out** | Generating the response (agentic loop, mentor traffic) | Inference Service |
| **Out** | Capturing the life stream | Recording Service |
| **Out** | Capturing interactive requests, QueryBuilder | Input Service |
| **Out** | Processing/enriching stream data | Data Processing Service |
| **Out** | Persisting context/sessions | Storage Service |
| **Out** | Fine-tuning, adapter lifecycle | Continuum Service |
| **Out** | Infra, deploy, CI | Platform Service |
| **Out** | Deciding *when* to be proactive (nudge triggers) | future; inference + continuum — both charters carry the future-trigger-ownership acknowledgment; output owns only the channel |

Anything not in the **In** rows is scope creep for this service. Sibling scope lives in the
sibling charters under `product/services/`; do not restate it here.

---

## Position in the system

**Upstream:** Inference Service — output consumes the grounded response stream (C9) inference
emits after recording the turn (C4). **Downstream:** the user's devices (computer app, browser
extension, mobile app for speech) and, for delivery status, the turn record in storage.

Contracts (definitions live in [../../ARCHITECTURE.md](../../ARCHITECTURE.md) § Contracts — by
ID only, never redefined here):

| Contract | Role for this service |
|---|---|
| C3 | Read-only: the UserPrompt's session/turn ids + **client capabilities** determine target surface and render format for the reply; the *clarification-answer* variant (replies to C9 clarification frames) is input's leg — answers never route through us |
| C4 | The turn record a delivery refers to; delivery outcome should be recordable against it (ownership open — OQ2) |
| **C9** | Our primary input: the grounded response-stream envelope from inference — token/text stream, **mid-turn frames** (mentor clarification questions, status), end-of-turn metadata. Payload fields pinned with inference in M0 |

---

## v0 deliverables

| M | Deliverable | Exit criterion |
|---|---|---|
| M0 | **C9 payload pinned** — envelope fields under C9 (token/segment stream, mid-turn frames, end-of-turn metadata), surface-targeting rules, ack semantics; OQ1–OQ3 closed with inference + input | Payload details merged under C9 in ARCHITECTURE.md; sign-off is mutual — inference's M0 references us, and input signs the C3/OQ3 pieces |
| M1 | **Computer text path** — relay the inference token stream to browser extension + computer app, markdown render | A pilot-user turn streams token-by-token into the computer surface; delivery ack recorded |
| M2 | **Mobile speech path** — sentence-boundary TTS, audio streamed to the mobile app, which plays to connected BT headphones/earbuds (§Ownership splits: mobile is the speech sink; the v0 wearable has no speaker). Needs the mobile app's playback surface (input owns the app) | A wearable/mobile query gets a spoken answer end-to-end into the mobile app → BT audio; first audio within ~2 s of first token |
| M3 | **Failure handling** — per-turn acks, idempotent retry keyed by turn id, undeliverable queue, surface fallback | Injected failures (device offline, mid-stream drop) yield correct ack states; zero lost or duplicated responses |
| M4 | **Metrics + dashboard** (D9) — `/metrics` on :8082 + a Grafana dashboard JSON (`dashboards/*.json`); baseline request rate/latency/errors + delivery latency, C9 relay throughput, ack/failure rates, TTS latency. Platform owns the shared backbone ([../../ARCHITECTURE.md](../../ARCHITECTURE.md) §Observability) | Service `/metrics` scraped by the shared Prometheus; dashboard shows request rate/latency/errors + delivery latency + ack/failure rates |

---

## Open questions

**Engineering**
1. C9 payload details (the ID itself is minted): pin the envelope fields with inference —
   session/turn ids, origin surface, token/segment stream, mid-turn clarification/status
   frames, end-of-turn metadata, error frame. Blocks M0.
2. Where delivery status lives: a delivery field on the C4 turn record vs a separate delivery
   log in storage — and which service writes it.
3. Transport ownership: does output hold its own persistent per-device channel (which the future
   proactive channel needs anyway), or ride the request connection input already holds for v0?
4. TTS engine + placement: open-weights model on our H100 nodes vs hosted API; shared service vs
   per-node; consistent voice across sessions. Playback path is the **mobile app → BT audio**
   (§Ownership splits); dependency is the mobile app's audio surface (input owns the app), not
   wearable hardware. Blocks M2.
5. Cross-surface rule for v0: always reply on the origin surface? What degrades when the answer
   form doesn't fit the surface (e.g. a table, asked from the wearable)?

**Future (not v0)**
6. Proactive channel: trigger ownership sits with inference + continuum (acknowledged as future
   scope in their charters — we own only the channel); notification transport (the v0 mobile app
   gives us a push surface — APNs / FCM); rate limits + quiet hours; user consent model.

---

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Scope creep toward proactive/notification infra before the chat loop works | thinnest service becomes the slowest | Charter gates v0 to M0–M3; proactive stays future scope |
| Mobile/BT audio drops mid-stream (mobile networks, BT pairing) | partial/lost answers erode pilot trust | Acks + retry, undeliverable queue, fallback delivery to the computer surface (M3) |
| TTS latency stacks on inference latency | voice replies feel slow; unusable hands-free | Stream TTS at sentence boundaries; first-audio latency is an M2 exit criterion |
| Retries duplicate delivery | answer arrives twice — worst in audio | Idempotent delivery keyed by turn id (M3) |
| Input and output each grow a half-owned device channel | unclear reconnect semantics, duplicated sockets | Settle OQ3 with input during M0, before any transport code |

---

## Team shape

v0 = **one lead session + on-demand workstream agents** (matching the org model in
[../../ORG.md](../../ORG.md)). When the proactive channel unlocks, this service grows into:

| Sub-team | Owns |
|---|---|
| Delivery/transport backend | device channels, streaming relay, acks, retries |
| Audio | TTS pipeline, voice quality, mobile-app audio streaming (→ BT) |
| Proactive channel | notifications, nudges, coach-mode delivery UX (the growth path) |
| Reliability | delivery SLOs, failure-mode drills — shared with Platform |

---

## Related work

- [../../../poc/live_video_chat/HANDOFF.md](../../../poc/live_video_chat/HANDOFF.md) —
  **reference only, not code to lift** ([§Decisions: code provenance](../../ARCHITECTURE.md)).
  Useful precedent for M1: token streaming shape (chunked `text/plain` over a fetch reader,
  metrics tail frame, `[error]`-line convention, markdown render on the client). Study the
  learnings; write the production path fresh.
- Outside precedents: SSE vs WebSocket trade-offs for authenticated streaming POSTs; APNs/FCM
  become relevant with the proactive channel (the v0 mobile app gives us the push surface).
