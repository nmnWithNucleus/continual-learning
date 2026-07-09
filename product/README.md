# Nucleus AI — v0 Product Workspace

> Product-focused successor to the `poc/` research directories (which continue as research
> feeders). Everything about building Nucleus v0 — vision, architecture, org, and one
> directory per service — lives here.

**Last updated:** 2026-07-08

## Cold start — read in this order

1. [VISION.md](VISION.md) — what we're building and why (context → weights; personalization).
2. [ARCHITECTURE.md](ARCHITECTURE.md) — the v0 system: the two loops, components, **§Contracts** (the spine).
3. [ORG.md](ORG.md) — how we work: doc protocol, session mechanics, growth rules.
4. [HANDOFF.md](HANDOFF.md) — live state: service status board, escalations, next.
5. [PROMPTS.md](PROMPTS.md) — copy-paste prompts to launch any kind of session.

A service lead only needs 1–2 above plus their own `services/<key>/CHARTER.md` + `HANDOFF.md`.

## Map

```
product/
├── README.md            ← you are here
├── VISION.md            why (stable)
├── ARCHITECTURE.md      system + contracts C1–C8 (stable, owns the seams)
├── ORG.md               operating model + doc protocol (stable)
├── PROMPTS.md           session launch prompts (stable)
├── HANDOFF.md           founders' working canvas (volatile)
├── handoff/             founders' aspect threads: engineering · research · design · hiring-ops
└── services/            one node per service — CHARTER.md (stable) + HANDOFF.md (volatile)
    ├── recording/         life capture: wearable + computer → ingest
    ├── data-processing/   raw streams → timestamped enriched records
    ├── storage/           /context · /sessions · model directory
    ├── input/             chat surfaces + QueryBuilder → UserPrompt
    ├── inference/         vLLM + per-user LoRA, agentic harness, mentors
    ├── output/            delivery: text to computer, speech to mobile→BT audio
    ├── continuum/         nightly per-user fine-tuning, eval-gated
    └── platform/          infra · CI/CD · security/privacy · cost (pending ratification)
```

## Conventions (short form — law lives in [ORG.md](ORG.md))

- Stable docs vs volatile canvases: CHARTER/README change deliberately; HANDOFF changes every session.
- One fact, one home; link, don't restate. Contracts change in ARCHITECTURE.md §Contracts *first*.
- Every session ends by updating its canvas + a clean, attribution-free commit.
