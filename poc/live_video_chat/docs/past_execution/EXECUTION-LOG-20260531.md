# Polyhive a3mega — teardown + redeploy + /mnt/disk restore: EXECUTION LOG

**Date executed:** 2026-05-31
**Operator:** nmnadmin@withnucleus.ai (interactive, command-by-command from Mac terminal)
**Outcome:** ✅ COMPLETE — 24-node Polyhive cluster redeployed, `/home` migrated, `/mnt/disk`
restored on nodes 0–15, all validation passed, handed back to Polyhive customer.

This document is the factual record of what was *actually done*, including every place reality
diverged from RUNBOOK.md / PROCEDURE.md. **Read this before trusting the original runbook for a
future redeploy** — several of its core assumptions were wrong (see "Runbook corrections" at the
bottom). The original runbook still describes the *intent*; this log describes what works.

---

## 0. Environment / how this was run

- All `gcloud` / `gcluster` / `terraform` / `ssh` were run from the user's **Mac** (the authenticated
  machine), not from a management VM. The sandbox the agent runs in had no GCP access — so this was
  driven as a paired session: agent proposes exact commands, user pastes output back.
- Working dir: `/Users/shibu/workplace/NucleusAI/cluster-toolkit` (the real git checkout, where the
  `./gcluster` v1.67.0 binary lives; Terraform 1.13.3).
- The four YAMLs were staged into the cluster-toolkit dir from `agent_task/Polyhive Cluster Create/`.
- **SSH:** existing aliases were `p-controller` (not `polyhive-controller` as the runbook assumed) and
  `n-controller`. Key = `~/.ssh/phive` (4096-bit RSA, `User ubuntu`). Mid-operation we generated a
  managed block of aliases `p-ctl`, `p-login`, `p-node-0`..`p-node-23` (see §SSH below).

---

## 1. Constants (as of completion)

```
PROJECT            = poetic-avenue-438401-a7
ZONE               = us-east4-b
REGION             = us-east4
RESERVATION        = nvidia-h100-dkydfas6m486t   (32 slots; 24 Polyhive + 8 Nucleus = 32/32 full)
POLYHIVE_DEPLOY    = a3mega-base-polyhive
NEW Filestore /home= a3mega-base-polyhive-1c9c63f7   (10.134.0.2:/nfsshare)   <-- NEW, created by redeploy
OLD Filestore /home= a3mega-base-polyhive-798798e0   (10.208.0.2:/nfsshare)   <-- preserved source, migrated FROM
NEW /gcs bucket    = a3mega-base-polyhive-63fe0f85    <-- NEW, created by redeploy (empty, fine)
OLD /gcs bucket    = a3mega-base-polyhive-c94cb801    <-- preserved (was empty; nothing to migrate)
Packer image       = slurm-a3mega-polyhive-20260531t090330z  (family slurm-a3mega-polyhive)
SSH key            = ~/.ssh/phive   (ubuntu)
```

---

## 2. Phase-by-phase: what was done

### Phase 0 — Pre-teardown verifications ✅
- gcloud authed (`nmnadmin@withnucleus.ai`), project correct.
- **17 PD snapshots** all `READY` (16 `phive-disk-N-pre-redeploy-20260530-2354` + 1
  `phive-disk-12-pre-sev1-orphan-20260530-2354`).
- Nucleus 10 instances RUNNING. Reservation `32  24`.
- Blueprint fix-greps passed; deployment `a3mega_cluster_size: 24` confirmed.
- **0.7 SSH key push — caused a mistake (see §Incidents #1).** Net result after fixing: project
  `ssh-keys` metadata holds 7 keys including `ubuntu:...phive`. Two rollback snapshots taken of the
  preserved disks: `pre-redeploy-phivea3m-ctlsave-20260531-0329`, `pre-redeploy-poly-env-20260531-0329`.

### Phase 1 — Teardown ✅
- **Drain skipped:** the old Polyhive `slurmctld` was already **dead** (crash-looping on a missing
  `/var/spool/slurm/jwt_hs256.key` — orphaned in the May-29 incident). Nothing to drain.
- **Preserved disks were already detached** (since 2026-05-29): `phivea3m-controller-save` and
  `polyhive-env-disk` both showed empty USERS. No action needed.
- **Fresh Filestore backup** created: `pre-redeploy-polyhive-20260531-0342` (READY). (An older
  `pre-redeploy-polyhive` from 05-29 also present.)
- Firewall cleanup: deleted `allow-ssh-external`.
- **gcluster destroy** required materializing the working dir first (`gcluster create`, not the
  original cluster's dir — that was gone). This is where the **blueprint bugs surfaced** (§Incidents
  #2, #3) and **ADC auth was missing** (§Incidents #4) — all fixed before any destroy ran.
- **Destroy-plan guardrail review caught two would-be data-loss deletes** (§Incidents #5): the plan
  was going to destroy the **`/home` Filestore** and the **`/gcs` data bucket**. Both were
  `terraform state rm`'d out of the cluster group before applying.
- Destroy applied: cluster 150 + build_script 25 + primary 11 = **186 resources**. Hit the predicted
  **PSC address** block on the network (§Incidents #6) — deleted the Polyhive PSC address
  `global-psconnect-ip-ab012556` (10.208.0.0), re-ran, network destroyed. (Left Nucleus's
  `global-psconnect-ip-181d2976` / 10.185.0.0 untouched.)
- Post-destroy sweep: deleted 4 leftover `phivea3m` instance-templates + 2 packer images.
- **Verified clean:** reservation `32  8`, 0 phivea3m instances/templates/images/networks; filestore
  `-798798e0`, bucket `-c94cb801`, `polyhive-env-disk` all preserved.
- **Anomaly:** `phivea3m-controller-save` was *deleted* during the destroy (it was the 1.1 MiB
  empty/orphaned disk; the real slurm state had been orphaned in the May-29 incident). Zero impact —
  new controller builds fresh state. Snapshot `pre-redeploy-phivea3m-ctlsave-20260531-0329` retained.

### Phase 2 — Redeploy (24 nodes) ✅
- `gcluster deploy --auto-approve` ran clean after the blueprint fixes: primary (+12) → build_script
  (+29) → slurm-build (packer image `slurm-a3mega-polyhive-20260531t090330z`) → cluster (+160).
- **26 instances RUNNING** (controller, login, 24 nodes). All 24 a3mega nodes registered `idle`.
- **First-try SSH needed the OS-Login flip** (§Incidents #7): controller came up with
  `enable-oslogin=TRUE`. Set `enable-oslogin=FALSE` on controller+login and pushed `ubuntu:phive` to
  each instance's metadata → clean `ssh ubuntu@...` thereafter.

### Phase 2.5 — /home migration (NEW; not in original runbook) ✅
- New cluster's `/home` is a **fresh empty Filestore** (`-1c9c63f7` @ 10.134.0.2). The blueprint does
  NOT reuse the old Filestore — so `/home` came up empty.
- Migrated from the preserved old Filestore (`-798798e0` @ 10.208.0.2), reachable from the new
  controller (NFS port 2049 open — the old service-networking peering survived the teardown).
- Method: `sudo mount -o ro,vers=3 10.208.0.2:/nfsshare /mnt/old-home` on the new controller, then
  `rsync -aHAX --numeric-ids /mnt/old-home/ /home/`.
- **228 GB / 1,191,876 files** copied in ~2h50m. Verified by dry-run (1 file diff = `.bash_history`).
- **Excluded** `['ubuntu` (a junk home dir created by the guest-agent from corrupted metadata during
  Incident #1) and `ubuntu/.ssh/authorized_keys` (to protect the live SSH key).
- 13 legit user dirs migrated: `aj, akito, alejandro, ayon, evan, hyunmin, nmnadmin,
  nmnadmin_withnucleus_ai, nomi, pranav, rescueadmin, shibu, ubuntu`.

### Phase 3 — Re-attach preserved disks ✅
- `polyhive-env-disk` (200 GB, ~97 GiB data) **hot-attached** to the running controller
  (`--device-name=polyhive-env-disk`, mode=rw) — no stop needed, slurmctld stayed active.
- `phivea3m-controller-save` **skipped** (deleted in teardown; was empty; new controller already had
  fresh `/var/spool/slurm` + JWT, slurmctld active). Mounting is left to Polyhive (PROCEDURE §3).

### Phase 4 — /mnt/disk restore, nodes 0–15 ✅ (but NOT by the runbook's method)
**The runbook's stop→detach→swap→start is IMPOSSIBLE here.** These nodes were created via
**resize-requests** (DWS/reservation bulk provisioning); GCE returns
`HTTPError 400: Operation is not supported for VMs created with resize requests` on `instances stop`.
No flag overrides it. (Local-SSD discard was a red herring — the real blocker is resize-request.)

We tried, in order, and discarded:
1. **snapshot→new-disk→stop→swap** — can't stop. ✗
2. **rsync from snapshot disk into live /mnt/disk** — works but **~3 GB/min** on these
   millions-of-tiny-files datasets (`pink_zebra_*`, `beige_squirrel_*`) → ~9h/node. Too slow. ✗
3. **Hot-attach restored disk + block copy** — would still copy 2 TB needlessly. ✗

**What actually worked — attach the surviving ORIGINAL disks directly:**
- The teardown did **not** destroy the 16 original per-node data disks
  (`phivea3m-a3meganodeset-N-1`, auto_delete=false, detached when their VMs died). **These ARE the
  real pre-teardown `/mnt/disk` filesystems.** No snapshot restore needed.
- Per node (0–15): `ssh umount /mnt/disk` → detach the empty deploy disk
  (`phivea3m-a3meganodeset-N-1-xxxx`) → attach the original `phivea3m-a3meganodeset-N-1` as
  `--device-name=phive-data-disk --mode=rw` → mount.
- Mount gotchas learned: device-name symlink races after detach/attach (wait for
  `/dev/disk/by-id/google-phive-data-disk` to settle, ~3 s); crash-consistent ext4 needs
  `-o noload` if the journal is dirty; mount by **UUID** when device-name is ambiguous.
- **Results:** nodes 0–11,13,15 = real data (704 GB–1.7 TB). **node-12 = blank** (its original had
  no filesystem at all — consistent with HANDOFF "post-SEV1, essentially empty, no data to recover";
  all 3 of its disk variants confirmed empty → `mkfs.ext4 -F` fresh). **node-14 = 12 GB** (small but
  real, expected). All `rw`, all reboot-safe (fstab `google-phive-data-disk` line present).
- **Naming consistency fix:** node-0 was briefly on a snapshot-derived disk (`phive-restore-src-0`);
  swapped it onto its original `phivea3m-a3meganodeset-0-1` so all 16 follow the uniform
  `phivea3m-a3meganodeset-N-1` convention (clean future teardown).
- **Quota incident (§Incidents #8):** the abandoned snapshot-restore attempts created ~14
  `phive-restore-src-*` disks (2 TB each, pd-balanced = SSD quota). Hit `SSD_TOTAL_GB` ceiling
  (126,123 GB). Deleted all redundant disks (restore-src + the 16 empty `-xxxx` deploy disks);
  reclaimed ~62 TB (usage 124,308 → 62,308 GB).
- Nodes 16–23: left with fresh empty 2 TB `/mnt/disk` (customer populates).
- `scontrol update nodename=phivea3m-a3meganodeset-[0-15] state=RESUME` → all 24 idle/alloc.

### Phase 5 — Validation ✅
- **5.1 aperture:** all 24 nodes report `aperture=8` (the blueprint's `aperture-devices-mount.service`
  safety-net works on every node).
- **5.2 slurm:** 24/24 idle, REASON empty, zero drains.
- **5.3 NCCL 2-node GPUDirect-TCPXO** (nodes 8,19, 16 ranks, `nvidia/pytorch:24.07-py3`):
  ```
  size  256MB  busbw 239.64 GB/s
  size 1024MB  busbw 302.25 GB/s     (Nucleus reference: 305 GB/s)
  size 2048MB  busbw 315.42 GB/s
  size 4096MB  busbw 327.80 GB/s
  size 8192MB  busbw 335.10 GB/s
  OK
  ```
  Confirmed `NET/FasTrak v1.0.15` (GPUDirect-TCPXO, not TCP fallback).
  - **NCCL fix (§Incidents #9):** the migrated `/home/ubuntu/.local/lib/python3.10/site-packages/torch`
    shadowed the container's torch → `AttributeError: _dlpack_exchange_api`. Fixed by running
    `python -s` in `nccl_run.sh` (ignore user-site packages). NCCL scripts were copied from
    `n-controller:/home/ubuntu/.post-destroy-validation-052926/`.
- **5.5 reservation:** `32  32` (full).

### Phase 6 — Hand-back ✅
- On-disk HANDOFF (`n-controller:/home/ubuntu/nucleus-admin/debug-052926/HANDOFF.md`) appended with
  completion record.
- **Handed back to Polyhive customer** (JuiceFS restage + workflow validation = their scope).
- **STOPPED.** Did NOT delete the 17 PD snapshots (await customer sign-off).

---

## 3. Incidents & deviations (the important part)

| # | What | Resolution |
|---|---|---|
| 1 | **Project ssh-keys metadata corrupted.** The 0.7 key-push used `--format=...extract(value)` which returned a Python-list repr (`['k1\nk2...']` with literal `\n`); the append mangled all 6 existing keys into one unparseable line, briefly disabling SSH-by-metadata for other users/VMs. | Reconstructed all 7 keys from the stored value (split on literal `\n`), re-pushed via `--metadata-from-file`. Verified 7 well-formed keys. Side effect: the old cluster's guest-agent created a junk `['ubuntu` home dir (excluded from /home migration). |
| 2 | **Blueprint bug — unescaped bash `$()`.** `gcluster create` failed: `Bitwise operators are not supported` at `for d in $(lspci ...)` in the aperture-mount script. ghpc parses `$()` as its own syntax. | Escaped `$(` → `\$(` on lines 329–330 of the aperture script (polyhive + nucleus blueprints). |
| 3 | **Blueprint bug — malformed firewall rule.** `allow-ssh-external` used bare `ports: ["22"]` with no `allow:` block → `"allow": one of allow,deny must be specified`. | Rewrote to nested `allow: [ {protocol: tcp, ports: ["22"]} ]`. |
| 4 | **ADC missing.** Terraform/ghpc validators need Application Default Credentials (separate from gcloud CLI auth). On a VM these come free from the metadata server; on the Mac they didn't exist. | `gcloud auth application-default login` + `set-quota-project`. |
| 5 | **Destroy plan would have deleted `/home` Filestore AND `/gcs` bucket.** The cluster terraform group owned both `module.homefs.google_filestore_instance` and `module.data-bucket.google_storage_bucket`. `deletion_protection` did NOT appear in the plan. | `terraform state rm` of: `module.homefs.google_filestore_instance.filestore_instance`, `module.homefs.random_id.resource_name_suffix`, `module.data-bucket.google_storage_bucket.bucket`, `module.data-bucket.google_storage_bucket_iam_binding.viewers`, `module.data-bucket.random_id.resource_name_suffix`. Then re-planned and confirmed both absent before applying. |
| 6 | **PSC address pinned the sysnet** (predicted by runbook 1.6). Network destroy failed: `network ... already being used by ... global-psconnect-ip-ab012556`. | Deleted **only** the Polyhive PSC address `global-psconnect-ip-ab012556` (10.208.0.0). Distinguished from Nucleus's `global-psconnect-ip-181d2976` (10.185.0.0) by IP range — left Nucleus's alone. Re-ran destroy. |
| 7 | **First-try SSH failed** post-deploy (`Permission denied (publickey)`). Controller had instance-level `enable-oslogin=TRUE`. | `add-metadata enable-oslogin=FALSE` on controller+login + push `ubuntu:phive` to each instance's `ssh-keys`. (POST_DEPLOY_SSH_BOOTSTRAP §2–3.) |
| 8 | **SSD quota exceeded.** Abandoned snapshot-restore attempts left ~14 `phive-restore-src-*` 2 TB disks → hit `SSD_TOTAL_GB` 126,123 limit. | Deleted all redundant disks (restore-src + 16 empty deploy disks). Reclaimed ~62 TB. |
| 9 | **NCCL torch import crash.** Migrated `~/.local` torch shadowed the container torch. | `python -s` in `nccl_run.sh`. |
| — | **node-12 has no data.** All 3 of its disk variants (`-12-1`, `-12-1-nl8n`, `-12-1-wq92`) confirmed empty/no-fs. | Formatted `-12-1` fresh (`mkfs.ext4 -F`). Matches HANDOFF SEV-1 notes. Correct/expected. |

---

## 4. Outstanding items (for the user, not done autonomously)

1. **17 PD snapshots still present** (`*-pre-redeploy-20260530-2354` + `*-pre-sev1-orphan-...`).
   ~$210/mo. **Delete ONLY after Polyhive signs off on data integrity.** Command (PROCEDURE §5b):
   ```
   gcloud compute snapshots list --filter="name~20260530-2354" --format='value(name)' \
     | xargs -r -n1 gcloud compute snapshots delete --quiet
   ```
2. **Old `/home` Filestore `a3mega-base-polyhive-798798e0`** (10 TB, 191 GB used) — preserved migration
   source. Delete after confirming the new `/home` is good (it is; verified). `deletion_protection`
   is on; disable then delete when ready.
3. **2 rollback snapshots** `pre-redeploy-{phivea3m-ctlsave,poly-env}-20260531-0329` — keep until
   cluster confirmed stable, then delete.
4. **Stale Nucleus packer VM** `packer-6b05f3` (RUNNING since 2026-05-29, ~$25–40/day). Labeled
   `ghpc_deployment=a3mega-base-nucleus` → NOT touched (Nucleus guardrail). Reap if truly orphaned.
5. **`~/.ssh/config`** has a managed block `# >>> phive-nodes BEGIN ... END` with `p-ctl/p-login/
   p-node-0..23`. Delete that block when no longer needed (node IPs change on reboot/replace).
6. **Filestore backups** `pre-redeploy-polyhive*` — extra safety net; delete when comfortable.

---

## 5. Files in this directory

| File | Status |
|---|---|
| `EXECUTION-LOG-20260531.md` | **THIS FILE** — the factual record. Read first for a future redeploy. |
| `HANDOFF.md` | Updated 2026-05-31 with the completion banner. |
| `RUNBOOK.md` | Original step-by-step + **ERRATA section appended** (corrections from this run). |
| `PROCEDURE.md` | Long-form reference + **ERRATA appended**. |
| `POST_DEPLOY_SSH_BOOTSTRAP.md` | Still accurate (the OS-Login flip in §7 was needed). |
| `PROMPT.md` | The original kickoff prompt. |
| 4× `a3mega-slurm-*.yaml` | Blueprints now contain the 2 syntax fixes (aperture `\$()`, firewall `allow:`). |
