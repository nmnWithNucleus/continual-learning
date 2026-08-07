# review_actions — data-processing service onboarding review

> The **active list only** for review of the onboarding field guide
> ([field-guide.html](field-guide.html), hosted at
> [https://claude.ai/code/artifact/760e18ff-2eb1-47e9-9b0d-c305fde223d4](https://claude.ai/code/artifact/760e18ff-2eb1-47e9-9b0d-c305fde223d4)).
> Completed rounds are trimmed once executed (the recording review set the precedent,
> CTO call 2026-07-30); every durable outcome lands in its proper home — code, the
> worklog, the board, or the charter — and this file only tracks what is still open.

**Last updated:** 2026-08-05 · reader review round 2, first tranche (CTO, modules
06–07 audio focus) executed; round 1 (modules 00–05) and the D22 compliance pass
already trimmed. Outcomes live in the guide itself, same hosted URL.

## Active

- **Round 2 — CTO review of modules 08–15:** pending (video modules explicitly parked
  by the CTO until the audio path is fully understood). The 06–07 tranche
  (2026-08-05) is executed: all three stage-graph panels are now top-down lanes with
  the independent stages on the start line, per-stage required/best_effort badges,
  and injected_caption drawn in; the executor guarantees and the fingerprint-guard
  history are rewritten in plain sequential prose; module 07 gained the
  asr-fw-v0 history, the wall-clock conversion story, the capability-flip
  operations paragraph, and two full side-by-side C2 records (transcript
  discriminator-absent + caption disc "acoustic") with real hashes.

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
  - `ASR_VAD` is output-affecting but not dialect-affecting: setting `ASR_VAD=0` on the
    faster-whisper backend changes what silence transcribes to (the exact behavior
    change that justified the `asr-fw-v0 → asr-fw-v1` bump,
    `app/asr/faster_whisper.py:26-29`) while records still stamp `asr-fw-v1` — so a
    gated and an ungated fleet would upsert different bytes under identical
    record_ids. Unlike `ASR_LANGUAGE` (documented as a deliberate runtime knob), no
    decision record blesses this; candidate fixes are folding the flag into the
    version string or freezing the knob. Surfaced by the 2026-08-05 review.
  - `dashboards/data-processing.json` has no panel for `dp_pipeline_dialect`: the gauge
    is exported at `/metrics` with a documented replica-robust alert expression
    (`app/main.py:305-321`), but an operator running the D-14 flip runbook cannot see
    it on the dashboard today — they must curl `/metrics` or `/health`. A one-stat
    panel (plus the mixed-dialect alert) closes it. Surfaced by the 2026-08-05 review.
  - The `_dialect_frozen` docstring (`app/main.py:68-75`) says an operator can
    "arm / disarm the freeze on a live process during the cutover without a redeploy"
    — but a process's environment is private after start, so an external operator
    cannot flip it live; the real lever is the env file plus a restart. The per-call
    read is still right (no stale boot snapshot; tests flip it in-process) — the
    docstring's claim just overstates who can use it. Wording fix.
  - The service `HANDOFF.md:130` still documents the ASR switch as
    `asr-mock-v0 / asr-fw-v0` — stale since the VAD gate bumped the real backend to
    `asr-fw-v1` (`app/asr/faster_whisper.py:29`).
  - The translate sidecar shares the latent hole the law flags for acoustic
    (worked-table row 15, caveat A-11) but is itself unflagged: flipping
    `TRANSLATE_BACKEND` mock↔whisper rewrites the disc `"translation"` record's bytes
    under an unchanged `record_id` (row 14 reads "none — additive only" with no
    flag). Related discussion point surfaced during the 2026-08-05 review: translate's
    record *set* also depends on model output (unit dropped when the detected
    language equals the target, or on empty text), which sits uneasily beside R4's
    "must not depend on model output, decoder build" — likely tolerated as honest
    absence under R3(d), but nowhere stated. Both belong to the audio owner's triage.

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
