# Inference Service — Charter

> The brain of v0: serves each user's personalized model and runs the agentic + mentor loop
> that turns a UserPrompt into a grounded response. This is the stable doc — working state
> lives in [HANDOFF.md](HANDOFF.md); system-wide architecture + contracts in
> [../../ARCHITECTURE.md](../../ARCHITECTURE.md).

**Status:** chartered · **Last updated:** 2026-07-08

## Mission

Serve the personalized per-user model — the base BWM with the user's all-layer LoRA
hot-swapped in vLLM — behind an agentic harness (tools, code sandbox, think/act/observe),
and run the mentor protocol so answer quality is frontier-grade while the personal model
matures: decide when to consult Claude/GPT/Gemini, build the assistance prompt, relay
mentor clarification questions through our model to the user and back, integrate the
handoff into one grounded response, and log every turn with full mentor thinking/plan/output
traces — those traces are the training data that eventually graduates the model out of
mentorship.

## Scope — v0

**In scope**

| Area | v0 shape |
|---|---|
| Model serving | vLLM hosting the base BWM — **BWM artifact custody, hosting, and serving are ours** (pinned in [ARCHITECTURE §Ownership splits](../../ARCHITECTURE.md); the pick itself is recorded in §Decisions; upgrade migrations are continuum-executed, never hot); per-user LoRA resolved through the model directory (C6), hot-swapped per request; clean fallback to base when no eligible adapter |
| Prompt assembly | System prompt + UserPrompt (C3) → one model request |
| Agentic harness | Think/act/observe loop: tool registry, code sandbox, loop control (step caps, timeouts); tool traces recorded |
| Mentor protocol (C7) | When-to-consult policy; assistance-prompt generation (user prompt + system prompt + everything the model knows about the user); invoking Claude/GPT/Gemini; relaying mentors' clarification questions through our model to the user and back; integrating the handoff into a final grounded response |
| Response stream (C9) | Emit the grounded response-stream envelope to output: token stream, mid-turn frames (mentor clarification questions, status), end-of-turn metadata |
| Turn logging (C4) | Full turn records to storage `/sessions`, incl. complete mentor traces (thinking, plan, outputs) and tool traces — these are continuum's training data |

**Out of scope** (not chartered here — see the owning sibling's charter)

| Not ours | Owner |
|---|---|
| Training/evaluating adapters, publishing them to the model directory (C5) | continuum |
| Building the UserPrompt from raw device payloads (QueryBuilder, C8) | input |
| Delivering the response to user devices | output |
| Capturing, processing, and storing the life stream | recording / data-processing / storage |
| Cluster, SLURM, GPU allocation, shared infra | platform |

## Position in the system

Upstream: **input** hands us a ready UserPrompt; **continuum** publishes the adapters we
resolve. Downstream: **storage** receives the turn record; **output** delivers the response
we produce (C9). Payload shapes are defined once in
[../../ARCHITECTURE.md](../../ARCHITECTURE.md) § Contracts — referenced here by ID only.

| Contract | Direction | Our role |
|---|---|---|
| C3 | input → inference | **Consume:** chat-templated multimodal UserPrompt + session/turn ids + client capabilities |
| C6 | inference ↔ model directory | **Resolve:** latest eligible adapter for `user_id`; hot-swap into vLLM per request |
| C7 | inference ↔ mentor models | **Speak:** assistance prompt out; thinking/plan/response traces back; clarification relay through our model to the user |
| C4 | inference → storage `/sessions` | **Produce:** turn record incl. full mentor + tool traces |
| C9 | inference → output | **Produce:** grounded response-stream envelope — token stream, mid-turn frames (mentor clarification questions, status), end-of-turn metadata; the mid-turn frames are C7's user-facing leg |

C5 is the write side of the directory we read via C6: adapter eligibility (eval gate,
active/rolled-back status) is continuum's call; honoring it per request is ours.

## v0 deliverables

| M | Deliverable | Exit criterion |
|---|---|---|
| M0 | **Serving spine** — vLLM hosts the base BWM on the a3mega partition; C3 request → prompt assembly → streamed response emitted as C9; C4 turn record written (no mentor/tool fields yet) | A pilot C3 request returns a streamed grounded answer over C9 and its C4 record is readable in `/sessions`; C9 envelope mutually signed off with output's M0 (their M0 blocks on ours) |
| M1 | **Per-user LoRA hot-swap (C6)** — per-request adapter resolution against the model directory; base-model fallback; rollback honored | Two users on interleaved requests each hit their own adapter; swap overhead measured + published; no-adapter and rolled-back cases fall back cleanly |
| M2 | **Agentic harness v1** — think/act/observe loop, tool registry, code sandbox, loop guards; tool traces land in C4 | A multi-step tool task (incl. a sandbox code run) completes and is fully replayable from its C4 record |
| M3 | **Mentor protocol v1 (C7)** — assistance-prompt builder, Claude/GPT/Gemini invocation, clarification relay, handoff integration, full trace logging | An end-to-end mentored turn incl. one clarification round-trip; continuum signs off that the logged trace is trainable |
| M4 | **Consult policy v1 + graduation instrumentation** — start always-consult, add a skip rubric; shadow-solo answer logged per turn and blind-judged vs the mentored answer | Policy tunable per user/task from config; a standing solo-vs-mentored quality report exists for pilot traffic |
| M5 | **Pilot hardening** — concurrency across pilot users, per-stage latency budgets, mentor-outage fallback ladder | Pilot users served concurrently for a week; every failure mode degrades to a defined behavior, not a hang |

## Open questions

### Research
1. **When to consult a mentor** — what gates the call: self-reported confidence, task-type routing, a learned verifier, output entropy? v0 starts always-consult; the policy must become data-driven (feeds on M4 measurements).
2. **Graduation** — see the section below; the defining research question of this service.
3. **Context injection into assistance prompts** — how much of what the model knows about the user goes to a third-party mentor? Quality wants everything; privacy and prompt budget want a minimum. Needs a redaction/summarization policy plus an ablation.
4. **Harness design** — how much scaffolding vs letting the model drive its own loop; does the harness need to co-evolve as nightly adapters absorb more of the user's patterns?
5. **Trace structure for training** — what shape does continuum need mentor traces in (thinking/plan/final separation, tool results inline)? Joint with continuum; pinned in C4.

### Engineering
6. vLLM multi-LoRA at request granularity: adapters resident in memory, eviction policy, swap latency — all-layer LoRA is heavier than typical attention-only adapters.
7. Sandbox isolation: per-turn container vs per-user persistent env; network policy for tool calls.
8. C4 record size: full mentor traces are large — format/retention negotiated with storage.
9. Clarification-relay turns block on the user. The user-facing legs are pinned: questions go out as C9 mid-turn frames (output delivers), answers come back as the C3 clarification-answer variant (input binds it to the pending turn). Open here: our pending-turn state, timeouts, resumption semantics.
10. Mentor fan-out: one mentor per turn vs an ensemble; provider selection; cost ceiling per turn.
11. Proactive triggers (future, not v0): output's proactive channel names inference/continuum jointly as trigger owner — when that channel lands, trigger generation lives here (with continuum); see output's charter OQ.

### Graduation — when does our model answer solo?
The honest v0 position: answer quality rides on the mentor protocol while the personal
model matures, and no one has a criterion for when it stops needing help. Candidate
signals: shadow-solo win-rate vs the mentored answer (blind cross-family judging),
per-task-type competence curves over nightly adapter versions, regression watch after each
C5 publish. Graduation is likely per-task-type and per-user, not one global switch, and
must be reversible. M4 exists to make this measurable before we argue thresholds; whatever
we learn feeds the consult policy (Q1) and continuum's eval gates.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| **v0 quality rides on the mentor protocol** (the key risk) | A weak assistance prompt, lossy clarification relay, or bad handoff integration caps product quality regardless of the model | Treat the mentor path as the product path: M3 before policy tuning; blind-judge mentored answers continuously |
| Graduation criterion unknown | Cut over too early → quality cliff; too late → permanent frontier-API cost + dependency | Shadow-solo instrumentation from M4 day one; graduate per task type, reversible per user |
| Mentor provider dependency | Outages/rate limits stall turns; provider ToS may restrict training on outputs | Multi-provider suite + fallback ladder ending in a solo answer with disclosure; ToS review before continuum trains on traces |
| User context leaves our boundary in assistance prompts | Pilot users' intimate life data reaches third-party APIs | Explicit pilot consent; context-minimization policy (Q3); provider no-train/retention flags where offered |
| Bad adapter reaches serving | A regressed nightly adapter speaks as "the user's model" | Serve only status-active C5 entries via C6; instant base fallback; report serving anomalies to continuum |
| Mentored-turn latency | Mentor thinking + clarification round-trips make turns feel slow | Stream progress states as C9 mid-turn frames; per-stage latency budgets (M5); consult-skip policy as it matures |

## Team shape

v0: one lead session + on-demand workstream agents (tracked in the
[HANDOFF.md](HANDOFF.md) workstream index). Eventual sub-teams:

| Sub-team | Owns |
|---|---|
| Serving | vLLM fleet, LoRA hot-swap, model-directory client (C6), latency |
| Harness | Tool registry, code sandbox, loop control, C3/C4/C9 plumbing |
| Mentor protocol | C7 end to end: prompt builder, providers, relay, handoff integration — research-heavy |
| Evals & graduation | Solo-vs-mentored measurement, consult policy, graduation research |

## Related work

- [poc/live_video_chat](../../../poc/live_video_chat/HANDOFF.md) — vLLM VLM serving on the
  a3mega partition (TP=8, streaming, video token budgets, clip normalization): direct
  precedent for M0's serving spine, plus the contracts-first parallel-workstream pattern.
- [poc/live_stream_stability](../../../poc/live_stream_stability/HANDOFF.md) — continual
  fine-tuning stability + blind cross-family judge panels: the judging patterns inform M4's
  solo-vs-mentored evaluation (the training itself is continuum's scope).
- Outside precedents: vLLM multi-LoRA serving (S-LoRA / punica lineage) for M1; agentic
  harnesses (Codex/Cursor-style think–act–observe loops) for M2; teacher-trace distillation
  literature for why C7 traces are logged in full.
