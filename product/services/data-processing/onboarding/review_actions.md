# review_actions — data-processing service onboarding review

> The **active list only** for review of the onboarding field guide
> ([field-guide.html](field-guide.html), hosted at
> [https://claude.ai/code/artifact/760e18ff-2eb1-47e9-9b0d-c305fde223d4](https://claude.ai/code/artifact/760e18ff-2eb1-47e9-9b0d-c305fde223d4)).
> Completed rounds are trimmed once executed (the recording review set the precedent,
> CTO call 2026-07-30); every durable outcome lands in its proper home — code, the
> worklog, the board, or the charter — and this file only tracks what is still open.

**Last updated:** 2026-08-03 · the guide was brought under the teaching-view rules
([D22](../../../DECISIONS.md)) and re-verified against the repo; that pass is executed
and its outcomes live in the guide itself, same hosted URL.

## Active

- **Round 1 — CTO review of the whole guide:** pending. The guide has had an
  adversarial fact-check against code and docs (build session, 2026-07-29) but no
  human review round yet.

- **Findings surfaced while building and fact-checking the guide, awaiting a DP
  service session to triage to the board.** These are repo defects the guide
  deliberately teaches around rather than hides; none belongs to the guide itself.
  - The pyannote banner still declares the backend unverified while the same file
    carries fixes found empirically by the node-7 smoke test of 2026-07-19
    (`app/audio/diarize/pyannote.py:8-11` vs `:61-65` and `:124-127`).
  - `VIDEO_PIXEL_THRESHOLD`, `VIDEO_DELTA_GRID` and `VIDEO_OCR_LAYOUT_SPREAD` sit in
    the OUTPUT_AFFECTING allowlist but are not plumbed into the delta gate, so
    setting them re-keys the whole clip corpus with zero behavior change
    (`app/vision/config.py:231-235`, `app/vision/version.py:41`,
    `app/vision/delta.py:42-52`).
  - The clipprep docstring paragraph saying clip mode is not wired on this branch is
    stale against the tree (`app/stages/video/clipprep.py:34-41`).
  - The stage-package docstrings lag their directories: audio omits
    `injected_caption.py`, video omits the three clip stages
    (`app/stages/audio/__init__.py:1`, `app/stages/video/__init__.py:1`).
  - `sidecars/ocr/README.md:187` links `bakeoff/report.md` in lowercase — a broken
    link on case-sensitive filesystems.
  - Measured OCR throughput is 0.93–0.97 s/frame at 1728 px against the design's
    0.6 s/frame assumption (`sidecars/ocr/bakeoff/REPORT.md:210-215`) — worth folding
    into pilot CPU sizing when O-2 runs on real frames.

## How to edit the guide

[STYLE.md](../../../STYLE.md) §Teaching views governs the voice, and
[ORG.md](../../../ORG.md) §Documentation protocol governs what a teaching view is and
what it owes ([D22](../../../DECISIONS.md)): linked from the charter, the repo wins on
any disagreement, and the guide is corrected in the same session as the change it
teaches. One local convention survives here, mirroring the recording guide's: the
module-11 decisions timeline keeps bare decision ids, because there the ids are the
subject.

## Where completed outcomes live

- The D22 compliance pass (voice, meaning-first decision references, derived-view
  posture) and the drift corrections it carried — recording's client-wire rename
  checked for impact, E-6 retaught as the two-phase rejected-path design, the deploy
  quiz retaught post-D18: the guide itself, same hosted URL.
- The build and fact-check record: commit `365ae62` and the D22 revision commit that
  follows it.
