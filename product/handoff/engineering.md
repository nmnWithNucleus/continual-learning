# Founders' thread — Engineering

> Running canvas for founders' engineering sessions (launch: [../PROMPTS.md](../PROMPTS.md) §E).
> Cross-service build sequencing, integration plans, infra calls. Service-local build state lives
> in each service's own `HANDOFF.md`; ratified decisions live in
> [../DECISIONS.md](../DECISIONS.md).

**Status:** seeded · **Last updated:** 2026-08-07

## Open agenda
1. **Client live-stream testing.** A real captured day flowing recording → DP → storage →
   continuum on real hardware. Four services, one sequencing question: what has to be true on
   each before a real day is worth capturing.
2. **The D9 observability backbone.** Every service emits `/metrics`; the shared Prometheus and
   Grafana do not exist ([D9](../DECISIONS.md)). It is the one item that keeps recording's M6
   exit criterion open.
3. **C5 registration and the model-directory build.** The shape pin is owed jointly by storage,
   continuum and inference before hosting C5 is more than a transport swap.

## Worklog

> **Newest first.** New entries are *prepended* directly under this heading, never appended at
> the bottom ([ORG.md](../ORG.md) §Documentation protocol). Each entry is a `### <date> — <title>`
> anchor so [DECISIONS.md](../DECISIONS.md) can point at the reasoning behind a decision by name.

### 2026-08-07 — thread reseeded

The thread's accumulated worklog described the system as it was before data-processing was
rebuilt, and it was the largest single store of that description in the tree. Under
[D29](../DECISIONS.md) it was removed whole rather than pruned entry by entry; git history holds
it. The agenda above is what is actually open across services today.
