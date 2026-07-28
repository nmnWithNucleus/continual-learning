# Nucleus v0 — Document Style

> [ORG.md](ORG.md) decides **which file** a fact belongs in. This file decides **what that file
> reads like** once the fact is in it. Binds every node — product root, every service, every
> workstream.

**Last updated:** 2026-07-28 · **Status:** ratified — [**D21**](DECISIONS.md). The file is
ratified, not this revision of it; changing it is a founders' act.

---

## Before you touch a document

**New document.** First sentence says what it is, status line underneath. Open with a Vocabulary
block if it coins any term. Take the file's shape and its writing mode from [ORG.md](ORG.md)
§Documentation protocol.

**Appending.** ORG §Documentation protocol says where the text goes: top for worklogs and
registers, nowhere for boards and charters, which are rewritten instead. Inside a card, append to
the section that owns the sentence, never by widening a table cell.

**Editing in place.** Grep the claim across `product/` first; if it already has a home, edit there
and link. Rewriting a rule means restating **Rules** in today's words plus a dated **How it got
here** entry, not a parenthetical apologising for the old wording. Stamp *Last updated*.

## The nine rules

**1. Tables scan, prose reads.** A table cell is at most 20 words. At the 21st the content becomes
a card below the table and the cell keeps a one-line summary plus a link — a 400-word cell is prose
wearing pipes.

**2. Meaning before metadata.** The first sentence says what the thing is, in words a new joinee
knows. Dates, D-numbers, hashes and status words go on a status line underneath, never in front —
nobody can use "ratified in D18" before they know what C10 is.

**3. Use the card template, in order.** Sections are optional; their order is not.

**4. Every change is a Was / Changed / Now / Payoff block.** Four labels, always the same four,
newest first.

**5. Bold is a budget.** One bold span per bullet or table cell, two per paragraph, spent on the
words a reader must not miss. No ALL-CAPS: if a word needs shouting, the sentence is weak.

**6. One bullet, one idea.** Around 25 words. A second clause is a second bullet. One em-dash per
sentence — three is a reader losing the thread of the first.

**7. Status is a controlled word, not a phrase.** Use the vocabulary below. Never claim a status you
must immediately qualify — a word that needs a footnote redefining it does negative work.

**8. One current home; link, never restate.** Before writing a fact into a second place, write a
link instead. The second copy is the one that goes stale.

| Kind of fact | Home |
|---|---|
| A contract's shape, rules, reasoning **and its evolution** | [ARCHITECTURE.md](ARCHITECTURE.md) §Contracts |
| A ratified founders' decision | [DECISIONS.md](DECISIONS.md), cited by D-number |
| A service-local call, an open question, an internal design | that service's `CHARTER.md` / `DECISIONS.md` |
| What happened on a given day | the relevant `handoff/*.md` §Worklog |
| Where we are right now | the node's `HANDOFF.md` board |

**9. Define your jargon once.** Any document that introduces terms opens with a Vocabulary block,
one line per term, including the terms that feel obvious to whoever is writing. *Day-log*,
*watermark* and *reservoir* are coinages of this repo; nobody arrives knowing them.

## The card template

A card is what a table row becomes when it outgrows the table. Contracts use it; so does anything
else with a shape, a set of rules and a history — an ownership split, a milestone, a subsystem.

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

Every section is optional; the order never changes. A settled contract gets three lines and one
carrying a year of reasoning gets a page — either way the invariants are under **Rules**.

**One sanctioned variant.** Where the subject has no wire format, Shape becomes the thing that
plays its role and keeps its slot in the order: ownership splits use **The split**, a list of who
owns what. Do not invent a third name for the slot, and do not reorder around it.

## The change format — Was / Changed / Now / Payoff

```markdown
- **2026-07-27 — the watermark moves only on a publish.**
  - **Was** — the first draft also advanced on `skipped_no_data`, so a night with nothing
    to train burned its window.
  - **Changed** — collapsed to one condition: advance on publish, never otherwise.
  - **Now** — gate failure, freeze, crash and no data all leave the watermark where it is,
    so the next window is a strict superset of the failed one.
  - **Payoff** — one sentence replaces five cases. Cost accepted: an inactive user's window
    grows unboundedly.
```

- **Was** describes the world, not the document: "a night with no data burned its window", never
  "the first draft said X".
- **Now** stands alone, present tense, with no reference to what it replaced. It is the only part a
  builder has to read.
- **Payoff** names the cost accepted, if there was one — a note claiming none reads as marketing.

Do not use literal S/T/A/R labels.

## Status vocabulary

Two words. Anything else is prose pretending to be a status.

| Status | Means |
|---|---|
| `designed` | Pinned on paper. No code. |
| `built` | Running in code. |

Add the date the status was reached, and nothing else. A caveat is a **Watch out for** bullet, not a
qualifier on the word.

**There is deliberately no `frozen`.** While the stage is PROTOTYPE ([D19](DECISIONS.md)) it
promises an immutability the stage withholds, and an agent who believes it circles a contract
hunting for a workaround instead of proposing the two-line edit. The schema link on the status line
carries what the word carried: code validates against that shape, so breaking it fails CI. Bring
`frozen` back at production stage, not before.

Decision rows keep their own status vocabulary and lineage rules, in [DECISIONS.md](DECISIONS.md)
§Stage. Do not copy them here.

## Checking yourself

- **Widest cell.** Any table cell over 20 words is a card waiting to be extracted.
- **Restatement grep.** `grep -rn "<the claim>" --include=*.md product/`. More than one *current*
  home is the bug; dated worklog entries don't count.
- **Bold density.** More than one bold span in a bullet or cell, or two in a paragraph: cut.
- **The joinee test.** Read the section assuming nothing. Every coined term is in Vocabulary, and
  every card's first sentence stands alone.
- **Meaning-first check.** Cover everything after a card's first sentence. Does it still say what
  the thing is?
