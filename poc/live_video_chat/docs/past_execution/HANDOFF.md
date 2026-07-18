# Nucleus + Polyhive a3mega clusters — full handoff (2026-05-31)

> ## ✅ UPDATE 2026-05-31 (LATE): POLYHIVE REDEPLOY + RESTORE COMPLETE
> The teardown+redeploy this doc was *preparing for* has now been **executed and
> handed back to the customer.** Polyhive is live at **24 nodes**, `/home` migrated,
> `/mnt/disk` restored on nodes 0–15, all validation passed (aperture=8, NCCL/TCPXO
> 302–335 GB/s, reservation 32/32). **The authoritative record of the redeploy is
> `EXECUTION-LOG-20260531.md`** — read that first; it documents several places where
> the RUNBOOK's assumptions were wrong (resize-VMs can't be stopped, blueprint had 3
> latent bugs, the destroy plan tried to delete `/home`+`/gcs`). The sections below
> describe the *pre-redeploy* state and remain accurate as history. The 17 PD
> snapshots are STILL PRESENT — do not delete until customer signs off.

**Reader: an operator/agent picking up after the Nucleus validation +
Polyhive backup work, in preparation for the Polyhive teardown+redeploy.**

This doc supersedes the earlier 2026-05-30 version. It captures everything
done up to and including the **Polyhive disk snapshots** (17 of them, all
READY), so a new agent on a fresh machine can pick up cleanly.

---

## TL;DR — Cluster state right now

| Cluster | State |
|---|---|
| **Nucleus** (8 × a3-megagpu-8g) | ✅ Fully validated end-to-end, multi-node NCCL/TCPXO at 305 GB/s busbw (2 nodes) + 187 GB/s busbw (8 nodes). `/home` restored from old Filestore (1.25M files / 2.6 TB). `/gcs` populated from old bucket (5.75M objects / 8.98 TB). `/gcs` mounted on controller. Production-ready. |
| **Polyhive** (~~16~~ → **24** × a3-megagpu-8g) | ✅ **REDEPLOYED 2026-05-31** to 24 nodes on the updated blueprint. `/home` migrated (191 GB / 13 users) old Filestore→new. `/mnt/disk` restored on nodes 0–15 from the **surviving original per-node disks** (NOT the snapshots — see EXECUTION-LOG). aperture=8 on all 24, NCCL/TCPXO 302–335 GB/s, reservation 32/32. Handed back to customer. **17 PD snapshots still present** (keep until sign-off). *(Pre-redeploy state below is history.)* |
| Orphans (cleaned up 2026-05-31) | Old Nucleus Filestore deleted, old Nucleus GCS bucket deleted, partial polyhive bucket backup data deleted. See section 6. |

---

## 1. What's been done since the previous handoff

### 1a. Nucleus `/home/ubuntu` restored from the pre-destroy Filestore
- The old Nucleus Filestore `a3mega-base-nucleus-d1946fef` (10.174.0.2) was preserved (state-rm during destroy, deletion_protection=true). RO-mounted to n-controller at `/mnt/old-nucleus-home`, then `rsync -aHAX --numeric-ids` to `/home/ubuntu/` on the new Filestore.
- Restored: **1,253,534 files / 19,358 symlinks / 2.62 TB** at average 80 MB/s (~9h total).
- Verified by dry-run rsync — zero files would transfer (only `.bash_history` drifted due to active shell use on n-controller).
- The pre-existing /home/ubuntu content at the moment of restore (NCCL validation artifacts) was preserved into `/home/ubuntu/.post-destroy-validation-052926/` first, so the restore didn't destroy them. That subdir is ~17 GB (mostly the cached `nvidia+pytorch+24.07-py3.sqsh` container layer).
- Orphan Filestore was then deleted (section 6).

### 1b. Nucleus `/gcs` populated from the pre-destroy GCS bucket
- The old Nucleus cluster-storage bucket `gs://a3mega-base-nucleus-fab56217/` (8.98 TB / 5,748,455 objects per rsync's count) was preserved (state-rm) at destroy time.
- `gcloud storage rsync --recursive gs://a3mega-base-nucleus-fab56217/ gs://a3mega-base-nucleus-c0c5c6ff/` — completed in ~2h at average **739 MiB/s**.
- Verified by dry-run rsync — both sides finished at identical object counts (5,748,455 = 5,748,455).
- Orphan bucket was then deleted (section 6).
- Contents (still in new bucket): `.lock/, akito/, eval/, fine-t2i/, imagenet/, models/, train-bucket-10_2_of_5_res_1024_dynamic_534/`. `fine-t2i/` alone is 5.74M objects.

### 1c. `/gcs` mount on Nucleus controller (and same baked into blueprint)
- Discovered that slurm-gcp 6.10's `setup_network_storage.py` mounts NFS only on the controller and **silently skips gcsfuse**. config.yaml on the controller had the gcsfuse entry for /gcs, but no mount happened.
- One-shot fix on the running controller: `mkdir /gcs`, fstab entry, `mount /gcs`. Verified working.
- Durable fix: a runner added to `controller_startup` in BOTH blueprints, `bootstrap_controller_environment.sh`, that reads `/slurm/scripts/config.yaml` and replays any gcsfuse entry into fstab + mount. Idempotent.

### 1d. Both blueprints updated with three consolidated fixes (in addition to gpu-test removal + aperture service)

In `phase2/final/a3mega-slurm-blueprint-nucleus.yaml` and `phase2/final/a3mega-slurm-blueprint-polyhive.yaml`:

| # | Fix | Where |
|---|---|---|
| 1 | Firewall rule for external SSH (`${deployment_name}-allow-ssh-external`, 0.0.0.0/0 → tcp/22) | `sysnet` module's `firewall_rules` |
| 2 | Pre-create `/home/ubuntu` on the controller so guest-agent can write authorized_keys — **eliminates the OS-Login flip dance** that was previously required to bootstrap SSH | `controller_startup` → `bootstrap_controller_environment.sh` |
| 3 | gcsfuse safety-net mount on controller (see 1c) | same runner |

Verified both YAMLs are symmetric (same intended fixes; only `blueprint_name` and some inherited comment diffs).

### 1e. Polyhive disk backups via PD snapshots (the path that actually works at speed)

After two failed attempts at GCS-bucket-rsync from the 16 nodes (root cause #1: NFS chunk-file race in `~/.config/gcloud/.../rsync_files/`; fixed with `CLOUDSDK_CONFIG=/tmp/gcloud-config-disk-N`. Root cause #2: per-file HTTP overhead caps each rsync at ~15 MB/s = 25h total, even with the race fixed), we switched to **GCE Persistent Disk snapshots** — the right tool.

**17 snapshots fired in parallel via `--async`, all READY in ~40 min:**

| Snapshot | Source disk | Compressed size |
|---|---|---:|
| `phive-disk-0-pre-redeploy-20260530-2354` | phivea3m-a3meganodeset-0-1 | 474.8 GiB |
| `phive-disk-1-pre-redeploy-20260530-2354` | phivea3m-a3meganodeset-1-1 | 531.9 GiB |
| `phive-disk-2-pre-redeploy-20260530-2354` | phivea3m-a3meganodeset-2-1 | 532.5 GiB |
| `phive-disk-3-pre-redeploy-20260530-2354` | phivea3m-a3meganodeset-3-1 | 532.8 GiB |
| `phive-disk-4-pre-redeploy-20260530-2354` | phivea3m-a3meganodeset-4-1 | 532.0 GiB |
| `phive-disk-5-pre-redeploy-20260530-2354` | phivea3m-a3meganodeset-5-1 | 531.8 GiB |
| `phive-disk-6-pre-redeploy-20260530-2354` | phivea3m-a3meganodeset-6-1 | **946.7 GiB** (larger than norm) |
| `phive-disk-7-pre-redeploy-20260530-2354` | phivea3m-a3meganodeset-7-1 | 532.2 GiB |
| `phive-disk-8-pre-redeploy-20260530-2354` | phivea3m-a3meganodeset-8-1 | 532.4 GiB |
| `phive-disk-9-pre-redeploy-20260530-2354` | phivea3m-a3meganodeset-9-1 | 531.7 GiB |
| `phive-disk-10-pre-redeploy-20260530-2354` | phivea3m-a3meganodeset-10-1 | 532.3 GiB |
| `phive-disk-11-pre-redeploy-20260530-2354` | phivea3m-a3meganodeset-11-1 | 535.6 GiB |
| `phive-disk-12-pre-redeploy-20260530-2354` | phivea3m-a3meganodeset-12-1-wq92 | 11.2 MiB (post-SEV1 fresh disk, essentially empty) |
| `phive-disk-12-pre-sev1-orphan-20260530-2354` | phivea3m-a3meganodeset-12-1 (orphan) | **0 bytes** (was empty before too — no pre-SEV1 data to recover) |
| `phive-disk-13-pre-redeploy-20260530-2354` | phivea3m-a3meganodeset-13-1 | **815.8 GiB** (larger than norm) |
| `phive-disk-14-pre-redeploy-20260530-2354` | phivea3m-a3meganodeset-14-1 | 2.9 GiB (unusually small — worth checking with Polyhive) |
| `phive-disk-15-pre-redeploy-20260530-2354` | phivea3m-a3meganodeset-15-1 | 536.8 GiB |

**Total: 7.91 TB compressed (~3.6× compression — disks were ~1.7 TB each).**
**Cost: ~$210/month at $0.026/GB-month** until deletion after restore.

Sync was done with `sync` on each node beforehand (filesystem-quiescent state). Polyhive workloads were running during snapshots — snapshots are crash-consistent (like an ext4 unexpected-shutdown recovery), which is the standard expectation.

Restore procedure is in PROCEDURE.md section 2d (new section, post-redeploy).

---

## 2. SSH access map (critical for the next agent)

The next agent picking this up may be on a completely fresh machine. Here's what they need.

### From the user's Mac
- `~/.ssh/config` has aliases for the Polyhive controller and Nucleus controller. Specifically, `polyhive-controller` and `n-controller`. The user manages these keys themselves.

### From a fresh management VM (where this work was done)
- `~/.ssh/google_compute_engine` is the gcloud-generated SSH key. Its `.pub` is already in the **project-level `ssh-keys` metadata** as `ubuntu:<key> ubuntu@phivea3m-a3meganodeset-0`. Any VM with default OS-Login=FALSE in the project can be SSHed to using this key as `ubuntu`.
- The `~/.ssh/config` we built on the current management VM has these aliases (preserve or recreate them):

```ssh-config
Host n-controller
  HostName <nucleus controller public IP, currently 34.181.188.7 or similar; check with gcloud>
  User ubuntu
  IdentityFile ~/.ssh/google_compute_engine

Host phivea3m-controller p-controller
  HostName 34.150.187.149
  User ubuntu
  IdentityFile ~/.ssh/google_compute_engine
  StrictHostKeyChecking no
  UserKnownHostsFile /dev/null

Host p-node-*
  User ubuntu
  IdentityFile ~/.ssh/google_compute_engine
  StrictHostKeyChecking no
  UserKnownHostsFile /dev/null

Host p-node-0   ; HostName 34.21.112.64
Host p-node-1   ; HostName 35.245.167.29
Host p-node-2   ; HostName 34.48.238.227
Host p-node-3   ; HostName 34.86.134.147
Host p-node-4   ; HostName 34.48.165.115
Host p-node-5   ; HostName 136.107.71.40
Host p-node-6   ; HostName 35.236.252.167
Host p-node-7   ; HostName 34.21.18.13
Host p-node-8   ; HostName 35.221.39.219
Host p-node-9   ; HostName 35.245.191.44
Host p-node-10  ; HostName 34.186.46.61
Host p-node-11  ; HostName 34.85.209.215
Host p-node-13  ; HostName 34.11.67.90
Host p-node-14  ; HostName 34.86.226.131
Host p-node-15  ; HostName 34.85.223.45
Host p-node-12  ; HostName 172.16.0.35 ; ProxyJump p-controller
```

(Note: the polyhive nodes' public IPs may change if/when nodes are rebooted/replaced. Re-query with `gcloud compute instances list --filter="name~phivea3m-a3meganodeset"`.)

### If the management VM is gone
The same gcloud-generated keys can be regenerated. Any VM in the project with `roles/editor` (or equivalent) can do this:

```bash
[ -f ~/.ssh/google_compute_engine ] || ssh-keygen -t rsa -N "" -f ~/.ssh/google_compute_engine -q
# First-time gcloud compute ssh attempt will push the key to project metadata
gcloud compute ssh phivea3m-controller --zone=us-east4-b --command=true
```

The new key (with a different `.pub` comment) will be added alongside the existing `ubuntu@phivea3m-a3meganodeset-0` entry in project metadata.

---

## 3. Cluster validation — actual results (from previous handoff, still current)

| Check | Result |
|---|---|
| Nucleus: 8 VMs RUNNING, 4 templates fresh, slurm 25.05.2, 8/8 IDLE | ✅ |
| Single-node srun: 8× H100 80GB HBM3, driver 570.211.01 | ✅ |
| Mounts: `/home`, `/gcs`, `/mnt/localssd`, `/dev/aperture_devices` populated on all 8 | ✅ |
| **2-node NCCL all_reduce, GPUDirect-TCPXO via FasTrak** | ✅ 305 GB/s busbw @ 1 GB |
| **8-node NCCL all_reduce, 64 ranks, GPUDirect-TCPXO** | ✅ 187 GB/s busbw @ 8 GB |
| Polyhive: 16 nodes still up on original Dec-2025 deploy, customer using JuiceFS/R2/spectral mounts | ✅ in production |

NCCL test recipe artifacts under `/home/ubuntu/.post-destroy-validation-052926/` on n-controller after the /home restore:
- `nccl_test.py` — torch.distributed all_reduce
- `nccl_run.sh` — sources `/var/lib/tcpxo/lib64/nccl-env-profile.sh`, sets `MASTER_ADDR`/`PORT`/`RANK`/`WORLD_SIZE` from `SLURM_*`
- `fix_aperture.sh` — manual aperture mount (now redundant; the systemd service does this on boot)
- `nccl-{41..63}.{out,err}` and `nccl_trace.*.log` from successful runs

---

## 4. Two real cluster issues already root-caused (durable fixes in blueprint)

### 4a. `gpu-test.epilog_slurmd` false-positive drains
`/slurm/scripts/tools/gpu-test` does `grep -i fail` over `dcgmi diag` output, which matches DCGM 4.5.3's benign "**Failed** to get hostengine environment variable" warning → drains every healthy node. **Fix:** the `ln -s …/gpu-test` line was removed from `controller_startup.stage_scripts.sh` in both blueprints. Polyhive's Dec-2025 deploy also runs without it.

### 4b. `/dev/aperture_devices/` empty on 3/8 fresh nodes (udev/systemd-mount race)
Google's udev rule does `RUN+="systemd-mount …"`. systemd-udevd kills any RUN+= over 59s; if systemd-manager is slow at boot, the action expires and the dir stays empty → GPUDirect-TCPXO can't initialize. **Fix:** a systemd oneshot service `aperture-devices-mount.service` baked into the image (in `image_build_script` runners) re-runs the mounts `After=systemd-udev-settle.service multi-user.target`. Idempotent, only mounts what's missing.

---

## 5. State of the IaC at handoff

All under `~/nucleus-admin/debug-052626/phase2/final/`:

| File | Status |
|---|---|
| `a3mega-slurm-blueprint-polyhive.yaml` | ✅ updated — gpu-test removed, aperture service, firewall rule, /home/ubuntu pre-create, /gcs safety-net |
| `a3mega-slurm-blueprint-nucleus.yaml` | ✅ updated — same as above |
| `a3mega-slurm-deployment-polyhive.yaml` | ✅ (24 nodes — but Polyhive only has 16 a3-megagpu slots in the reservation; keep at 24 only if reservation is expanded; otherwise edit deployment_yaml to 16) |
| `a3mega-slurm-deployment-nucleus.yaml` | ✅ (8 nodes) |
| `PROCEDURE.md` | ✅ updated 2026-05-31 — pre-redeploy firewall cleanup + new section on snapshot restore |
| `POST_DEPLOY_SSH_BOOTSTRAP.md` | ✅ kept for reference, **but most steps are now in the blueprint** so this is mostly obsolete |
| `RUNBOOK.md` | ✅ **NEW** — strict step-by-step claude-executable runbook for Polyhive teardown+redeploy+restore. Read this if you're the agent executing the redeploy. |
| `PROMPT.md` | ✅ **NEW** — copy-paste prompt the user gives to start a fresh Claude session that will execute RUNBOOK.md autonomously. |

---

## 6. Cleanups performed 2026-05-31

| Resource | What happened |
|---|---|
| `gs://a3mega-base-polyhive-c94cb801/backup/` (partial bucket-rsync data, ~6 GB) | Deleted |
| Nucleus orphan Filestore `a3mega-base-nucleus-d1946fef` (10.174.0.2, 10 TB tier, 2.4 TB used) | Deletion_protection disabled, then deleted. Was previously unmounted from `/mnt/old-nucleus-home` on n-controller. |
| Nucleus orphan GCS bucket `gs://a3mega-base-nucleus-fab56217/` (8.98 TB, 5.75M objects) | Deleted (after content verified against new bucket via dry-run rsync) |

If any of these orphans still appear in console at the time of reading: re-check, the deletions may have hit transient errors. See PROCEDURE.md section 6 for command reference.

---

## 7. Next steps for the new agent (in execution order)

### 7a. Polyhive teardown
Follow PROCEDURE.md section 1 with the updates noted there:
- The pre-redeploy firewall-rule cleanup name pattern has changed (now `${deployment_name}-allow-ssh-external` from the blueprint; old manual `allow-ssh-external` from earlier may also exist).
- 16 nodes, not 24 — verify reservation count before proceeding.
- `phive-data-disk` (the 2 TB pd-balanced disks) — the destroy will delete the current ones. Snapshots are in place; don't worry about them.
- Filestore deletion_protection should hold; preserve `/home`.

### 7b. Polyhive redeploy
Use `phase2/final/a3mega-slurm-blueprint-polyhive.yaml` (NOT the original December version). The updated blueprint has all the fixes. PROCEDURE.md section 2.

### 7c. Post-deploy disk restore (NEW)
After the new Polyhive cluster is up with empty 2 TB disks, restore each disk from its snapshot. PROCEDURE.md section 2d (new) has the per-node commands.

### 7d. Re-attach `phivea3m-controller-save` and `polyhive-env-disk`
PROCEDURE.md section 2b. These are Polyhive's slurm state + customer env disk; preserved across the destroy.

### 7e. Hand back to Polyhive customer
For JuiceFS, R2 mounts, training workflows. PROCEDURE.md section 3.

### 7f. Optional Nucleus follow-up
- Re-attach `nucla3m-controller-save` (50 GB pd-ssd, currently detached). Slurm accounting/jobs would otherwise be lost on a controller rebuild.
- Decide what to do with `/home/ubuntu/.post-destroy-validation-052926/` (17 GB of NCCL trace logs + the cached pytorch container layer) — keep or rm. Keep is fine.

---

## 8. Key commands the new agent will use

```bash
# Project + zone
gcloud config set project poetic-avenue-438401-a7

# SSH access (assuming ~/.ssh/google_compute_engine in place + project metadata key)
ssh n-controller        # or use gcloud compute ssh nucla3m-controller --zone=us-east4-b
ssh p-controller        # or gcloud compute ssh phivea3m-controller --zone=us-east4-b
ssh p-node-0            # any polyhive compute node

# Look at PD snapshots from the 2026-05-30 backup
gcloud compute snapshots list --filter="name~20260530-2354" \
  --format="table(name,sourceDisk.basename(),diskSizeGb,storageBytes.size(),status)"

# Reservation health
gcloud compute reservations describe nvidia-h100-dkydfas6m486t \
  --zone=us-east4-b --format='value(specificReservation.count,specificReservation.inUseCount)'

# Where the IaC lives (on n-controller's /home/ubuntu/)
ssh n-controller 'ls -la /home/ubuntu/nucleus-admin/debug-052626/phase2/final/'

# Documents (this file + procedure live on n-controller too)
ssh n-controller 'cat /home/ubuntu/nucleus-admin/debug-052926/HANDOFF.md | head -40'
ssh n-controller 'cat /home/ubuntu/nucleus-admin/debug-052626/phase2/final/PROCEDURE.md | head -40'

# Or via gsutil (durable backup of the docs)
gcloud storage cat gs://nucleus-a3mega-cluster/handoff/HANDOFF.md | head -40
gcloud storage cat gs://nucleus-a3mega-cluster/handoff/PROCEDURE.md | head -40
```

---

## 9. Key constants

- **Project:** `poetic-avenue-438401-a7`
- **Zone:** `us-east4-b`
- **Reservation:** `nvidia-h100-dkydfas6m486t` (32 slots; 16 Polyhive + 8 Nucleus = 24 in use; will be 24 again after Polyhive redeploys to 16 nodes — or 32 if Polyhive expands to 24)
- **Terraform backend buckets:** `gs://polyhive-a3mega-cluster`, `gs://nucleus-a3mega-cluster`
- **Cluster-storage buckets (current):**
  - Nucleus: `gs://a3mega-base-nucleus-c0c5c6ff/` (8.98 TB)
  - Polyhive: `gs://a3mega-base-polyhive-c94cb801/` (still essentially empty)
- **Filestores (current):**
  - Nucleus: `a3mega-base-nucleus-d80713e1` (10.185.0.2:/nfsshare, 10 TB tier)
  - Polyhive: `a3mega-base-polyhive-798798e0` (10 TB tier — unchanged from Dec-2025)
- **Polyhive PD snapshots (this work):** name pattern `phive-disk-N-pre-redeploy-20260530-2354` (N=0..15) and `phive-disk-12-pre-sev1-orphan-20260530-2354`

### Post-redeploy constants (updated 2026-05-31 — supersede the pre-redeploy values above for Polyhive)
- **Polyhive cluster size:** 24 nodes (`phivea3m-a3meganodeset-0..23`) + controller + login-001.
- **Polyhive `/home` Filestore (NEW):** `a3mega-base-polyhive-1c9c63f7` @ `10.134.0.2:/nfsshare`.
  - Old one `a3mega-base-polyhive-798798e0` (10.208.0.2) **preserved** as migration source; delete after sign-off.
- **Polyhive `/gcs` bucket (NEW):** `gs://a3mega-base-polyhive-63fe0f85/` (old `-c94cb801` was empty; preserved).
- **Polyhive packer image:** `slurm-a3mega-polyhive-20260531t090330z` (family `slurm-a3mega-polyhive`).
- **Per-node data disks:** `phivea3m-a3meganodeset-N-1` (2 TB, `device-name=phive-data-disk`, `/mnt/disk`).
  Nodes 0–15 = restored originals (node-12 fresh-empty); nodes 16–23 = fresh empty.
- **SSH:** `~/.ssh/phive` (User ubuntu). `~/.ssh/config` managed block: `p-ctl`, `p-login`, `p-node-0..23`.
  Aliases `p-controller` / `n-controller` also work. Node external IPs change on reboot — re-query with
  `gcloud compute instances list --filter="name~phivea3m-a3meganodeset"`.
- **Controller-save disk:** `phivea3m-controller-save` was deleted in the redeploy teardown (empty/orphaned);
  rollback snapshot `pre-redeploy-phivea3m-ctlsave-20260531-0329` retained. `polyhive-env-disk` re-attached.

---

## 10. Polyhive note (still applies)

**Don't deploy the original December blueprint to Polyhive — use the updated `a3mega-slurm-blueprint-polyhive.yaml` in `phase2/final/`.** The updated version has all five fixes (no gpu-test symlink, aperture systemd service, firewall rule, /home/ubuntu pre-create, /gcs safety-net). Without them, Polyhive will ship with broken epilog (chronic drains), 3/16 nodes with empty aperture devices, no external SSH, broken SSH-as-ubuntu bootstrap, and no /gcs on controller. The customer will hit at least one of these within minutes.
