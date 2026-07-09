# Platform Service — Charter

> Cross-cutting foundations every other service stands on: infra, CI/CD, secrets, observability,
> security/privacy/compliance, per-user cost. Stable doc; working state lives in
> [HANDOFF.md](HANDOFF.md); system-wide architecture + contracts in
> [../../ARCHITECTURE.md](../../ARCHITECTURE.md).

**Status:** chartered · **RATIFIED as a service 2026-07-09 (D1)** · **Last updated:** 2026-07-09

## Why this service exists (ratified 2026-07-09, D1)

This service was **not in the original high-level design** — it was proposed as an addition and
**ratified by the CTO** (Decisions log D1). The original rationale, kept for the record:
Rationale: an always-on life-recording product makes privacy/compliance and shared infra
**load-bearing from day one**, not a later hardening pass. Every sibling needs the GCP project,
GPU allocation, CI/CD, secrets, observability, and a security envelope; today nobody owns them.
The alternative is folding each concern into a sibling (deletion orchestration into storage; the
consent-record store into **recording**, the named fallback owner if this service folds) —
workable, but the concerns are inherently cross-service (a deletion must reach raw blobs,
processed records, session traces, **and trained adapters**). The alternative was declined; the
concerns live here. Recording remains the named fallback owner of the consent-record store if
this service is ever unwound.

## Mission

Own the foundations the other seven services build on: the GCP project and SLURM a3mega
allocation (serving vs training), environments and CI/CD, secrets, observability
(logs/metrics/alerts), the security-privacy-compliance backbone (consent policy + records,
encryption in transit and at rest, per-user deletion orchestrated end-to-end across all stores
and trained adapters via storage/continuum delete primitives), and per-user cost tracking.
Platform owns **no product feature and no data-plane contract**; it enforces the security
envelope around all of C1–C11 and keeps the siblings fast by
providing paved roads, not gates.

## Scope — v0

| In scope | Out of scope (owner) |
|---|---|
| GCP project, IAM, quotas; SLURM a3mega allocation policy (serving vs training windows) | Device capture, upload endpoints (**Recording**) |
| Environments (dev / pilot), CI/CD conventions, deploy tooling | Stream/interactive processing pipelines (**Data Processing**) |
| Secrets management (mentor API keys, HF token, service accounts) | Schemas, DB/GCS layout for /context and /sessions (**Storage** — platform sets encryption/retention standards) |
| **Observability backbone**: run the shared **Prometheus + Grafana** (one instance, pinned port), scrape every service's `/metrics`, run the standard exporters (node/CPU, **dcgm** for GPU, DB), alert routing, and **provision each service's Grafana dashboard from the service-owned JSON**. Services own their instrumentation + dashboard JSON; Platform owns the hosting. (§Observability in ARCHITECTURE) | QueryBuilder, chat surfaces (**Input**) |
| Encryption standards in transit + at rest; network boundary; least-privilege access | Serving, agentic harness, mentor calls (**Inference**) |
| Consent policy + consent-record store (per user / device / modality) + consent gate primitive | Response delivery (**Output**); on-device consent enforcement — pause/mute/delete-last-N, capture indicators (**Recording**, its M2) |
| Per-user deletion: cross-store orchestration + proof-of-deletion, calling storage/continuum delete primitives (split pinned in ARCHITECTURE §Ownership splits) | Fine-tuning pipeline, adapter training (**Continuum**); model directory internals + per-store delete primitives incl. /raw and adapter artifacts (**Storage** — continuum publishes via C5) |
| Per-user cost tracking (ingest, storage, GPU-hours, mentor API spend) | Each service's application code and runbooks (**each service**) |

## Position in the system

Platform sits **beneath** the data plane: upstream of no contract, consumed by every sibling
(infra, secrets, CI/CD, observability primitives). Contracts are defined in
[../../ARCHITECTURE.md](../../ARCHITECTURE.md) §Contracts; platform's role per contract is the
**envelope, never the payload**:

| Contract | Platform's enforcement role |
|---|---|
| C1 recording → data-processing | In-transit encryption device→backend; consent gate (no consent record ⇒ no ingest); ingest metering per user_id |
| C2 data-processing → storage /context | At-rest encryption + retention standard; processed records are deletion targets |
| C3 input → inference | Client authn / session identity; TLS standard on the client path |
| C4 inference → storage /sessions | Turn records incl. full mentor traces are personal data: retention + deletion targets |
| C5 continuum → model directory | Adapter entries carry the training-window provenance the deletion path depends on; adapter teardown is a deletion-orchestration target (M2) |
| C6 inference ↔ model directory | Access control on adapter resolve/hot-swap; adapters never leave the security boundary |
| C7 inference ↔ mentor models | Mentor keys in secrets manager; third-party egress policy (what user data may leave, provider retention terms); per-user spend metering |
| C8 QueryBuilder ↔ data-processing | Same authn + in-transit envelope as C2/C3 applied to the synchronous path |
| C9 inference → output | TLS on the response-stream path to devices; stream + mid-turn frames are personal data in transit |
| C10 storage → continuum | Access control on training-window exports; training data never leaves the security boundary |
| C11 storage → input (QueryBuilder) | Least-privilege read scope + authn on the recent-context path — same envelope as C10 |

## v0 deliverables

| M | Deliverable | Exit criterion |
|---|---|---|
| M0 | **Estate baseline + allocation policy** — inventory GCP project, IAM, buckets, the 8-node a3mega partition; written serving-vs-training allocation (proposal: daytime serving, nightly training window for continuum, preemption rules) | Policy doc merged; any sibling can request nodes/quota via a documented path; continuum + inference sign off |
| M1 | **Security envelope v0** — secrets manager live (mentor keys + HF token out of dotfiles), TLS on all C1/C3 client paths, at-rest encryption posture set for GCS + DBs, least-privilege service accounts per sibling | No plaintext secrets in repos or home dirs; per-service envelope checklist green for every path in C1–C11 |
| M2 | **Consent + deletion v0** — consent record store (per user/device/modality) with a gate check callable by recording/input; deletion **orchestrator** that enumerates every store (raw blobs, /context, /sessions, model directory, adapters), calls storage's per-store delete primitives (storage M5) + continuum's adapter teardown, and emits a proof-of-deletion report | Test pilot user deleted across all stores incl. adapter teardown within the SLA (proposal: 72 h); rehearsal documented |
| M3 | **Observability backbone v0** — shared **Prometheus + Grafana** (pinned port), scrape config discovering each service's `/metrics`, standard exporters (node, **dcgm** GPU, DB), alert routing, and a **dashboard-provisioning path** that loads each service's `dashboards/*.json` into Grafana. Plus the per-user **cost dashboard** (ingest GB, storage GB, GPU-hours train/serve, mentor $) | Both founders open ONE Grafana URL and see every service's request rate / latency / errors (+ GPU for inference, DB for storage); a service that ships a `/metrics` endpoint + dashboard JSON appears automatically; weekly pilot cost report generates without manual steps |
| M4 | **CI/CD + environments v0** — dev/pilot environments, one deploy convention | One sibling service ships through the pipeline end-to-end |

## Open questions

**Engineering**
1. Ratification (see top note): standalone service, or concerns assigned to siblings?
2. GPU allocation shape: static node split vs time-windowed sharing of the 8 a3mega nodes between
   vLLM serving and nightly LoRA training — needs continuum + inference input (M0).
3. Consent granularity and **bystander consent** — the decision is ours: the body cam records
   third parties; two-party consent jurisdictions constrain the pilot. Platform decides the
   policy (incl. which jurisdictions pilot users live in); recording enforces it on-device
   (its OQ 7).
4. Regulatory posture for v0: GDPR/CCPA erasure + access rights, data residency for the GCS/DB
   estate — decide before pilot users onboard, not after.
5. Mentor egress terms (C7): user life-context leaves to Claude/GPT/Gemini; do we require
   zero-retention/DPA terms per provider, and is any redaction applied at the boundary?
6. Deletion SLA and scope of "delete": raw stream vs derived records vs mentor traces vs
   provider-side copies.

**Research**
7. **Deletion vs trained weights** (flagged for CTO + continuum): data deleted from stores may
   already be distilled into a user's LoRA adapter. Policy + technique needed — candidates:
   windowed retrain from retained data (C5's training-window provenance makes this tractable),
   adapter rollback to a pre-window version, or machine unlearning. Owner: platform (policy) +
   continuum (technique). Blocks a truthful "your data is deleted" claim.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Life-stream breach (a user's entire recorded life) | Catastrophic trust/legal damage; product-ending | M1: encryption, least-privilege, secrets manager, audit logs; smallest-possible access surface |
| Deletion doesn't reach adapter weights | Erasure non-compliance; broken user promise | Open question 7; C5 provenance from day one so windowed retrain stays possible |
| Bystander recording without consent | Legal exposure in two-party-consent jurisdictions | Question 3: policy + pilot-jurisdiction screen before devices ship |
| GPU contention: nightly training vs serving latency | Missed fine-tune cadence or degraded inference | M0 allocation policy with explicit windows + preemption rules; utilization metering |
| Mentor egress leaks context to third parties | Silent privacy violation via C7 | Provider retention terms + egress policy (question 5); traces logged for audit |
| Per-user cost blowout (always-on ingest + nightly H100 training) | Unit economics invisible until too late | M3 metering from day one; budget alerts per user |
| Platform becomes a gate siblings queue behind | Org-wide slowdown | Paved-road defaults + self-serve docs; platform reviews envelopes, not application code |

## Team shape

v0 = **one lead session + on-demand workstream agents** (house model). As load grows, expected
sub-teams: **infra/SRE** (cluster, environments, CI/CD), **security & privacy engineering**
(consent, deletion, encryption, audit), **compliance/policy** (with outside counsel), **FinOps**
(cost + capacity). Org conventions in [../../ORG.md](../../ORG.md).

## Related work

- [poc/live_stream_stability/HANDOFF.md](../../../poc/live_stream_stability/HANDOFF.md) — the
  current estate in practice: GCS posture (uniform bucket-level access, public-access prevention,
  signed URLs), a3mega/SLURM usage patterns, NFS-vs-localssd conventions. Seed for M0/M1.
- [poc/live_video_chat/HANDOFF.md](../../../poc/live_video_chat/HANDOFF.md) — single-node vLLM
  bring-up + cloudflared HTTPS exposure; precedent for the secure client path (C3) and deploy
  scripting (M4).
- Outside precedents for question 7: machine-unlearning literature (e.g., SISA-style sharded
  training) and GDPR Art. 17 erasure practice for ML-derived data.
