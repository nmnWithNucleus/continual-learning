# Nucleus v0 — Document Style

> [ORG.md](ORG.md) decides **which file** a fact belongs in; this file decides **what it reads
> like**. Binds every node: root, service, workstream.

**Last updated:** 2026-07-29 · ratified — [D21](DECISIONS.md). The file is ratified, not this
revision; editing it is a founders' act. §Was / Changed / Now / Payoff trimmed 2026-07-28: the
example is the teaching, so the glosses restating it and the duplicate worklog-entry block went.
Rule 6's general cap raised 25 → 40 on 2026-07-29: 40 is where one idea plus its citation lands,
and past it a bullet is usually carrying a second clause.

## Before you edit

**New document.** First sentence says what it is, status line underneath. Vocabulary block if it
coins a term. Shape and mode come from [ORG.md](ORG.md).

**Appending.** ORG says where: top for worklogs and registers, nowhere for boards and charters,
which get rewritten instead. Inside a card, append to the section that owns the sentence, never by
widening a table cell.

**Editing in place.** Grep the claim across `product/`; if it has a home, edit there and link.
Rewriting a rule means restating Rules in today's words plus a dated How it got here entry, not a
parenthetical about the old one. Stamp *Last updated*.

## The ten rules

**1. Tables scan, prose reads.** A table cell is at most 20 words. At the 21st the content becomes
a card below, and the cell keeps a one-line summary and a link.

**2. Meaning before metadata.** The first sentence says what the thing is, in words a new joinee
knows. Dates, D-numbers, hashes and status words go on a status line underneath, never in front —
nobody can use "ratified in D18" without knowing what C10 is.

**3. Use the card template, in order.** A table row that outgrows its cell becomes a card.
Sections are optional; their order is not.

**4. Every change is a Was / Changed / Now / Payoff block.** Always those four labels, newest first.

**5. Bold is a budget.** One bold span per bullet or table cell, two per paragraph. No ALL-CAPS.

**6. One bullet, one idea.** Around 40 words; a second clause is a second bullet. Reasoning allows
60: Why it's this way, Watch out for, How it got here, a dated worklog entry, a learning. One
em-dash per sentence.

**7. Status is a controlled word, not a phrase.** Use the vocabulary below. Never claim a status
you must immediately qualify.

**8. One current home; link, never restate.** A fact going into a second place goes in as a link.
The second copy is the one that goes stale.

| Kind of fact | Home |
|---|---|
| A contract's shape, rules, reasoning **and its evolution** | [ARCHITECTURE.md](ARCHITECTURE.md) §Contracts |
| A ratified founders' decision | [DECISIONS.md](DECISIONS.md), cited by D-number |
| A service-local call, question or design | that service's `CHARTER.md` / `DECISIONS.md` |
| What happened on a given day | the relevant `handoff/*.md` §Worklog |
| Where we are right now | the node's `HANDOFF.md` board |

**9. Define your jargon once.** A document that introduces terms opens with a Vocabulary block,
one line per term, including the obvious ones. *Day-log*, *watermark* and *reservoir* are this
repo's coinages.

**10. Quoted material keeps its original wording.** A verbatim snapshot of a retired artifact is a
quotation, not our prose: mark it as a quote and leave it. These rules govern what we write, not
what we preserve.

## The card template

````markdown
### C10 · storage → continuum — the training-window read
> `built` 2026-07-27 · [D18](DECISIONS.md) · [c10_daylog.v1.json](contracts/c10_daylog.v1.json)

**In one line.** Continuum asks storage what to train on tonight; storage answers.

**Shape** — the wire, in a code block. Never prose.

**Rules** — the invariants a builder must honour. One imperative sentence each.

**Why it's this way** — the reasoning. Five bullets at most.

**Watch out for** — traps, accepted costs, and anything a tidy-minded reader would "fix".

**How it got here** — dated entries, newest first.
````

**One sanctioned variant.** Where there is no wire format, Shape becomes whatever plays its role,
in the same slot: ownership splits use **The split**, a list of who owns what. No third name, no
reordering.

## Was / Changed / Now / Payoff

````markdown
### 2026-07-27 — the watermark moves only on a publish
> build · storage × continuum · [D18](DECISIONS.md)

**Was** — it also advanced on `skipped_no_data`, so a night with no data burned its window.
**Changed** — collapsed to one condition: advance on publish, never otherwise.
**Now** — gate failure, crash and no data leave the watermark where it is, so the next window
is a superset of the failed one.
**Payoff** — one sentence replaces five cases. Cost accepted: an inactive user's window grows
unboundedly.
````

Inside a document the same four labels are a bullet with its date instead of a heading. Never a
prose blob — the labels are what let a reader skip an entry safely.

Kind is one of `design` · `build` · `decision` · `review` · `incident`. Name the services the entry
touched and the D-numbers it produced. Never tag an entry's importance: every session rates its own
work highly, and the honest signal is who cites it.

## Learnings

**A learning is what we tried, what stopped us, and what won instead.** It earns its place only if
it would change a future decision.

- It binds a live component → it is a **Watch out for** bullet on that component's card, not a
  learning.
- It binds nothing specific → a `## Learnings` entry on the thread, one bullet, linking the worklog
  entry it came from.

"Be careful with timezones" is not a learning. "Per-user `home_tz` could not survive travel, so the
device reports the zone per chunk ([D17](DECISIONS.md))" is.

## Growing a worklog

**Roll, never prune.** A worklog is append-only, and which entry matters later is not knowable when
you would be deleting it. When the read cost gets too high, cut the file at a date boundary into
`handoff/<aspect>-<period>.md` and leave a one-line pointer. Moving costs nothing; deleting is
unrecoverable in practice, because nobody greps git history for a rejected design.

## Status vocabulary

Two words. Anything else is prose pretending to be a status.

| Status | Means |
|---|---|
| `designed` | Pinned on paper. No code. |
| `built` | Running in code. |

Add the date reached, nothing else. A caveat is a **Watch out for** bullet, not a
qualifier on the word.

**There is deliberately no `frozen`.** At stage PROTOTYPE ([D19](DECISIONS.md)) it promises an
immutability the stage withholds, and an agent who believes it hunts for a workaround instead of
the two-line edit. The schema link carries what the word carried; bring it back at production stage.

Decision rows keep their own vocabulary and lineage rules: [DECISIONS.md](DECISIONS.md) §Stage.
Never copy them here.

## Self-check

- **Widest cell.** Find each table's widest cell; if it fails rule 1, extract a card.
- **Restatement grep.** `grep -rn "<claim>" --include=*.md product/`. More than one *current*
  home is the bug; dated worklog entries don't count.
- **Bold density.** Count bold spans against rule 5. Cut until it passes.
- **The joinee test.** Read it assuming nothing. Every coined term is in Vocabulary; every card's
  first sentence stands alone.
- **Meaning-first check.** Cover everything after a card's first sentence. Does it still say what
  the thing is?
