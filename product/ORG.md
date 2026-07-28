# Nucleus v0 — Organization & Operating Model

> How we split the work, run parallel sessions, and keep everyone (human or agent) in context. 
> Launch prompts live in [PROMPTS.md](PROMPTS.md); live status in[HANDOFF.md](HANDOFF.md).

**Last updated:** 2026-07-08

---

## The structure

The target hierarchy which we scale into is four layers:

```
CTO + AI co-founder + Founders
  └─ Directors, one per service             (deep in their service, aware of siblings)
       └─ Sub-team managers, one per craft  (backend, UI, research, CI/CD, security…)
            └─ Sub-team members             (agents now, humans as we hire)
```

**[v0-simplification] For v0 we run exactly two of those layers, and grow the other two on demand:**

| Layer | v0 reality | Instrument |
|---|---|---|
| Founders | CTO + AI co-founder, working sessions by aspect | [HANDOFF.md](HANDOFF.md) + `handoff/<aspect>.md` |
| Service lead (= "director") | **One session launched with the service's charter** — the role is the session, not a standing entity | `services/<key>/CHARTER.md` + `HANDOFF.md` |
| Sub-team manager | **Deferred** — created only when a service's canvas shows sustained parallel workstreams | `services/<key>/handoff/wsN-*.md` (workstream pattern) |
| Sub-team member | Worker agents spawned by a lead for scoped tasks | Same ws file, Worklog section |

Why the simplification:

1. **An org-role with no work queue is an overhead. Thus, directors are just pure agent sessions**
2. **Documents are the org chart.** Reporting lines are reads/writes on handoff files rather than meetings. A superior "checks in" by reading the canvas. A subordinate "reports" by updating it and flipping its status row. This survives session death, model swaps, and humans employees joining later.
3. **Contracts before fan-out.** The only rule that makes parallelism safe: a piece of work may be parallelized only after the interfaces it touches are pinned in [ARCHITECTURE.md §Contracts](ARCHITECTURE.md). Change a contract -> edit that section first, then note it in both affected services' canvases.

## Stage: PROTOTYPE (pre-dev, pre-production)

**Ratified 2026-07-27 (D19).** Every charter and canvas in this repo reads as though we were
shipping to production. We are not. **The goal is one end-to-end product that genuinely works,
reached as fast as we can honestly get there** — so contracts may be re-cut rather than versioned,
stored data may be wiped and re-collected rather than migrated, and durability work is deferred on
purpose with the reason recorded.

This changes what we build. **It never changes what we say about what we built.** The rules below —
contracts before fan-out, the contract-edit order, documents as the org chart — hold unchanged, and
"prototype" is never a reason to leave a decision unrecorded or to call a thing BUILT when it is
DECIDED. Full posture: [ARCHITECTURE.md](ARCHITECTURE.md) §Stage.

## Keeping documents true (learned the hard way, 2026-07-25 → 07-27)

Documents drifting from code is not a hygiene problem here; it is the **leading indicator of real
defects**. Across the D18 slice every serious bug — a day-log stamping a recipe whose knobs it never
used, a shipped default that silently trained on nothing, a `rollback()` that had quietly stopped
working — presented first as a document disagreeing with the code or with another document. **None
was caught by a test.** Twice a *test harness* was green while asserting the defect as correct
behaviour. So:

**1. Sort a doc defect by SHAPE before deciding ceremony.** Three shapes recur, and they need
wildly different responses:

| Shape | What it means | Ceremony |
|---|---|---|
| **Incomplete description** | one truth, written down only partially | fill it in everywhere; no D-number |
| **Intent/build gap** | two true statements about *different things* — the standing intent vs what v0 built | state both and name the gap; no D-number. The defect is asserting the intent in the voice of the build |
| **Prose lagging its own authoritative artifact** | the schema/code was already right; only the summary lagged | fix the prose; explicitly **not** a contract change |

**2. Inventory with a repo-wide grep, not by memory.** A "fixed in three places" item turned out to
be in five — and the two missed sites included the strongest as-though-built claim in the repo
(`VISION.md`'s *"v0 mechanism (locked)"*). `grep -rn "<claim>" --include=*.md` costs seconds.

**3. A number belongs in exactly one *current* place.** Status boards carry current figures; dated
entries keep their contemporaneous ones. Rewriting a dated entry to match today destroys the record
rather than reconciling it.

**4. State a claim's status in the row that makes it.** "DECIDED" and "BUILT" are different words
and the [ARCHITECTURE.md](ARCHITECTURE.md) §Stage banner forbids trading one for the other. When
something ships, annotate the original decision with its build date — do not rewrite what was
decided.

**5. When a review closes, fold its findings into the docs they belong to and delete the review
file.** A standing notes file holding reasoning that explains a charter is exactly the parallel copy
the protocol below forbids — and it will drift from the thing it explains.

## Documentation protocol

**Two files per node, one format.** Every node in the org (product root, each service, each
workstream) maintains:

| File | Nature | POC ancestor |
|---|---|---|
| `CHARTER.md` (root: README/VISION/ARCHITECTURE/ORG) | **Stable** — mission, scope, interfaces, milestones. Changes deliberately. | POC `README.md` |
| `HANDOFF.md` | **Volatile board** — status tables, where we are, what's next, gotchas. **Rewritten in place** every session. | POC `HANDOFF.md` |
| `handoff/<ws>.md` | Per-workstream / per-aspect working file: the reasoning and a newest-first worklog | POC `phase-N-*.md` / `wsN-*.md` |
| `DECISIONS.md` | **Append-at-top register** of ratified decisions. Cited everywhere, restated nowhere. At the root it holds the founders' D-numbers; a service opens one only when it has service-local decisions to record (see *The same three files, at every level* below). | — (new 2026-07-27) |

**Deliberate deviation from the original plan — one format, not two.** The original intent
was parallel human-readable and AI-readable copies at every level (4+ docs per node). We
drop that: **structured markdown is already both.** Agents parse the same headings, tables,
and links humans read; two copies of one truth guarantees drift, and every stale copy
poisons every future session that cold-starts from it. If a machine-consumable index ever
becomes necessary (e.g. a dashboard), we *generate* it from the markdown — never hand-author
it twice. (Ratified 2026-07-09, D2; reversible if it fails us.)

**A board is not a log — the two writing modes.** Added 2026-07-27, after `HANDOFF.md` grew to
498 lines by quietly becoming a *second* worklog: its `§Current state` and `§Next` had accumulated
~4,800 words of dated history that already lived in `handoff/engineering.md`, and — the tell — the
weaker copy went stale within a day. Nobody deletes from a section called "current state"; they
append. So the mode is now named, per section:

| Mode | Applies to | Rule |
|---|---|---|
| **Rewrite in place** | every `HANDOFF.md` (root + service), every `CHARTER.md`, `README`/`VISION`/`ARCHITECTURE`/`STACK`/`ORG` | Describe **today**. Nothing is appended; no history accumulates. When an item is done it *leaves* the board for the aspect/ws worklog — it does not stay struck through. |
| **Prepend (newest first)** | `handoff/<aspect>.md` and `handoff/<ws>.md` §Worklog · `DECISIONS.md` | Genuinely append-only history, written **at the top**, never at the bottom. A cold-starting reader hits today first and can stop reading whenever they have enough. |

Worklog entries are `### <date> — <title>` headings, not list items, so every entry is a stable
anchor other documents can point at. That is what lets a decision row carry a *pointer* to its
reasoning instead of a copy of it.

**The same three files, at every level.** The split is fractal — a service node gets exactly what the
product node gets, and for the same reasons:

| | Product node | Service node |
|---|---|---|
| Board (rewrite in place) | `HANDOFF.md` | `services/<key>/HANDOFF.md` |
| Timeline (prepend) | `handoff/<aspect>.md` §Worklog | `services/<key>/handoff/worklog.md` |
| Decisions (prepend) | `DECISIONS.md` — `D-n` | `services/<key>/DECISIONS.md` — a service prefix (`C-n`, `R-n`) |

Open a service's `DECISIONS.md` / `handoff/worklog.md` **when there is something to put in it**, not
pre-emptively — most services have neither and should not.

**Which register a decision belongs in is a question about authority, not topic.** A decision taken
inside a service's chartered autonomy is service-local and gets a service number. A decision that
**re-cuts a charter, moves a contract, or binds a sibling service** is not the service's to settle:
it is proposed in [HANDOFF.md](HANDOFF.md) §Escalations and ratified at the founders' board with a
**D-number**. A service-local row may *cite* a D-number and record its own implementation of it —
that is not a restatement, because the implementation is genuinely the service's.

**Rules (inherited from the POCs, now law):**

- **One fact, one home.** Shared truths (infra, contracts, conventions) live once — root
  docs or the owning charter — everything else links. Never restate a sibling's internals.
  **Ratified decisions live in [DECISIONS.md](DECISIONS.md) and are cited by D-number** — if a
  decision is written out in full anywhere else, that copy is the bug.
- **A table is not an essay.** This file decides *which* document a fact belongs in;
  [STYLE.md](STYLE.md) decides what that document reads like — the card template, the
  Was/Changed/Now/Payoff change format, and the status vocabulary. It binds every node, for the
  same reason the doc protocol does: when a rule has nowhere to go but the end of the paragraph it
  is already in, the next session appends, and four appends later the copies disagree.
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
- **Ask vs. decide (the founder-in-the-loop rule).** A service's **initial kickoff (Prompt A)
  is consultative**: the lead produces a kickoff brief — M0 plan + the blocking open questions
  with recommendations — and **stops for founder answers before designing or building.** Every
  **resume (Prompt B) is autonomous**: proceed from the canvas's Next, make + document decisions,
  escalate only true blockers. Cross-service / contract questions are *never* decided by one
  service session — they route to a founders' session (or a joint interface-pin), regardless
  of A vs B.
- **Lead plans and dispatches; workers advance ws files.** An A/B lead decomposes M0 into
  `handoff/wsN-*.md` files and may either build inline or fan out workers. **Prompt C** is a
  scoped worker that **presupposes its ws file already exists** — C is how you (or a human hire)
  drive one workstream directly, especially interactive/stateful work a fire-and-forget sub-agent
  can't. No ws file yet ⇒ an A/B planning pass writes it first.
- **Git is the message bus.** Sessions communicate by committing doc + code updates;
  superiors monitor by reading canvases (and `git log`), not by being present. Frequent
  small commits; the repo is always the ground truth.
- **Escalation path:** worker → its ws file → service HANDOFF → founders'
  [HANDOFF.md](HANDOFF.md) (the `Escalations` section) → a founders' session resolves it and
  writes the decision back down the same path.
- **Parallelism discipline:** fan out only what has pinned contracts (rule 3 above). The
  integrator role (a session that wires parallel outputs together, like `live_video_chat`
  WS6) is opened per fan-out, not standing.
- **POCs are reference, not source** ([D7](DECISIONS.md)).
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
