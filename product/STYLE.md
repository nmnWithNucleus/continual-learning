# Nucleus v0 — Document Style

> How we write the documents. [ORG.md](ORG.md) decides **which file** a fact belongs in; this file
> decides **what that file reads like** once the fact is in it. Both bind every node — product root,
> every service, every workstream.

**Last updated:** 2026-07-28 · **Status:** adopted in practice, **not yet ratified** — wants a
D-number at the next founders' session, because a repo-wide writing standard binds every service.

---

## Why this exists

Our documents are read by two audiences with opposite failure modes. A model will happily parse a
430-word table cell and lose nothing. A human reads the first clause, meets the fourth em-dash, and
stops. We were optimising for the reader who never complains.

That was a real cost, not an aesthetic one. When a rule is written as one dense paragraph, the next
session extends it by appending another clause — because there is nowhere else to put it. Do that
four times and you get [ARCHITECTURE.md](ARCHITECTURE.md)'s watermark rule: the same invariant
stated in four places, three of them saying one thing and the fourth saying another. Nobody
introduced that defect. The **shape of the document** did.

So the rules below are mostly about giving every kind of sentence a named place to live, so that
"where does this go?" has an answer other than *the end of the paragraph I am already in*.

## The nine rules

**1. Tables scan, prose reads.** A table cell is at most two lines — call it twenty words. The
moment a cell needs a third, the content is not table content: it becomes a **card** below the
table, and the cell keeps a one-line summary plus a link. A table whose cells run from 25 to 430
words is not a table; it is prose wearing pipes.

**2. Meaning before metadata.** The first sentence says what the thing *is*, in words a new joinee
already knows. Ratification dates, D-numbers, commit hashes and status words go on a **status
line** underneath — never in front. A reader who does not yet know what C10 is cannot use the fact
that it was ratified in D18.

**3. Use the card template, in order.** Sections are optional; their order is not. See below.

**4. Every change is a Was / Changed / Now / Payoff block.** Four labels, always the same four. See
below.

**5. Bold is a budget.** At most one bold span per bullet, spent on the words a reader must not
miss. When a third of a paragraph is bold, nothing in it is emphasised — and that is the state we
are correcting. **No ALL-CAPS shouting**; if a word needs shouting, the sentence around it is weak.

**6. One bullet, one idea.** Twenty-five words or so. If you need a second clause, you probably need
a second bullet. No stacked em-dashes: an em-dash is a pause, and three of them in one sentence is a
reader losing the thread of the first.

**7. Status is a controlled word, not a phrase.** See the vocabulary below. `DECIDED` and `BUILT`
are different words and the difference is load-bearing — the [ARCHITECTURE.md](ARCHITECTURE.md)
§Stage banner forbids trading one for the other. And never claim a status a document has to
immediately qualify: if the word needs a footnote redefining it, the word is doing negative work.

**8. One current home; link, never restate.** [ORG.md](ORG.md)'s rule, made operational:

| Kind of fact | Home |
|---|---|
| A contract's shape, rules, reasoning **and its evolution** | [ARCHITECTURE.md](ARCHITECTURE.md) §Contracts |
| A ratified founders' decision | [DECISIONS.md](DECISIONS.md), cited by D-number |
| A service-local call, an open question, an internal design | that service's `CHARTER.md` / `DECISIONS.md` |
| What happened on a given day | the relevant `handoff/*.md` §Worklog |
| Where we are right now | the node's `HANDOFF.md` board |

If you are about to write a fact into a second place, write a link instead. The second copy is the
one that goes stale, and it goes stale within days.

**9. Define your jargon once.** Any document that introduces terms opens with a **Vocabulary**
block — one line per term, no exceptions for terms that feel obvious to whoever is writing. *Day-log*,
*watermark*, *dialect* and *reservoir* are all coinages of this repo. Nobody arrives knowing them.

## The card template

A card is what a table row becomes when it outgrows the table. Contracts use it; so does anything
else with a shape, a set of rules, and a history — an ownership split, a milestone, a subsystem.

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

Every section is optional and the order never changes. A settled, simple contract gets a heading, a
status line and three lines. A contract carrying a year of hard-won reasoning gets a page. Either
way a reader who wants the invariants knows to look under **Rules**, and a reader who wants to know
why it isn't simpler knows to look under **Why it's this way**.

**One sanctioned variant.** Where the subject has no wire format, **Shape** becomes the thing that
plays its role and keeps its slot in the order. Ownership splits use **The split** — who owns what,
as a list. Do not invent a third name for the same slot, and do not reorder around it.

Two sections earn their keep more than they look like they will:

- **Watch out for** is where a named cost goes. Costs we have accepted on purpose are our most
  fragile knowledge — they read like oversights to anyone who wasn't there, and a future session
  will helpfully remove one. Naming them is the defence.
- **How it got here** is what stops the rest of the card re-arguing itself. Once a change has a
  dated entry, the **Rules** section can simply state today's rule without the parenthetical
  apology explaining what it used to be.

## The change format — Was / Changed / Now / Payoff

Every entry under **How it got here** takes the same four labels. It is STAR in engineering
clothes: *Was* is the situation, *Changed* is the task and the action, *Now* and *Payoff* split the
result into the half a builder needs and the half a reviewer needs.

```markdown
- **2026-07-27 — the watermark moves only on a publish.**
  - **Was** — the first draft also advanced on `skipped_no_data`, so a night with nothing
    to train burned its window. The rule was written in four places and the fourth disagreed.
  - **Changed** — collapsed to one condition: advance on publish, never otherwise.
  - **Now** — gate failure, freeze, crash, no data and too-little data all leave the
    watermark where it is, so the next window is a strict superset of the failed one.
  - **Payoff** — one sentence replaces five cases, and the watermark becomes auditable by its
    own definition. Cost accepted: an inactive user's window grows unboundedly.
```

Three things to hold to:

- **Was** describes the world, not the document. "The first draft said X" is about the doc;
  "a night with no data burned its window" is about the system. Write the second.
- **Now** is the only part a builder has to read. It must stand alone, in the present tense,
  with no reference to what it replaced.
- **Payoff** names the **cost accepted**, if there was one. A change with no cost is rare, and a
  change note claiming none reads as marketing.

Do not use literal S/T/A/R labels. They read like a résumé; these read like an engineer.

## Status vocabulary

Two words. Anything else is prose pretending to be a status.

| Status | Means |
|---|---|
| `designed` | Pinned on paper. No code. |
| `built` | Running in code. |

Add the date the status was reached, and nothing else. If the status needs a caveat, the caveat is a
**Watch out for** bullet.

**There is deliberately no `frozen`,** and the omission is the interesting part. While the stage is
PROTOTYPE ([D19](DECISIONS.md)) the word promises an immutability the stage explicitly withholds —
and the failure mode is expensive and specific: an agent that reads `frozen` will circle a contract
hunting for a workaround, when the honest fix was a two-line edit to the contract itself. A status
nobody can act on correctly is worse than no status.

What `frozen` was really carrying — *a machine-readable schema exists and services validate against
it* — is carried better by the **schema link** on the status line. A link cannot drift from reality
the way an adjective can: either the file is there and CI checks it, or it is not.

Bring the word back at production stage, when it will be true. Edit this section then, not before.

**Decision rows keep their own vocabulary** — `ratified` · `BUILT` · `superseded` · `demoted` ·
`RETIRED`, capitalised only where the status changes what you may do next — along with the rule
that a D-number is an immutable handle and lineage lives in a column beside it. That belongs to the
register: [DECISIONS.md](DECISIONS.md) §Stage.

## Checking yourself

Cheap, mechanical, and each one has caught a real defect here:

- **Widest cell.** Any table cell over ~20 words is a card waiting to be extracted.
- **Restatement grep.** `grep -rn "<the claim>" --include=*.md product/`. More than one *current*
  home is the bug. Dated worklog entries keep their contemporaneous wording and don't count.
- **Bold density.** If a paragraph has more than two or three bold spans, cut until it has one.
- **The joinee test.** Read the section start to finish assuming nothing. Every coined term should
  be in Vocabulary, and the first sentence of every card should make sense on its own.
- **Meaning-first check.** Cover everything after the first sentence of a card. Does that sentence
  still say what the thing is?
