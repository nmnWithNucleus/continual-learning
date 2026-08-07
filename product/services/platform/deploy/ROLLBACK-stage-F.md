# Stage F rollback runbook — restore the v0 world

Written at WP-F0c, before the cutover, so nothing in it is assumed under pressure.
Rollback in Stage F means **restoring services**, not reverting commits: the merge
stays in history; what changes is which world serves. Every step is named; run them
in order; paste each verification into the worklog as you go. Do not improvise past
a failed step — stop and report.

Facts this runbook relies on (recorded pre-cutover, 2026-08-07):

- Pre-merge `main` = `9307b7e`; the dp-v0-live worktree sits at exactly that SHA.
- Worktree: `/home/ubuntu/nmn/dp-v0-live` (branch `main`); its
  `storage/.venv` is a symlink → this tree's `product/services/storage/.venv`.
- learn.env backup made at F1 before the un-repoint: `learn.env.pre-stage-f.bak`
  (five `STORAGE_*` pins + worktree `SERVICES_ROOT`/`DP_VAR_DIR`; no
  `DP_SUPERVISOR`, no `VLM_URL`).
- v0 data homes: storage data lives in THIS tree (`storage/app/dev.db`,
  `app/raw_store`, `app/reservoir`) — the repoint moved code, never data; the v0
  DP journal lives in the worktree (`data-processing/var/dp.db`).

## 1. Stop the v1 world

```
cd /home/ubuntu/nmn/continual_learning/product/services/platform/deploy
bash run_learn.sh --stop          # storage, data-processing (+ its supervisor fleet), recording
bash run_vllm.sh --stop           # the captioner VLM (GPUs 0-1 release)
```

Verify: `ss -tlnp | grep -E ':(8083|8085|812[0-9]|813[0-9]|814[0-9]|815[0-9]|8161)'`
prints nothing; `nvidia-smi` shows GPUs 0-1 at 0 MiB. Recording (`:8084`) may stay
up — it buffers by design while DP is away; leave it capturing.

## 2. Restore the worktree (only if it was already retired)

```
cd /home/ubuntu/nmn/continual_learning
git worktree add /home/ubuntu/nmn/dp-v0-live 9307b7e
printf '.venv\n.venv-learn\nvar/\n' >> .git/worktrees/dp-v0-live/info/exclude
ln -s /home/ubuntu/nmn/continual_learning/product/services/storage/.venv \
      /home/ubuntu/nmn/dp-v0-live/product/services/storage/.venv
```

(The worktree adds detached at `9307b7e` — v0's exact code. The DP side needs no
venv inside the worktree: the fleet launches it under `deploy/.venv-learn`.)

Verify: `git worktree list` shows `/home/ubuntu/nmn/dp-v0-live` at `9307b7e`
(detached); `git -C /home/ubuntu/nmn/dp-v0-live rev-parse HEAD` prints
`9307b7e…`; `readlink -f /home/ubuntu/nmn/dp-v0-live/product/services/storage/.venv`
resolves into this tree's `storage/.venv`.

## 3. Restore learn.env

```
cd /home/ubuntu/nmn/continual_learning/product/services/platform/deploy
cp learn.env.pre-stage-f.bak learn.env
```

Verify: `grep -c 'dp-v0-live' learn.env` ≥ 2 (worktree `SERVICES_ROOT` +
`DP_VAR_DIR` back), `grep -c 'STORAGE_' learn.env` = 5, no `DP_SUPERVISOR`, no
`VLM_URL`.

## 4. Re-wipe (the v1 world's writes must not reach v0's renderer)

Anything the v1 stack wrote post-cutover is v2-shaped (C2 v1 records, v2
day-log stamps, D27 windows). v0 must never serve a mixed corpus, so run the
same wipe again — same KEEP/WIPE law, same manifest discipline — with the DP
journal flag pointed at the NEW tree's journal (the v0 journal was wiped at F1;
the new one holds the v1 world's receipts):

```
python3 cutover_wipe.py \
  --dp-db /home/ubuntu/nmn/continual_learning/product/services/data-processing/var/dp.db
# read the manifest, then:
python3 cutover_wipe.py --execute \
  --dp-db /home/ubuntu/nmn/continual_learning/product/services/data-processing/var/dp.db \
  --recording-claims <the disposition the founder ruled at GATE 1>
```

`/raw`, `/sessions`, profiles, the model directory and recording's capture
history survive this exactly as they survived F1 — the classification is the
same code.

## 5. Relaunch v0

```
bash run_learn.sh                 # SERVICES_ROOT now the worktree again
```

Verify: `curl -s localhost:8083/health`, `:8085/health`, `:8084/health` all 200;
`:8085`'s health shape is v0's (no `supervisor` field). Trigger
`bash run_learn.sh --smoke` and confirm record_ids come back and
`GET /context/records/<id>` reads one.

## 6. Continuum during rollback

Post-merge continuum (on `main`) is taught v2-only; v0 storage renders v1
day-logs, so nightly runs would refuse — correctly (that refusal is the net
working; the window stays open, nothing is lost but the night). Do NOT re-teach
v1 by hand. Nights pause until roll-forward; if a night must run during a long
rollback, run continuum from `9307b7e` (`git worktree add` a second checkout)
against the v0 stack — its gate expects exactly what v0 storage serves.

## 7. Report

State in the worklog: the trigger, the steps taken with their pasted
verifications, the dispositions applied, and what the roll-forward plan is.
GPUs 0-1 stay free until a new "CUTOVER APPROVED".
