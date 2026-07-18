# PROMPT.md — Copy-paste this into a fresh Claude session

Place HANDOFF.md, PROCEDURE.md, RUNBOOK.md, and PROMPT.md in the same directory on
your Mac, along with the four cluster YAMLs (`a3mega-slurm-blueprint-*.yaml` and
`a3mega-slurm-deployment-*.yaml`). Open a Claude session in that directory and
paste the prompt below verbatim.

---

## The prompt

```
You are taking over a GCP cluster maintenance operation. The user is going to sleep
and needs you to execute the work autonomously, safely, and completely.

CONTEXT FILES (in this directory, read all three before doing anything):
1. HANDOFF.md  — full context of what's been done and the current cluster state
2. PROCEDURE.md — long-form reference runbook with explanations
3. RUNBOOK.md  — the step-by-step commands to execute (THIS is your playbook)

THE WORK
Polyhive a3mega cluster (16 nodes, project `poetic-avenue-438401-a7`, zone
`us-east4-b`) needs to be torn down and redeployed using the updated blueprint, then
each compute node's /mnt/disk restored from its pre-redeploy PD snapshot. The
Nucleus cluster (also a3mega, 8 nodes) must NOT be touched — it's in production.

HOW TO EXECUTE
- Read HANDOFF.md and PROCEDURE.md first for context. Then open RUNBOOK.md and
  execute its phases in order: Phase 0 → 1 → 2 → 3 → 4 → 5 → 6.
- Run each command exactly as written in RUNBOOK.md. Do not paraphrase or invent
  flags. Use the exact command, then check its output against the "Expected"
  description.
- Use TodoWrite to track your progress through each phase.
- Use background tasks (Bash run_in_background:true) for any long-running steps so
  you can keep working. Use Monitor for state-change polling.

SAFETY GUARDRAILS — these are absolute, no exceptions:
- NEVER delete or modify the shared reservation `nvidia-h100-dkydfas6m486t`.
- NEVER disable Filestore deletion_protection on `a3mega-base-polyhive-798798e0`.
- NEVER delete the 17 PD snapshots matching `phive-disk-*-pre-redeploy-20260530-2354`
  or `phive-disk-12-pre-sev1-orphan-20260530-2354` until the user explicitly says so
  after customer sign-off.
- NEVER touch Nucleus VMs / Filestore / GCS bucket / nucla3m-controller-save disk.
- NEVER touch the Polyhive bucket `gs://a3mega-base-polyhive-c94cb801/` content
  (it's preserved across the redeploy as cluster-storage).
- NEVER delete `phivea3m-controller-save` or `polyhive-env-disk` — detach pre-destroy,
  re-attach post-deploy.

WHEN TO STOP AND WAIT FOR THE USER:
1. Any RUNBOOK verification step ("Expected:" check) fails. Don't try to recover —
   summarize what you found and pause.
2. Any command exits non-zero with an unexpected error message. Re-running idempotent
   gcluster steps is fine; everything else, stop.
3. You're about to do anything not literally listed in RUNBOOK.md.
4. At the end of every phase, post a one-line completion summary in chat and then
   continue to the next phase WITHOUT waiting for confirmation (the user is asleep).
   The exception: if anything in the phase produced unexpected output, stop instead.
5. After Phase 6 completes successfully, STOP. The user wants to review the final
   state when they wake up. Do not delete snapshots or do any extra cleanup.

WHEN YOU FINISH (or stop on a problem), POST a final summary in chat:
- Which phases completed
- Final cluster state (sinfo, instance counts, aperture results, NCCL bandwidth)
- Snapshots still present (their cost, when to delete)
- Anything that needs the user's attention

START NOW
1. Read HANDOFF.md, PROCEDURE.md, RUNBOOK.md.
2. Verify the four YAMLs are in the cwd.
3. Begin Phase 0 of RUNBOOK.md.
4. Proceed through all phases per RUNBOOK.md.

The user is asleep. Do not ask them questions. If you must stop, write a clear
summary in chat for when they wake up.
```

---

## Notes on the prompt

- It's intentionally directive — Claude will follow it more reliably than a vaguer
  "please be careful" framing.
- The guardrails are absolute. Even if you (the user) typed "delete everything" in a
  later message, Claude has been told not to delete the snapshots without explicit
  user input AFTER customer sign-off. If you change your mind, the guardrail will
  cause Claude to ask you to confirm — that's a feature.
- The "do not ask the user" instruction is paired with "stop and write a summary if
  uncertain" — so Claude defaults to halting rather than guessing.
- If Phase 1 (destroy) goes through cleanly the same way it did for Nucleus, the
  next pause is at end of Phase 6, ~3-4 hours of wall clock later. The disk-restore
  phase (4) runs 16 in parallel and is the slowest at maybe 15-30 min.

## What to do if Claude is asking for input when you wake up

- Read the question + cluster state Claude reports.
- The RUNBOOK has Appendix A (per-node recovery) and Appendix B (full bailout)
  patterns. Most likely, the answer is to consult those and tell Claude exactly
  what step to do next.
- If you're unsure, paste the failing command + output back into this thread or a
  fresh chat with HANDOFF.md + PROCEDURE.md + RUNBOOK.md as context.
