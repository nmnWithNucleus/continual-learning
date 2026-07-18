# Cluster takedown + redeploy — 32-node reservation -> Polyhive 24 + Nucleus 8

> **⚠️ ERRATA (2026-05-31):** The Polyhive half of this procedure was executed on
> 2026-05-31. See **`EXECUTION-LOG-20260531.md`** for what actually happened and the
> RUNBOOK.md errata banner for the short list. Key correction to **§2d below**: the
> per-node stop→detach→swap→start disk restore **does not work** — these VMs are
> resize-request-created and cannot be stopped. The surviving original per-node disks
> (`phivea3m-a3meganodeset-N-1`) were hot-attached directly instead. Also: the destroy
> plan tried to delete `/home` Filestore + `/gcs` bucket (state-rm both first).

This is the runbook for tearing down both live a3mega clusters and redeploying them
from the four YAMLs in this folder. Read end-to-end before you start.

Files this references:
- `a3mega-slurm-blueprint-polyhive.yaml`  + `a3mega-slurm-deployment-polyhive.yaml`   (24 nodes)
- `a3mega-slurm-blueprint-nucleus.yaml`   + `a3mega-slurm-deployment-nucleus.yaml`    (8 nodes)

Project: `poetic-avenue-438401-a7` · zone: `us-east4-b` · shared reservation:
`nvidia-h100-dkydfas6m486t` (32 slots — must NOT be deleted; 24 + 8 will consume it).

---

## 0. Pre-flight — confirm before you touch anything

1. **Customer maintenance window scheduled with BOTH sides** (Polyhive + Nucleus). Both
   clusters go down together — you can't redeploy one while the other holds 16 slots,
   because 16 + 24 > 32.
2. **Terraform backend buckets — verified.** Both exist and hold the live state from the
   original Dec-9 provisioning (serial 9, untouched since):
   - `gs://polyhive-a3mega-cluster/polyhive-a3mega-slurm/a3mega-base-polyhive/{primary,build_script,cluster}/default.tfstate`
   - `gs://nucleus-a3mega-cluster/nucleus-a3mega-slurm/a3mega-base-nucleus/{primary,build_script,cluster}/default.tfstate`
   The 2026-05 incident work (8 templates recreated out-of-band, node-12 rebuild) is NOT
   reflected in this state — expect drift on `terraform refresh` (compute-template
   replacement, controller save-disk diffs). That's OK because step 1c is a destroy anyway.
3. **Reservation is healthy:**
   `gcloud compute reservations describe nvidia-h100-dkydfas6m486t --zone=us-east4-b --project=poetic-avenue-438401-a7`
   → `count=32`. Do NOT delete or modify it.
4. **JuiceFS is Polyhive's responsibility.** Compute nodes will come up without it; we
   hand back to Polyhive for restaging post-redeploy. No JuiceFS-related disks to
   preserve on our side — Polyhive's data is in their object storage.

### What survives the takedown (PRESERVE these — detach, don't delete)

| Resource | Why |
|---|---|
| `phivea3m-controller-save`, `nucla3m-controller-save` (50 GB pd-ssd each) | Slurm state (jobs/accounting). Re-attach to new controllers. |
| `polyhive-env-disk` (200 GB) | Polyhive controller env. |
| Filestores `a3mega-base-polyhive-798798e0` and `a3mega-base-nucleus-d1946fef` (10 TB each, `nfsshare`) | User `/home`. Blueprint sets `deletion_protection: true` — keep that on. |
| Data buckets (`a3mega-base-*-c*`, gcsfuse-mounted at `/gcs`) | May contain customer data. |
| Shared reservation `nvidia-h100-dkydfas6m486t` | Required for both new clusters. |
| JuiceFS object-storage (juicefs.io tokens) | Customer-owned; outside our control. Safe. |

### What is acceptable to lose

- All compute VMs (their `phive-data-disk` per-node 2 TB scratch — customer treated as ephemeral).
- All instance templates (we recreate from new YAMLs).
- VPCs, subnets, routers, firewalls, addresses (recreated with the same deployment_name
  prefix — so the new ones will be named identically to the old ones).
- private_service_access connection (recreated).

### Snapshot before destroy (recommended, ~minutes per disk)

```
# Snapshots are cheap and let you roll back if anything goes sideways.
gcloud compute disks snapshot phivea3m-controller-save --zone=us-east4-b --snapshot-names=pre-redeploy-phivea3m-ctlsave
gcloud compute disks snapshot nucla3m-controller-save  --zone=us-east4-b --snapshot-names=pre-redeploy-nucla3m-ctlsave
gcloud compute disks snapshot polyhive-env-disk         --zone=us-east4-b --snapshot-names=pre-redeploy-poly-env
# HIGH_SCALE_SSD filestores are zonal, so --instance-zone is required even with --region set.
# NOTE: 10 TB filestore backups commonly exceed gcloud's 30-min local wait. If you see
# "has not finished in 1800 seconds", the operation is still running server-side — wait
# and verify with `gcloud filestore backups list` (below). Re-running the create will
# error ALREADY_EXISTS once the first one finishes; that's fine.
gcloud filestore backups create pre-redeploy-polyhive \
  --instance=a3mega-base-polyhive-798798e0 --instance-zone=us-east4-b \
  --file-share=nfsshare --region=us-east4
gcloud filestore backups create pre-redeploy-nucleus \
  --instance=a3mega-base-nucleus-d1946fef --instance-zone=us-east4-b \
  --file-share=nfsshare --region=us-east4

# Verify both backups landed in READY state before proceeding.
gcloud filestore backups list --region=us-east4 \
  --filter='name~pre-redeploy' \
  --format='table(name.basename(),state,createTime,sourceInstance.basename())'
```

---

## 1. Takedown

### 1a. Drain workloads (both clusters)

From each controller (`phivea3m-controller`, `nucla3m-controller`):
```
sudo scontrol update partitionname=phivea3mega state=DRAIN     # Polyhive
sudo scontrol update partitionname=a3mega      state=DRAIN     # Nucleus (partition name may differ; check sinfo)
squeue                          # wait until empty (or scancel deliberately)
```

### 1b. Detach the disks to preserve

These have `auto_delete=false`, but the instances they're attached to will be deleted.
Detach explicitly so they aren't surprised by something else. Do this BEFORE deleting
the controllers.

```
# Polyhive controller
for d in phivea3m-controller-save polyhive-env-disk; do
  gcloud compute instances detach-disk phivea3m-controller --zone=us-east4-b --disk=$d
done
# Nucleus controller
gcloud compute instances detach-disk nucla3m-controller --zone=us-east4-b --disk=nucla3m-controller-save
```

Verify they're detached (USERS column empty):
```
gcloud compute disks list --filter='name~(controller-save|polyhive-env-disk)' --format='table(name,users.basename())'
```

### 1c. Destroy (gcluster-managed, per cluster)

`gcluster destroy <deployment_name>` walks the deployment groups in reverse and runs
`terraform destroy` for each, using the live remote backend. Must be run from a host
that has the original `<deployment_name>/` working directory next to the `gcluster`
binary (the one created by the original `gcluster deploy`). If you no longer have it,
regenerate locally first: `gcluster deploy -d <deployment>.yaml <blueprint>.yaml`
WITHOUT applying, just to materialize the dir, then destroy.

**Recommended: run once WITHOUT `--auto-approve` to read the plan**, then re-run with
`--auto-approve` once it looks sane. The state has known drift from our 2026-05
incident work (8 templates recreated out-of-band, node-12 rebuild), so expect to see
templates being re-destroyed and possibly an instance whose attributes differ.

```
# From the cluster-toolkit working directory
./gcluster destroy a3mega-base-polyhive   # review the plan
./gcluster destroy a3mega-base-polyhive --auto-approve

./gcluster destroy a3mega-base-nucleus    # review the plan
./gcluster destroy a3mega-base-nucleus --auto-approve
```

**Guardrails:**
- Filestores have `deletion_protection: true` — `terraform destroy` will refuse to
  delete them. That's the correct behavior; keep `/home` data.
- The shared reservation is *not* in the cluster's terraform state, so destroy can't
  touch it. (If your state somehow has it, `terraform state rm` the resource first.)
- The `*-controller-save` and `polyhive-env-disk` disks were detached in 1b, so destroy
  won't take them.

**Out-of-band firewall rules will block the sysnet destroy.** SSH rules pin the sysnet
alive so the network delete fails with "resource is already being used". Delete them
BEFORE re-running destroy.

There are two possible rule sources to clean up:
1. **Old manually-created rules** (from earlier deploys, before the 2026-05-31 blueprint
   update): `allow-ssh-external`, `allow-ssh-external-nucleus`.
2. **Blueprint-managed rule from the now-updated blueprint** (named
   `${deployment_name}-allow-ssh-external` — e.g. `a3mega-base-polyhive-allow-ssh-external`).
   This is in terraform state, so `terraform destroy` SHOULD reap it. If destroy fails on
   the network because of it, delete manually as below.

```
# Defensive cleanup — won't error if the rule doesn't exist
gcloud compute firewall-rules delete allow-ssh-external          --quiet 2>/dev/null || true
gcloud compute firewall-rules delete allow-ssh-external-nucleus  --quiet 2>/dev/null || true
gcloud compute firewall-rules delete a3mega-base-polyhive-allow-ssh-external --quiet 2>/dev/null || true
gcloud compute firewall-rules delete a3mega-base-nucleus-allow-ssh-external  --quiet 2>/dev/null || true
```

**Instance templates we recreated via API during the 2026-05 incident recovery may not
get reaped by destroy** (terraform's state has the original Dec-9 attributes; our
recreates have different fingerprints). After destroy, sweep manually:
```
gcloud compute instance-templates list --filter='name~(nucla3m|phivea3m)' --format='value(name)' \
  | xargs -r -n1 gcloud compute instance-templates delete --quiet
```

### 1d. Post-destroy cleanup (orphans gcluster doesn't always reap)

```
# 1) Cluster-toolkit local deployment folders — delete so the next deploy is clean.
rm -rf a3mega-base-polyhive a3mega-base-nucleus

# 2) Compute Engine images (packer-built) — these often outlive `terraform destroy`
#    because packer creates them outside the cluster's terraform state.
gcloud compute images list --filter='family=slurm-a3mega-polyhive OR family=slurm-a3mega-nucleus' \
  --format='value(name)' | xargs -r -n1 gcloud compute images delete --quiet

# 3) Residual gpunet / sysnet VPCs (in case any leaked past destroy)
gcloud compute networks list \
  --filter='name~(a3mega-base-polyhive-gpunet|a3mega-base-nucleus-gpunet|polyhive-a3mega-sys-net|nucleus-a3mega-sys-net)' \
  --format='value(name)' | xargs -r -n1 gcloud compute networks delete --quiet

# 4) Residual slurm config buckets (NOT the backend buckets; those stay so the
#    next gcluster deploy can write fresh state into them).
gsutil -m rm -r gs://slurm-phivea3mc39df 2>/dev/null || true
gsutil -m rm -r gs://slurm-nucla3m20e97  2>/dev/null || true
# NOTE: do NOT delete polyhive-a3mega-cluster / nucleus-a3mega-cluster (the
#       terraform-backend buckets) or jfs-*/worldmodel*/employee-backup* (unrelated).
```

### 1e. Verify clean state

```
gcloud compute reservations describe nvidia-h100-dkydfas6m486t --zone=us-east4-b \
  --format='value(specificReservation.count,specificReservation.inUseCount)'
# expect "32  0"   (32 slots, 0 in use)
gcloud compute instance-templates list --filter='name~(phivea3m|nucla3m)'   # expect empty
gcloud compute instances        list  --filter='name~(phivea3m|nucla3m)'    # expect empty
gcloud compute images           list  --filter='family~slurm-a3mega-'       # expect empty
gcloud compute networks         list  --filter='name~(a3mega-base-(polyhive|nucleus)|polyhive-a3mega-sys|nucleus-a3mega-sys)'   # expect empty
gcloud compute disks            list  --filter='name~(controller-save|polyhive-env)' \
  --format='table(name,users.basename())'                                   # expect USERS empty
```

---

## 2. Redeploy

### 2a-pre. Post-deploy environment (now baked into blueprint — 2026-05-31)

**Heads-up for the new agent:** the blueprint as of 2026-05-31 now handles three
things automatically that previously required manual post-deploy steps:

| Old manual step | Now blueprint-managed |
|---|---|
| `gcloud compute firewall-rules create allow-ssh-external...` | `firewall_rules` on `sysnet` (named `${deployment_name}-allow-ssh-external`) |
| `sudo mkdir /home/ubuntu` on the controller to fix guest-agent | `controller_startup` → `bootstrap_controller_environment.sh` pre-creates it |
| OS-Login on→off→on→off "flip dance" to bootstrap SSH | NOT NEEDED anymore (because /home/ubuntu exists from minute one) |
| `mkdir /gcs && mount /gcs` on the controller manually | `controller_startup` → same bootstrap runner replays gcsfuse entries from config.yaml |

**Still required from the operator post-deploy** (NOT blueprint-managed because it's a
user credential, not infra):
- Push your SSH pubkey into project- or instance-level `ssh-keys` metadata.

```bash
# One-line push to project metadata as `ubuntu:<pubkey>` (visible to all VMs that
# have OS-Login=FALSE — that's what we want here).
gcloud compute project-info add-metadata \
  --metadata-from-file ssh-keys=<(echo "ubuntu:$(cat ~/.ssh/id_ed25519.pub)")
```

(If somebody already pushed an `ubuntu:` key — verify with
`gcloud compute project-info describe --format='value(commonInstanceMetadata.items.filter(key:ssh-keys).extract(value))' | tr ';' '\n'`
— you can either add your key alongside it or skip.)

After `gcluster deploy` completes, you should be able to `ssh ubuntu@<controller-ip>`
on the first try — no bootstrap dance needed.

### 2a. Generate and apply (one cluster at a time)

Put the four YAMLs from this folder next to your `gcluster` binary (or pass full
paths). Then, for each cluster, the same pattern as your provisioning notes:

```
# Polyhive (24 nodes)
./gcluster deploy \
  -d /path/to/a3mega-slurm-deployment-polyhive.yaml \
     /path/to/a3mega-slurm-blueprint-polyhive.yaml \
  --auto-approve

# Nucleus (8 nodes)
./gcluster deploy \
  -d /path/to/a3mega-slurm-deployment-nucleus.yaml \
     /path/to/a3mega-slurm-blueprint-nucleus.yaml \
  --auto-approve
```

`gcluster deploy` runs the deployment groups in order — `primary` (networks) →
`build_script` (staging) → `slurm-build` (packer image build, ~20 min) → `cluster`
(filestore, controller, login, templates, compute). The compute nodes will then
auto-power-up against the shared reservation (24 + 8 = 32 slots).

**First-time deploys are safe to `--auto-approve`** (there's nothing to destroy). If
you ever re-deploy ON TOP of an existing state, drop `--auto-approve` and read the
plan — that's when surprises happen.

If something fails mid-deploy, the cluster-toolkit modules are idempotent: fix the
input and re-run. To run only a specific group, `gcluster deploy ... --only=<group>`
(useful if you only need to re-apply `cluster`).

### 2b. Re-attach preserved disks (before users come online)

Once `phivea3m-controller` / `nucla3m-controller` exist (terraform creates them), and
before announcing the cluster:

```
# Stop the controllers briefly so slurmctld doesn't trip while disks attach
gcloud compute instances stop phivea3m-controller --zone=us-east4-b
gcloud compute instances stop nucla3m-controller  --zone=us-east4-b

# Polyhive
gcloud compute instances attach-disk phivea3m-controller --zone=us-east4-b --disk=phivea3m-controller-save --device-name=phivea3m-controller-save --mode=rw
gcloud compute instances attach-disk phivea3m-controller --zone=us-east4-b --disk=polyhive-env-disk        --device-name=polyhive-env-disk        --mode=rw

# Nucleus
gcloud compute instances attach-disk nucla3m-controller --zone=us-east4-b --disk=nucla3m-controller-save --device-name=nucla3m-controller-save --mode=rw

# Start the controllers
gcloud compute instances start phivea3m-controller --zone=us-east4-b
gcloud compute instances start nucla3m-controller  --zone=us-east4-b
```

After the controllers are up, confirm slurm state was picked up (`sinfo`, `sacct`).
If slurmctld can't find its state because of a device-name change, mount the disk and
symlink as needed (compare to the device path slurmctld expects under `/save` or
`/var/spool/slurm`).

### 2c. Power up compute nodes

Static nodes auto-resume; if not, push them:
```
sudo scontrol update partitionname=phivea3mega state=UP                 # Polyhive
sudo scontrol update partitionname=<nucleus_partition> state=UP         # Nucleus (check sinfo for actual name)
sudo scontrol update nodename=phivea3m-a3meganodeset-[0-15] state=POWER_UP
sudo scontrol update nodename=nucla3m-a3meganodeset-[0-7]   state=POWER_UP
```
Watch resume: `sudo tail -f /var/log/slurm/resume.log` on each controller.

### 2d. Polyhive: restore each `/mnt/disk` from the 2026-05-30 PD snapshots

> **⚠️ DOES NOT WORK AS WRITTEN (proven 2026-05-31).** Every `gcloud compute instances
> stop` below fails with `HTTPError 400: Operation is not supported for VMs created
> with resize requests`. a3-mega nodes here are resize-request/DWS-created and cannot
> be stopped. **Use the method that actually worked instead** (EXECUTION-LOG §Phase 4):
> the teardown leaves the original per-node disks `phivea3m-a3meganodeset-N-1` intact
> (auto_delete=false) — these ARE the real `/mnt/disk` data, so no snapshot restore is
> needed. Per node, on the RUNNING VM:
> ```bash
> ssh p-node-$N "sudo umount /mnt/disk"
> gcloud compute instances detach-disk phivea3m-a3meganodeset-$N --zone=$ZONE --device-name=phive-data-disk   # the empty -xxxx deploy disk
> gcloud compute instances attach-disk phivea3m-a3meganodeset-$N --zone=$ZONE --disk=phivea3m-a3meganodeset-$N-1 --device-name=phive-data-disk --mode=rw
> ssh p-node-$N "for i in \$(seq 1 20); do [ -e /dev/disk/by-id/google-phive-data-disk ] && break; sleep 2; done; sleep 3; sudo mount /mnt/disk"
> ```
> Gotchas: wait for the by-id symlink to settle after detach/attach; use `-o noload`
> or mount by UUID if a crash-consistent ext4 journal is dirty; node-12's disk had NO
> filesystem (mkfs fresh — it's legitimately empty per SEV-1 history). The snapshot
> route below is a backup-of-last-resort only, and beware SSD_TOTAL_GB quota (each
> 2 TB restore disk counts; we hit the 126 TB ceiling).

This is new — added 2026-05-31 after the Polyhive disk backup pivoted from
GCS-bucket-rsync to PD snapshots. **Run this only on the Polyhive cluster** (Nucleus
has no per-node 2 TB disk).

The 17 snapshots (16 active + 1 pre-SEV1 orphan that turned out empty) live in the
project at:
```
gcloud compute snapshots list --filter="name~20260530-2354" \
  --format="table(name,sourceDisk.basename(),diskSizeGb,storageBytes.size(),status)"
```

**Restore flow per node** — the new Polyhive cluster was just deployed with EMPTY 2 TB
disks attached at `device-name=phive-data-disk`. We detach those empty disks, create
new disks from the snapshots, attach the restored disks with the same device-name
(so `/dev/disk/by-id/google-phive-data-disk` resolves to the right device on the VM),
then remount.

```bash
SNAP_DATE=20260530-2354
ZONE=us-east4-b
PROJECT=poetic-avenue-438401-a7

# Wait until the new Polyhive cluster's nodes are up and IDLE in slurm before
# starting this. Restoration involves a disk swap that requires stopping the node.

for N in 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  NODE=phivea3m-a3meganodeset-$N
  SNAP=phive-disk-$N-pre-redeploy-$SNAP_DATE
  RESTORED=phive-restored-disk-$N

  echo "=== node-$N restore ==="

  # 1) Drain + stop the node so we can swap disks
  ssh phivea3m-controller "sudo scontrol update nodename=$NODE state=DRAIN reason='disk-restore'"
  gcloud compute instances stop $NODE --zone=$ZONE --quiet

  # 2) Find and detach the empty data disk that the new deploy created
  EMPTY_DISK=$(gcloud compute instances describe $NODE --zone=$ZONE \
    --format='value(disks[].source.basename(),disks[].deviceName)' | \
    awk -F';' 'BEGIN{RS=";"} /phive-data-disk/ {print prev} {prev=$1}' | head -1)
  # (simpler: it's usually $NODE-1, but the awk above handles edge cases)
  EMPTY_DISK=${EMPTY_DISK:-${NODE}-1}
  gcloud compute instances detach-disk $NODE --zone=$ZONE --disk=$EMPTY_DISK

  # 3) Delete the empty disk (no data on it, just-deployed)
  gcloud compute disks delete $EMPTY_DISK --zone=$ZONE --quiet

  # 4) Create the restored disk from the snapshot
  gcloud compute disks create $RESTORED \
    --zone=$ZONE \
    --source-snapshot=$SNAP \
    --size=2000 \
    --type=pd-balanced

  # 5) Attach it under the SAME device-name (so /dev/disk/by-id/google-phive-data-disk works)
  gcloud compute instances attach-disk $NODE \
    --zone=$ZONE \
    --disk=$RESTORED \
    --device-name=phive-data-disk \
    --mode=rw

  # 6) Start the VM
  gcloud compute instances start $NODE --zone=$ZONE

  # 7) Bring back to service
  ssh phivea3m-controller "sudo scontrol update nodename=$NODE state=RESUME"
  echo "node-$N done"
done

# 8) Verify on each node that /mnt/disk is mounted and has the data
ssh phivea3m-controller 'for N in 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  srun -w phivea3m-a3meganodeset-$N -N1 --gres=gpu:8 --exclusive -t 1 \
    bash -c "df -h /mnt/disk | tail -1; ls /mnt/disk | head -3"
done'
```

**Parallel variant:** the per-node block above can be wrapped in `&` + `wait` so all 16
restores run in parallel. Disk creation from snapshot is server-side and fast (~2–5 min
per disk regardless of compressed size). Total wall clock with parallelism: ~10 min.

**Special cases:**
- node-12: its snapshot (`phive-disk-12-pre-redeploy-20260530-2354`) is essentially empty
  (11 MiB — just the ext4 metadata after the SEV-1 recovery). Restoring it is fine but
  effectively a no-op; you could alternatively just keep the empty disk created by the
  deploy.
- The `phive-disk-12-pre-sev1-orphan-20260530-2354` snapshot is 0 bytes (the pre-SEV1
  disk was already empty when it became orphaned). Nothing to restore from it. Delete
  it after redeploy to save ~$0 (it costs nothing but adds noise).

**After all restores are done and validated**, delete the snapshots to stop the
~$210/month storage cost (only do this after Polyhive has signed off on data integrity):
```
gcloud compute snapshots list --filter="name~20260530-2354" --format='value(name)' | \
  xargs -r -n1 gcloud compute snapshots delete --quiet
```

---

## 3. Customer-side restoration (NOT our scope — handed back to Polyhive)

Compute nodes come up clean from the new image. The blueprint already handles
`/mnt/disk` (per-node 2 TB on Polyhive) and the docker dead-container janitor.
**Everything else is customer-owned infra.** After step 2c, hand back to Polyhive to:

- Re-stage JuiceFS on the compute nodes (binary + their unit files + tokens for
  `/jfs-spectral`, `/jfs-spectral-r2`, `/jfs-spectral-s3`, `/jfs-phive`, `/jfs-useast`).
  Polyhive owns this end-to-end.
- Re-mount `polyhive-env-disk` on the new `phivea3m-controller` (step 2b attaches it;
  the mountpoint/fstab line is Polyhive's call).
- Any other workflow setup (paths, scripts, env) on the new cluster.

We don't put any of the above in our IaC — those are customer secrets / customer
infra. Once we hand back, Polyhive validates their workflows end-to-end.

---

## 4. Validation (before customer hand-back)

```
# All nodes registered, no drains
sinfo -p phivea3mega -N -o "%.30n %.10t %.20E"     # Polyhive — 24 idle
sinfo                                              # Nucleus — 8 idle
# Reservation now 32/32
gcloud compute reservations describe nvidia-h100-dkydfas6m486t --zone=us-east4-b \
  --format='value(specificReservation.count,specificReservation.inUseCount)'

# One GPU job on each cluster, exercises prolog/epilog (rxdm), mounts
srun --partition=phivea3mega -w phivea3m-a3meganodeset-0 -N1 --gres=gpu:8 --exclusive -t 4 \
  bash -lc 'nvidia-smi --query-gpu=count --format=csv,noheader; ls /dev/aperture_devices | wc -l; df -h | grep -E "/home|/gcs|/mnt|jfs"'

# Customer-side smoke: an sbatch with --output and --chdir under /jfs-spectral-r2 (where the customer's
# real workflows run). Confirms JuiceFS is up.
```

Use the existing `phase2/verify_3node_no_drain.sh` (adjust node names for the new layout) to
confirm prolog+epilog don't drain.

---

## 5. Rollback considerations

- **Snapshots from step 0** are your safety net for the preserved disks and Filestore.
  If anything corrupts state during re-attach, restore from snapshot.
- **If terraform apply leaves the deployment half-built**, fix and re-apply — the
  cluster-toolkit modules are idempotent. Don't manually create resources outside
  terraform unless you're prepared to import them later.
- **If a compute node won't register**, the most common cause now (lessons from
  node-12) is something missing from the *new* image or a missing `startup-script`
  metadata key on the template — the blueprint takes care of both, but verify the
  template includes `startup-script` (gcloud compute instance-templates describe …).

---

## 5b. Orphan cleanup commands (reference)

These are commands that were used on 2026-05-31 to clean up Nucleus orphans after the
redeploy. Keep them here as reference for the Polyhive cleanup (after Polyhive
redeploys + restores successfully + you've confirmed data integrity).

```bash
# A) Delete the old cluster-storage bucket (only AFTER restore is verified)
# This iterates through millions of objects, so run with --recursive --no-user-output-enabled.
# Can take 30+ min for ~10 TB / 5M+ objects.
gcloud storage rm --recursive --no-user-output-enabled gs://<old-bucket-name>/
gcloud storage buckets delete gs://<old-bucket-name>

# B) Delete the old Filestore (only AFTER /home is verified migrated)
# First disable deletion_protection, then delete.
gcloud filestore instances update <filestore-name> --zone=us-east4-b --clear-deletion-protection
gcloud filestore instances delete <filestore-name> --zone=us-east4-b --quiet

# C) Delete the per-disk snapshots after restore-and-validate (saves ~$13/TB/mo)
gcloud compute snapshots list --filter="name~20260530-2354" --format='value(name)' | \
  xargs -r -n1 gcloud compute snapshots delete --quiet
```

---

## 6. Known risks / caveats

1. **terraform vs the API-recreated templates from the 2026-05 incident:** in the
   `phase2/recovery/` folder we recreated 8 templates out-of-band. Those templates
   will be DESTROYED in step 1c (along with everything else), so the terraform state
   on the fresh deploy starts clean. No reconciliation needed if you go through a
   full takedown.
2. **Filestore `deletion_protection`** is `true` in the blueprint — keep it. If
   `terraform destroy` complains it cannot delete the filestore, that's the protection
   working as intended. Don't disable it to "fix" the destroy; preserve the filestore
   instead.
3. **Shared reservation** must not be deleted. If terraform's destroy plan shows the
   `google_compute_reservation` resource being destroyed, remove it from state first:
   `terraform state rm <module>.google_compute_reservation.<name>`.
4. **JuiceFS tokens** are customer secrets — don't put them in IaC or any repo. Stage
   from a controlled location.
5. **`/mnt/disk` data** on the current compute nodes is lost on takedown. The new
   nodes come up with a fresh empty `/mnt/disk` (blueprint formats + mounts it
   automatically now).
6. **`controller-save` is zonal**, so it cannot be referenced in a *global* instance
   template (a lesson from the node-12 incident). It must be **attached at the
   instance level** as in step 2b. The blueprint does not include it in the controller
   template.
