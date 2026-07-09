# Nucleus v0 — Organization & Operating Model

> How we split the work, run parallel sessions, and keep everyone (human or agent) in
> context. Launch prompts live in [PROMPTS.md](PROMPTS.md); live status in
> [HANDOFF.md](HANDOFF.md).

**Last updated:** 2026-07-08

---

## The structure (and the v0 simplification)

The target hierarchy — the one we scale into — is four layers:

```
CTO + AI co-founder
  └─ Senior manager, one per service        (deep in their service, aware of siblings)
       └─ Sub-team managers, one per craft  (backend, UI, research, CI/CD, security…)
            └─ Sub-team members             (agents now, humans as we hire)
```

**For v0 we run exactly two of those layers, and grow the other two on demand:**

| Layer | v0 reality | Instrument |
|---|---|---|
| Founders | CTO + AI co-founder, working sessions by aspect | [HANDOFF.md](HANDOFF.md) + `handoff/<aspect>.md` |
| Service lead (= "senior manager") | **One session launched with the service's charter** — the role is the session, not a standing entity | `services/<key>/CHARTER.md` + `HANDOFF.md` |
| Sub-team manager | **Deferred** — created only when a service's canvas shows sustained parallel workstreams | `services/<key>/handoff/wsN-*.md` (ws pattern) |
| Sub-team member | Worker agents spawned by a lead for scoped tasks | Same ws file, Worklog section |

Why the simplification (this is the "simpler solution" answer):

1. **A role with no work queue is overhead.** With 8 services and zero humans, a standing
   cast of 15 senior managers + N sub-team managers is fiction to maintain. A "senior
   manager" *is* a session reading its charter + canvas cold; it exists while work exists.
   This was proven in the POCs: `live_video_chat` shipped with 6 parallel workstream agents
   + 1 integrator, coordinated purely by pinned contracts and ws-files — no managers at all.
2. **Documents are the org chart.** Reporting lines are reads/writes on handoff files, not
   meetings. A superior "checks in" by reading the canvas; a subordinate "reports" by
   updating it and flipping its status row. This survives session death, model swaps, and
   (later) humans joining.
3. **Contracts before fan-out.** The only rule that makes parallelism safe: a piece of work
   may be parallelized only after the interfaces it touches are pinned in
   [ARCHITECTURE.md §Contracts](ARCHITECTURE.md). Change a contract → edit that section
   first, then note it in both affected services' canvases.

## Documentation protocol

**Two files per node, one format.** Every node in the org (product root, each service, each
workstream) maintains:

| File | Nature | POC ancestor |
|---|---|---|
| `CHARTER.md` (root: README/VISION/ARCHITECTURE/ORG) | **Stable** — mission, scope, interfaces, milestones. Changes deliberately. | POC `README.md` |
| `HANDOFF.md` | **Volatile** — working canvas: status tables, current state, next, gotchas. Updated every session. | POC `HANDOFF.md` |
| `handoff/<ws>.md` | Per-workstream working file, opened on demand | POC `phase-N-*.md` / `wsN-*.md` |

**Deliberate deviation from the original plan — one format, not two.** The original intent
was parallel human-readable and AI-readable copies at every level (4+ docs per node). We
drop that: **structured markdown is already both.** Agents parse the same headings, tables,
and links humans read; two copies of one truth guarantees drift, and every stale copy
poisons every future session that cold-starts from it. If a machine-consumable index ever
becomes necessary (e.g. a dashboard), we *generate* it from the markdown — never hand-author
it twice. (CTO to ratify; reversible if it fails us.)

**Rules (inherited from the POCs, now law):**

- **One fact, one home.** Shared truths (infra, contracts, conventions) live once — root
  docs or the owning charter — everything else links. Never restate a sibling's internals.
- **Stamp your work.** Every canvas edit updates *Last updated* + owner session; finishing a
  workstream flips its status row in the index table.
- **Don't scatter.** No stray READMEs/notes in working directories; everything routes
  through the node's canvas and is referenced by filepath.
- **Cold-start guarantee.** Charter + canvas together must be enough for a fresh session to
  be productive *without asking anyone*. If a session had to ask, the docs were the bug —
  fix them in the same session.
- **Commits: clean, professional, no attribution** (pinned globally).

## Session mechanics

- **Launching work** = opening a new session (Cursor/Claude Code tab) and pasting the
  matching prompt from [PROMPTS.md](PROMPTS.md). Prompts encode the read-order and the
  end-of-session duties, so any model/agent can be slotted in.
- **Git is the message bus.** Sessions communicate by committing doc + code updates;
  superiors monitor by reading canvases (and `git log`), not by being present. Frequent
  small commits; the repo is always the ground truth.
- **Escalation path:** worker → its ws file → service HANDOFF → founders'
  [HANDOFF.md](HANDOFF.md) (the `Escalations` section) → a founders' session resolves it and
  writes the decision back down the same path.
- **Parallelism discipline:** fan out only what has pinned contracts (rule 3 above). The
  integrator role (a session that wires parallel outputs together, like `live_video_chat`
  WS6) is opened per fan-out, not standing.
- **POCs are reference, not source** (CTO decision, [ARCHITECTURE.md §Decisions](ARCHITECTURE.md)).
  The `poc/` projects were built to answer research questions fast, not to production standard.
  Sessions mine them for **learnings, contracts, and de-risking** — never lift-and-shift their
  code. Every production path is written fresh, to fit this product's architecture. Cite a POC
  as *reference*; if you catch yourself copying a file, stop and re-derive it.

## Growth rules (when to add the deferred layers)

- A service canvas holds **3+ concurrently-active workstreams for 2+ weeks** → give it
  standing sub-team structure (named ws-files per craft, a manager session cadence).
- A craft (e.g. backend) spans **3+ services with shared idioms** → consider a horizontal
  guild doc under `product/` rather than per-service duplication.
- **Hiring humans** changes nothing structurally: a human slots into exactly the same
  node, reads exactly the same charter + canvas, and stamps the same files. That is the
  point of one format — day-one onboarding is "read these two files."

## Founders' working areas

Aspect canvases under `handoff/` keep our own cross-service threads separate and
launchable ([PROMPTS.md](PROMPTS.md) §Founders' session):

| Aspect | File | What lives there |
|---|---|---|
| Engineering | [handoff/engineering.md](handoff/engineering.md) | Cross-service build sequencing, integration plans, infra calls |
| Research | [handoff/research.md](handoff/research.md) | Research agenda: continual-learning stability, mentor policy, MoE-users; POC ↔ product bridge |
| Design / UX | [handoff/design.md](handoff/design.md) | Surfaces, wearable interaction, output UX, product feel |
| Hiring / Ops | [handoff/hiring-ops.md](handoff/hiring-ops.md) | Role definitions, agent-vs-human staffing, vendor/compliance ops |
