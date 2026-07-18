# RUNBOOK.md — Polyhive teardown + redeploy + /mnt/disk restore

> ## ⚠️ ERRATA — this runbook was EXECUTED 2026-05-31; several steps were WRONG
> This redeploy actually ran on 2026-05-31. **`EXECUTION-LOG-20260531.md` is the
> authoritative record** — read it before re-using this runbook. The steps below
> describe the original *intent*; the corrections that made it work are:
>
> 1. **Phase 4 disk restore as written is IMPOSSIBLE.** These nodes are created via
>    **resize-requests** and **cannot be stopped** (`HTTPError 400: Operation is not
>    supported for VMs created with resize requests`). The stop→detach→swap→start flow
>    fails at step 1. **What worked:** the teardown does NOT destroy the per-node
>    original disks (`phivea3m-a3meganodeset-N-1`, auto_delete=false) — they ARE the
>    real `/mnt/disk` data. Just **hot-attach the original disk** as
>    `device-name=phive-data-disk` to the running node and mount. No stop, no snapshot
>    restore needed. (The 17 snapshots became a backup-of-last-resort, not the path.)
> 2. **The destroy plan tried to delete the `/home` Filestore AND the `/gcs` bucket**
>    (`deletion_protection` did NOT save them in the plan). You MUST `terraform state rm`
>    BOTH `module.homefs.*` and `module.data-bucket.*` before `--auto-approve`. See 1.6.
> 3. **The blueprint had 3 latent never-deployed bugs** that block `gcluster create`
>    /`deploy`: unescaped bash `$()` in the aperture script, a malformed firewall
>    `allow:` block, and missing ADC auth. The YAMLs in this dir are now FIXED; if you
>    regenerate from elsewhere, re-apply them (EXECUTION-LOG §3 #2–4).
> 4. **`/home` comes up EMPTY** on redeploy (fresh Filestore). The runbook had no
>    `/home` restore step — one was added (Phase 2.5: RO-mount old Filestore, rsync).
> 5. **First-try SSH needs the OS-Login flip** (POST_DEPLOY_SSH_BOOTSTRAP §2–3) — the
>    blueprint's `/home/ubuntu` pre-create alone was NOT sufficient.
> 6. **SSH alias is `p-controller`, not `polyhive-controller`.** Watch SSD quota when
>    creating restore disks (we hit the 126 TB ceiling).

**Read PROMPT.md first** — it explains the guardrails for the agent executing this.
**Read HANDOFF.md** for full context.
**PROCEDURE.md** is the long-form explained version; this RUNBOOK is the strict
step-by-step. When in doubt about *why*, consult PROCEDURE.md or HANDOFF.md.

The work: tear down the current **Polyhive a3mega cluster** (16 nodes still on the
Dec-2025 deploy), redeploy it using the updated blueprint, restore each `/mnt/disk`
from its 2026-05-30 PD snapshot, validate, hand back.

**Nucleus cluster MUST NOT be touched** during this work — it's in production.

---

## Safety guardrails (the agent must never do these without explicit user input)

- ❌ NEVER delete or modify the shared reservation `nvidia-h100-dkydfas6m486t`.
- ❌ NEVER disable Filestore deletion_protection on `a3mega-base-polyhive-798798e0` (10 TB `/home` for Polyhive).
- ❌ NEVER delete the 17 PD snapshots `phive-disk-*-pre-redeploy-20260530-2354` — they're the only backup until the customer has verified restore.
- ❌ NEVER touch Nucleus VMs / Filestore / GCS bucket / controller-save disk / etc.
- ❌ NEVER touch the Polyhive bucket `gs://a3mega-base-polyhive-c94cb801/` (kept across redeploy as cluster-storage).
- ❌ NEVER delete the controller-save / env disks: `phivea3m-controller-save`, `polyhive-env-disk`. They must be detached pre-destroy and re-attached post-deploy.

## On unexpected output

If any verification step in this runbook produces output other than what's described
under "expected", **STOP**, summarize what was found, and wait for user input.
Do not try to recover or work around.

## Constants

```
PROJECT=poetic-avenue-438401-a7
ZONE=us-east4-b
REGION=us-east4
RESERVATION=nvidia-h100-dkydfas6m486t
POLYHIVE_DEPLOY=a3mega-base-polyhive
POLYHIVE_FILESTORE=a3mega-base-polyhive-798798e0
POLYHIVE_BUCKET=a3mega-base-polyhive-c94cb801
SNAPSHOT_SUFFIX=20260530-2354
```

---

## Phase 0 — Pre-teardown verifications (zero side effects)

### 0.1 Verify gcloud + SSH prerequisites on the running machine
```bash
gcloud auth list --filter=status:ACTIVE --format="value(account)"
gcloud config get-value project
ssh -o ConnectTimeout=10 polyhive-controller "echo OK; hostname; uptime | head -1"
```
**Expected:** active account printed; project=`poetic-avenue-438401-a7`; `OK / phivea3m-controller / uptime line`.
**If polyhive-controller SSH fails:** stop — the agent's environment isn't set up for this work.

### 0.2 Verify the 17 PD snapshots exist and are READY
```bash
gcloud compute snapshots list --filter="name~pre-redeploy-20260530-2354 OR name~pre-sev1-orphan-20260530-2354" \
  --format="value(name,status)" | sort
```
**Expected:** 17 rows, every row ending with `READY`.
**If any row != READY or count != 17:** stop. The backups aren't safe to rely on.

### 0.3 Verify Nucleus cluster is unaffected and healthy (sanity)
```bash
gcloud compute instances list --filter="name~nucla3m" --format="value(name,status)"
```
**Expected:** 10 rows (1 controller + 1 login + 8 a3megagpu nodes), all `RUNNING`.

### 0.4 Verify the reservation isn't accidentally on the destroy plan
```bash
gcloud compute reservations describe $RESERVATION --zone=$ZONE \
  --format='value(specificReservation.count,specificReservation.inUseCount)'
```
**Expected:** `32  24` (32 slots, 24 in use = 16 Polyhive + 8 Nucleus).

### 0.5 Verify the IaC files are in cwd and the blueprint has all five fixes
```bash
ls -la a3mega-slurm-blueprint-polyhive.yaml a3mega-slurm-deployment-polyhive.yaml HANDOFF.md PROCEDURE.md RUNBOOK.md
grep -c "allow-ssh-external" a3mega-slurm-blueprint-polyhive.yaml
grep -c "bootstrap_controller_environment.sh" a3mega-slurm-blueprint-polyhive.yaml
grep -c "aperture-devices-mount.service" a3mega-slurm-blueprint-polyhive.yaml
grep -c "NOTE: the gpu-test epilog symlink" a3mega-slurm-blueprint-polyhive.yaml
```
**Expected:** all 5 files present; each grep returns ≥1 (firewall, bootstrap runner, aperture service, gpu-test note).

### 0.6 Verify a3mega-slurm-deployment-polyhive.yaml has the target node count = 24
The redeploy target is **24 nodes** (fills the 32-slot reservation with Nucleus's 8).
Nodes 0-15 will have /mnt/disk restored from snapshots; nodes 16-23 come up with
empty 2 TB disks (the customer populates them as workloads need).
```bash
grep -E "a3mega_cluster_size" a3mega-slurm-deployment-polyhive.yaml
```
**Expected:** `a3mega_cluster_size: 24`. **STOP if it says any other value.**

### 0.7 Push SSH pubkey to project metadata (if not already there)
```bash
PUBKEY_FILE=~/.ssh/id_ed25519.pub
[ -f $PUBKEY_FILE ] || PUBKEY_FILE=~/.ssh/id_rsa.pub
PUBKEY_LINE="ubuntu:$(cat $PUBKEY_FILE)"

# Check if our key is already in project metadata as 'ubuntu:'
PRESENT=$(gcloud compute project-info describe \
  --format='value(commonInstanceMetadata.items.filter(key:ssh-keys).extract(value))' 2>/dev/null \
  | tr ';' '\n' | grep -F "$(awk '{print $2}' $PUBKEY_FILE)" | wc -l)

if [ "$PRESENT" -eq 0 ]; then
  echo "Key not in project metadata; adding."
  # Get existing keys, append ours, push back
  TMPFILE=$(mktemp)
  gcloud compute project-info describe \
    --format='value(commonInstanceMetadata.items.filter(key:ssh-keys).extract(value))' 2>/dev/null \
    | tr ';' '\n' > $TMPFILE
  echo "$PUBKEY_LINE" >> $TMPFILE
  gcloud compute project-info add-metadata --metadata-from-file ssh-keys=$TMPFILE
  rm -f $TMPFILE
else
  echo "Key already in project metadata; no change."
fi
```

---

## Phase 1 — Teardown of the current Polyhive cluster

### 1.1 Drain workloads
```bash
ssh polyhive-controller "sudo scontrol update partitionname=phivea3mega state=DRAIN"
ssh polyhive-controller "squeue -t RUNNING -p phivea3mega"
```
**Expected (second command):** empty list (or only your own admin sessions).
**If running jobs remain:** stop and ask user. Don't cancel them.

### 1.2 Detach controller-save and polyhive-env-disk (PRESERVE, not delete)
```bash
for d in phivea3m-controller-save polyhive-env-disk; do
  gcloud compute instances detach-disk phivea3m-controller --zone=$ZONE --disk=$d
done

# Verify USERS column empty
gcloud compute disks list \
  --filter="name=phivea3m-controller-save OR name=polyhive-env-disk" \
  --format="table(name,users.basename())"
```
**Expected:** both disks show empty USERS.

### 1.3 Take fresh Filestore backup (extra safety net beyond what the destroy preserves)
```bash
gcloud filestore backups create pre-redeploy-polyhive-$(date +%Y%m%d-%H%M) \
  --instance=$POLYHIVE_FILESTORE \
  --instance-zone=$ZONE \
  --file-share=nfsshare \
  --region=$REGION
```
This call may exit "has not finished in 1800 seconds" — that's fine, the backup is
running server-side. Verify with:
```bash
gcloud filestore backups list --region=$REGION \
  --filter="name~pre-redeploy-polyhive" \
  --format="table(name.basename(),state,createTime,sourceInstance.basename())"
```
**Expected:** one row, state eventually `READY`. **WAIT FOR `READY` before proceeding.**

### 1.4 Defensive: delete any out-of-band firewall rules that pin sysnet
```bash
for r in allow-ssh-external allow-ssh-external-polyhive \
         a3mega-base-polyhive-allow-ssh-external; do
  gcloud compute firewall-rules delete $r --quiet 2>/dev/null || true
done
```
Non-existence is fine; this is purely defensive.

### 1.5 gcluster destroy — get the plan first, eyeball, then execute

Run from the cluster-toolkit working directory (where the `gcluster` binary lives,
with the existing `a3mega-base-polyhive/` working directory next to it).

If the working directory doesn't exist locally, regenerate it without applying:
```bash
./gcluster deploy \
  -d ./a3mega-slurm-deployment-polyhive.yaml \
     ./a3mega-slurm-blueprint-polyhive.yaml
# Answer 'n' when it asks to apply — we just want the dir materialized.
```

Then the destroy:
```bash
# Run WITHOUT --auto-approve first — print the plan
./gcluster destroy a3mega-base-polyhive
```
**Expected:** plan shows ~150-200 resources to destroy, BUT:
- Filestore `a3mega-base-polyhive-798798e0` should be reported as preserved (deletion_protection=true).
- Reservation `nvidia-h100-dkydfas6m486t` must NOT appear in the destroy plan.

**STOP IF:** the reservation is in the destroy plan, or any disk we want to preserve (`phivea3m-controller-save`, `polyhive-env-disk`, the 16 `phivea3m-a3meganodeset-N-1` disks — WAIT, those last 16 are getting destroyed and replaced. That's expected because their backup is in the snapshots. So scratch that — those should be in the destroy plan.) Let me revise:

**STOP IF:** the reservation is in the destroy plan OR `phivea3m-controller-save`/`polyhive-env-disk` is in the destroy plan OR the Filestore is going to be destroyed (rather than preserved).

If plan looks correct:
```bash
./gcluster destroy a3mega-base-polyhive --auto-approve
```

### 1.6 Handle the predictable filestore + PSC error
The destroy will get partway, then fail on the network because of:
1. Filestore deletion_protection → refuses to delete. **This is correct; preserve.**
2. PSC address pinning the sysnet → "resource is already being used".

To unblock the network destroy:
```bash
# 1. Filestore: state-rm so terraform stops trying to delete it (it remains alive in GCP)
cd a3mega-base-polyhive/cluster   # the cluster group's terraform dir
terraform state list | grep filestore
# Remove from state — expect something like module.homefs.google_filestore_instance...
terraform state rm <the.filestore.resource.address>
cd -

# 2. PSC: find the PSC address pinning the sysnet and delete it
gcloud compute addresses list --filter="purpose=PRIVATE_SERVICE_CONNECT AND name~polyhive" \
  --format="table(name,region,address,purpose,users)"
# Note the name; delete it
gcloud compute addresses delete <psc-address-name> --region=$REGION --quiet

# 3. Re-run destroy
./gcluster destroy a3mega-base-polyhive --auto-approve
```

### 1.7 Post-destroy sweep — orphans gcluster doesn't always reap
```bash
# A) Local deployment folder
rm -rf a3mega-base-polyhive

# B) Packer image
gcloud compute images list --filter="family=slurm-a3mega-polyhive" --format="value(name)" \
  | xargs -r -n1 gcloud compute images delete --quiet

# C) Residual gpunet/sysnet VPCs
gcloud compute networks list \
  --filter="name~(a3mega-base-polyhive-gpunet|polyhive-a3mega-sys-net)" \
  --format="value(name)" \
  | xargs -r -n1 gcloud compute networks delete --quiet

# D) Residual slurm config bucket (do NOT delete polyhive-a3mega-cluster — that's the backend bucket)
gcloud storage rm -r gs://slurm-phivea3mc39df 2>/dev/null || true
```

### 1.8 Verify clean state
```bash
gcloud compute reservations describe $RESERVATION --zone=$ZONE \
  --format='value(specificReservation.count,specificReservation.inUseCount)'
# Expected: 32  8   (Nucleus's 8 only; Polyhive freed 16)

gcloud compute instance-templates list --filter="name~phivea3m" --format='value(name)' | wc -l
# Expected: 0

gcloud compute instances list --filter="name~phivea3m" --format='value(name)' | wc -l
# Expected: 0

gcloud compute images list --filter="family~slurm-a3mega-polyhive" --format='value(name)' | wc -l
# Expected: 0

gcloud compute networks list --filter="name~(a3mega-base-polyhive|polyhive-a3mega-sys)" \
  --format='value(name)' | wc -l
# Expected: 0

# Preserved
gcloud compute disks list \
  --filter="name=phivea3m-controller-save OR name=polyhive-env-disk" \
  --format="table(name,users.basename())"
# Expected: both present, USERS empty

gcloud filestore instances describe $POLYHIVE_FILESTORE --zone=$ZONE --format='value(state)'
# Expected: READY
```

If all checks pass, Phase 1 is done.

---

## Phase 2 — Redeploy the Polyhive cluster

### 2.1 Confirm files are in cluster-toolkit working dir
```bash
ls -la a3mega-slurm-blueprint-polyhive.yaml a3mega-slurm-deployment-polyhive.yaml
which gcluster || ls -la ./gcluster
```

### 2.2 Deploy (apply runs primary → build_script → slurm-build → cluster groups in order)
```bash
./gcluster deploy \
  -d ./a3mega-slurm-deployment-polyhive.yaml \
     ./a3mega-slurm-blueprint-polyhive.yaml \
  --auto-approve 2>&1 | tee polyhive-deploy.log
```
**Expected:** "Apply complete!" at the end. ~30-45 min including the packer image build.

If apply fails, the gcluster modules are idempotent — read the error, fix the input,
re-run the same command. Do not manually create resources outside terraform.

### 2.3 Verify new cluster is up
```bash
gcloud compute instances list --filter="name~phivea3m" \
  --format="table(name,status,networkInterfaces[0].accessConfigs[0].natIP)"
# Expected: 26 rows — 1 controller, 1 login, 24 nodes — all RUNNING.

# Try SSH (should work IMMEDIATELY thanks to the blueprint's pre-create-/home/ubuntu fix)
ssh -o ConnectTimeout=10 polyhive-controller "echo OK; hostname; sinfo -N | head -5"
```
**Expected:** `OK / phivea3m-controller / sinfo output` — clean first-try SSH.

**If SSH still fails with "Permission denied (publickey)":** the blueprint runner may
have raced or the controller VM may need the OS-Login flip dance. See PROCEDURE.md
section 2a-pre and POST_DEPLOY_SSH_BOOTSTRAP.md for the fallback.

---

## Phase 3 — Re-attach preserved disks

### 3.1 Stop controllers briefly
```bash
gcloud compute instances stop phivea3m-controller --zone=$ZONE
# Wait for it to actually stop
until [ "$(gcloud compute instances describe phivea3m-controller --zone=$ZONE --format='value(status)')" = "TERMINATED" ]; do
  sleep 5
done
```

### 3.2 Attach preserved disks
```bash
gcloud compute instances attach-disk phivea3m-controller --zone=$ZONE \
  --disk=phivea3m-controller-save --device-name=phivea3m-controller-save --mode=rw

gcloud compute instances attach-disk phivea3m-controller --zone=$ZONE \
  --disk=polyhive-env-disk --device-name=polyhive-env-disk --mode=rw
```

### 3.3 Start controller, wait for slurmctld
```bash
gcloud compute instances start phivea3m-controller --zone=$ZONE
# Wait for SSH to come back
until ssh -o ConnectTimeout=8 polyhive-controller "true" 2>/dev/null; do sleep 5; done
# Verify slurmctld is up
ssh polyhive-controller "sudo systemctl is-active slurmctld"
```
**Expected:** `active`.

---

## Phase 4 — Restore /mnt/disk from the 2026-05-30 PD snapshots

Per-node operation: drain, stop, detach empty disk, delete empty disk, create from
snapshot, attach with the same device-name, start, resume. Run all 16 in parallel.

**Scope: nodes 0-15 only get their /mnt/disk restored from snapshots. Nodes 16-23
stay with empty 2 TB disks from the fresh deploy** — the customer populates them
as workloads need.

### 4.1 Wait until all 24 nodes are IDLE in slurm
```bash
until [ "$(ssh polyhive-controller "sinfo -p phivea3mega -t idle -h -o '%n'" | wc -l)" = "24" ]; do
  echo "$(date) waiting for 24 IDLE nodes..."
  ssh polyhive-controller "sinfo -p phivea3mega -N -o '%n %t'" | head -30
  sleep 30
done
echo "All 24 IDLE — proceeding with disk restore on nodes 0-15."
```

### 4.2 Drain nodes 0-15 for disk restore (leave 16-23 alone)
```bash
ssh polyhive-controller "sudo scontrol update nodename=phivea3m-a3meganodeset-[0-15] state=DRAIN reason='disk-restore-from-snapshot'"
```

### 4.3 Restore each of nodes 0-15's /mnt/disk from its snapshot (in parallel)
```bash
SNAP_DATE=20260530-2354

for N in 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
(
  NODE=phivea3m-a3meganodeset-$N
  SNAP=phive-disk-$N-pre-redeploy-$SNAP_DATE
  RESTORED=phive-restored-disk-$N

  echo "[node-$N] starting"
  gcloud compute instances stop $NODE --zone=$ZONE --quiet

  # Wait for stop
  until [ "$(gcloud compute instances describe $NODE --zone=$ZONE --format='value(status)')" = "TERMINATED" ]; do sleep 5; done

  # Find the empty data disk attached at device-name 'phive-data-disk'
  EMPTY=$(gcloud compute instances describe $NODE --zone=$ZONE \
    --format='value(disks[].source.basename(),disks[].deviceName)' | \
    awk -F';' 'BEGIN{RS=";"; prev=""} /phive-data-disk/{print prev} {prev=$1}' | head -1)
  EMPTY=${EMPTY:-${NODE}-1}

  # Detach + delete the empty disk
  gcloud compute instances detach-disk $NODE --zone=$ZONE --disk=$EMPTY --quiet
  gcloud compute disks delete $EMPTY --zone=$ZONE --quiet

  # Create restored disk from snapshot
  gcloud compute disks create $RESTORED --zone=$ZONE \
    --source-snapshot=$SNAP --size=2000 --type=pd-balanced --quiet

  # Attach with the SAME device-name (so /dev/disk/by-id/google-phive-data-disk resolves correctly)
  gcloud compute instances attach-disk $NODE --zone=$ZONE \
    --disk=$RESTORED --device-name=phive-data-disk --mode=rw

  # Start
  gcloud compute instances start $NODE --zone=$ZONE

  echo "[node-$N] done"
) &
done
wait
echo "All 16 disk restores submitted; waiting for VMs to come back up."
```

### 4.4 Verify each restored node (0-15) is up and /mnt/disk has restored content
```bash
for N in 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  STATUS=$(gcloud compute instances describe phivea3m-a3meganodeset-$N --zone=$ZONE --format='value(status)')
  echo "node-$N: VM=$STATUS"
done

# Once all 16 restored nodes are RUNNING, resume them in slurm
ssh polyhive-controller "sudo scontrol update nodename=phivea3m-a3meganodeset-[0-15] state=RESUME"

# Wait for all 24 to be IDLE again
until [ "$(ssh polyhive-controller "sinfo -p phivea3mega -t idle -h -o '%n'" | wc -l)" = "24" ]; do
  sleep 30
done

# Sanity check /mnt/disk content on ALL 24 (16 restored + 8 fresh-empty)
ssh polyhive-controller 'for N in $(seq 0 23); do
  srun -w phivea3m-a3meganodeset-$N -N1 --gres=gpu:8 --exclusive -t 1 \
    bash -c "echo node-$N:; df -h /mnt/disk | tail -1; ls /mnt/disk | head -3; echo"
done'
```
**Expected:**
- Nodes 0-11, 13, 15 → /mnt/disk ~1.7 TB used, contains training data (`beige_squirrel_v9`, `pink_zebra_synthetic_v1_single_op`, etc).
- Node-12 → /mnt/disk essentially empty (the post-SEV1 snapshot was ~11 MiB; faithful empty restore).
- Node-14 → small data (~2.9 GiB compressed snapshot). Also expected.
- Nodes 16-23 → empty 2 TB ext4 (`lost+found` only) — fresh capacity, not restored from snapshot.

---

## Phase 5 — Validation

### 5.1 Aperture devices check (must be 8 on every node — all 24)
```bash
ssh polyhive-controller 'for N in $(seq 0 23); do
  out=$(srun -w phivea3m-a3meganodeset-$N -N1 --gres=gpu:8 --exclusive -t 1 \
        bash -c "ls /dev/aperture_devices 2>/dev/null | wc -l" 2>/dev/null)
  echo "  phivea3m-a3meganodeset-$N: aperture=$out"
done'
```
**Expected:** every one of the 24 nodes reports `aperture=8`. **If any reports 0:** the systemd safety-net
service should self-correct on next reboot, but you can manually trigger:
`srun -w <node> --gres=gpu:8 --exclusive -t 2 sudo systemctl start aperture-devices-mount.service`.

### 5.2 No drains, all 24 IDLE
```bash
ssh polyhive-controller "sinfo -p phivea3mega -N -o '%.30n %.10t %.30E'"
```
**Expected:** 24 rows, all `idle` state, REASON column empty.

### 5.3 2-node NCCL/TCPXO sanity (uses the Nucleus recipe)
The NCCL test scripts live in `/home/ubuntu/.post-destroy-validation-052926/` on
nucla3m-controller (Nucleus's /home), but they're not on Polyhive. Copy them over:
```bash
ssh polyhive-controller "mkdir -p /home/ubuntu"
scp polyhive-controller:/dev/null /tmp/x 2>/dev/null  # warm ssh
ssh n-controller 'cat /home/ubuntu/.post-destroy-validation-052926/nccl_test.py' | \
  ssh polyhive-controller "cat > /home/ubuntu/nccl_test.py"
ssh n-controller 'cat /home/ubuntu/.post-destroy-validation-052926/nccl_run.sh' | \
  ssh polyhive-controller "cat > /home/ubuntu/nccl_run.sh && chmod +x /home/ubuntu/nccl_run.sh"

# Submit a 2-node test
ssh polyhive-controller 'cat > /tmp/nccl-2node.sh' <<'EOF'
#!/bin/bash
#SBATCH -p phivea3mega
#SBATCH --nodelist=phivea3m-a3meganodeset-[0-1]
#SBATCH --ntasks-per-node=8
#SBATCH --gres=gpu:8
#SBATCH --exclusive
#SBATCH -t 15
#SBATCH -o /home/ubuntu/nccl-%j.out
#SBATCH -e /home/ubuntu/nccl-%j.err
export MASTER_ADDR=$(scontrol show hostnames $SLURM_JOB_NODELIST | head -1)
export MASTER_PORT=29500
echo "JOB=$SLURM_JOB_ID master=$MASTER_ADDR procs=$SLURM_NTASKS"
srun --container-image="nvcr.io#nvidia/pytorch:24.07-py3" \
     --container-mounts=/var/lib/tcpxo:/var/lib/tcpxo,/dev/aperture_devices:/dev/aperture_devices,/home/ubuntu:/home/ubuntu \
     /home/ubuntu/nccl_run.sh
EOF
ssh polyhive-controller "sbatch /tmp/nccl-2node.sh"
```

Wait for the job to complete, then check bandwidth:
```bash
ssh polyhive-controller "ls -la /home/ubuntu/nccl-*.out /home/ubuntu/nccl_trace.*.0.log 2>/dev/null"
ssh polyhive-controller "grep -E 'size.*MB.*busbw|FasTrak' /home/ubuntu/nccl_trace.*.0.log 2>/dev/null | head -20"
```
**Expected:** `NET/FasTrak` lines + bandwidth lines reaching ~200-300 GB/s busbw at 1 GB tensor size.

### 5.4 Confirm restored /mnt/disk is intact (and document the new empty nodes)
```bash
ssh polyhive-controller 'for N in $(seq 0 23); do
  srun -w phivea3m-a3meganodeset-$N -N1 --gres=gpu:8 --exclusive -t 1 \
    bash -c "echo node-$N:; df -h /mnt/disk | tail -1; ls /mnt/disk | head -5; echo"
done | tee /tmp/restore-verify-$(date +%s).log'
```
**Expected:**
- Nodes 0-11, 13, 15 → ~1.7 TB used, restored training datasets.
- Node-12, 14 → as called out in Phase 4.4 (essentially empty / small).
- Nodes 16-23 → empty 2 TB ext4 (fresh capacity).

### 5.5 Reservation utilization
```bash
gcloud compute reservations describe $RESERVATION --zone=$ZONE \
  --format='value(specificReservation.count,specificReservation.inUseCount)'
```
**Expected:** `32  32` (24 polyhive + 8 nucleus = full).

---

## Phase 6 — Hand-back, then user-driven cleanup

### 6.1 Update the on-disk handoff doc with the redeploy completion
```bash
ssh n-controller "cat >> /home/ubuntu/nucleus-admin/debug-052926/HANDOFF.md" <<EOF

---

## Update — $(date -u +%Y-%m-%d): Polyhive redeploy + restore complete

- Polyhive teardown + redeploy executed per RUNBOOK.md.
- **24 nodes** up (rebalanced from 16 to fill the 32-slot reservation), IDLE, aperture=8 on all, NCCL/TCPXO at expected bandwidth.
- /mnt/disk on nodes 0-15 restored from the 2026-05-30 PD snapshots:
  - 14 nodes with full data (0-11, 13, 15)
  - node-12 with the post-SEV1 (empty) snapshot
  - node-14 with its small 2.9 GiB snapshot
- Nodes 16-23 came up with empty 2 TB disks (fresh capacity for the customer).
- Reservation now at 32/32 utilization (24 polyhive + 8 nucleus).
- Handed back to Polyhive customer for JuiceFS restage and workflow validation.
- 17 PD snapshots STILL PRESENT (do not delete until customer signs off on data integrity).
EOF
```

### 6.2 Notify the user (the operator who set this work in motion)
Send a Slack/email/text to the user that all 6 phases are complete. Include:
- Final aperture sweep
- 2-node NCCL bandwidth
- Any nodes with unexpected /mnt/disk state
- Reminder: snapshots are still alive and costing ~$210/month — delete only after customer sign-off.

### 6.3 STOP HERE — do not delete the snapshots autonomously
The user will (with confirmation from Polyhive's owner) run this command later to
free the ~$210/month snapshot storage:
```bash
# DO NOT RUN THIS WITHOUT EXPLICIT USER GO-AHEAD AFTER CUSTOMER SIGN-OFF
gcloud compute snapshots list --filter="name~20260530-2354" --format='value(name)' \
  | xargs -r -n1 gcloud compute snapshots delete --quiet
```

---

## Appendix A — Recovery from failed restore (per-node)

If one node's restore fails mid-flow (e.g. snapshot create succeeded but attach
failed), the disk is named `phive-restored-disk-<N>` and exists in the zone. Clean
state:
```bash
# Where N is the affected node number
N=<NODE>
gcloud compute disks delete phive-restored-disk-$N --zone=$ZONE --quiet 2>/dev/null
# Then re-run the per-node block from Phase 4.3 for just that node.
```

## Appendix B — How to bail out mid-way

If something looks wrong and the user hasn't woken up:
1. STOP further commands.
2. Write a brief diagnosis to a file you can show on resume:
   `~/runbook-aborted-$(date +%Y%m%dT%H%M).log` with the failing command, the output,
   and the current cluster state.
3. Push to `gs://nucleus-a3mega-cluster/handoff/aborted-runs/` so it's durable.
4. Wait for user input. Don't roll back disk operations unless explicitly told.

## Appendix C — Quick reference: where files live

| Where | What |
|---|---|
| User's mac, working dir | HANDOFF.md, PROCEDURE.md, RUNBOOK.md, PROMPT.md, 4 YAMLs |
| `n-controller:/home/ubuntu/nucleus-admin/debug-052626/phase2/final/` | mirror of YAMLs + PROCEDURE.md |
| `n-controller:/home/ubuntu/nucleus-admin/debug-052926/` | HANDOFF.md mirror |
| `gs://nucleus-a3mega-cluster/handoff/` | gold copy of all docs + YAMLs |
| `gs://polyhive-a3mega-cluster/handoff/` | second gold copy |
