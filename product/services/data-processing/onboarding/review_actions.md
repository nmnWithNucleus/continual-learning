# review_actions — data-processing onboarding review

> The **active list only** for review of the onboarding field guide
> ([field-guide.html](field-guide.html)). Completed rounds are trimmed once executed; every
> durable outcome lands in its proper home — code, the board, or the charter — and this file
> tracks only what is still open.

**Last updated:** 2026-08-08

## Active

- **First reader review of the rewritten guide — pending.** Fifteen modules (00–14) in the house
  style the recording guide set. It wants a fresh read against the running service.

- **Re-check the dated live readings when they move.** Every volatile figure is dated in place and
  all were taken 2026-08-08. The served video dialect and the suite count go stale first.

- **Run `check-widgets.js` after any edit.** Every instrument shares one `<script>`, so a single
  syntax error silently kills them all. That happened once, on 2026-08-08.

  ```
  cd product/services/data-processing/onboarding && deno run --allow-read check-widgets.js
  ```

The guide teaches one record per chunk, slots, identity, the version law, stages and the three
honesty states, the model fleet, crash safety and healing, the downstream day-log, the decisions and
their ripples, an honest board, day-one workflow, and a closing quiz. Its instruments are an animated
slot-ribbon hero, twelve vocabulary accordions, a clickable record explorer, a dialect composer that
recomputes a real `record_id`, an eight-scenario life-of-a-chunk stepper, six flip cards, a claim-tree
walker, a decisions timeline, and seven quiz questions.

Anything a fresh read surfaces about the code goes to the [service board](../HANDOFF.md) §Next, not
here. The 2026-08-08 rewrite sent three items there: L9's shutdown clause naming the wrong cause,
the untrue "enforced in CI" claim, and the T-1/T-3 order dependence.

## How to edit the guide

[STYLE.md](../../../STYLE.md) §Teaching views governs the voice, and [ORG.md](../../../ORG.md)
§Documentation protocol governs what a teaching view owes ([D22](../../../DECISIONS.md)): it is
linked from the charter, the repo wins on any disagreement, and the guide is corrected in the same
session as the change it teaches.

## Where completed outcomes live

- The world the guide teaches: the charter's §Slot Law ([../CHARTER.md](../CHARTER.md)).
- Open engineering items: the [service board](../HANDOFF.md) §Next.
