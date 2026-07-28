# Nucleus AI — v0 Product Workspace

> Product-focused successor to the `poc/` research directories (which continue as research
> feeders). Everything about building Nucleus v0 — vision, architecture, org, and one
> directory per service lives here.

**Last updated:** 2026-07-28

## Cold start — read in this order

1. [VISION.md](VISION.md) — what we're building and why.
2. [ARCHITECTURE.md](ARCHITECTURE.md) — the v0 system: the two loops, components, services and §Contracts.
3. [ORG.md](ORG.md) — how we work: doc protocol, session mechanics, growth rules.
4. [STYLE.md](STYLE.md) — how we write it down: the card template, the change format, status words.
5. [HANDOFF.md](HANDOFF.md) — the board: service status, escalations, what's next. Rewritten each session.
6. [DECISIONS.md](DECISIONS.md) — every ratified founders' decision, numbered. Cited everywhere, restated nowhere.
7. [PROMPTS.md](PROMPTS.md) — sample copy-paste prompt templates to launch any kind of session.

A service lead only needs 1–2 above plus their own `services/<key>/CHARTER.md` + `HANDOFF.md`.
Anyone editing any document reads 4 first.

## Map

```
product/
├── README.md            ← you are here
├── VISION.md            why (stable)
├── ARCHITECTURE.md      system + contracts C1–C14, one card each (stable)
├── STACK.md             stack registry: shared runtime, serving stack, per-service manifests (stable)
├── ORG.md               operating model + doc protocol (stable)
├── STYLE.md             document style: cards, change format, status vocabulary (stable)
├── PROMPTS.md           session launch prompt templates (stable)
├── DECISIONS.md         ratified founders' decisions D1..Dn (register, newest first)
├── HANDOFF.md           founders' board: status · escalations · next (rewritten each session)
├── onboarding/          new joinee's workbook to ramp-up on the product landscape (stable)
├── contracts/           machine-readable payload schemas (C1…C14, versioned)
├── handoff/             founders' aspect threads: `engineering`, `research`, `design`, `hiring-ops`
└── services/            one node per service. Contains CHARTER.md (stable) + HANDOFF.md (volatile)
    ├── recording/         life capture: wearable + computer → ingest
    ├── data-processing/   raw streams → timestamped enriched records
    ├── storage/           /context · /sessions · model directory
    ├── input/             chat surfaces + QueryBuilder → UserPrompt
    ├── inference/         vLLM + per-user LoRA, agentic harness, mentors
    ├── output/            delivery: text to computer, speech to mobile→BT audio
    ├── continuum/         nightly per-user fine-tuning, eval-gated
    └── platform/          infra · CI/CD · security/privacy · cost · observability
```

## Conventions

Four rules bind every session. Each is stated once, elsewhere, and linked from here.

| Rule | Home |
|---|---|
| A board is not a log: boards are rewritten, worklogs prepend | [ORG.md](ORG.md) §Documentation protocol |
| One fact, one home; link, never restate | [STYLE.md](STYLE.md) rule 8 |
| A contract changes in §Contracts first, then `contracts/`, then both canvases | [ARCHITECTURE.md](ARCHITECTURE.md) §Contracts |
| Every session ends by stamping its canvas and committing, attribution-free | [ORG.md](ORG.md) §Documentation protocol |
