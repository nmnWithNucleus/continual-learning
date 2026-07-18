# RUNBOOK — Nucleus a3-mega in-place GPU-stack upgrade (paired session)

> **Read `HANDOFF.md` first.** This runbook is strict and paired: the agent proposes exact commands,
> the operator runs them on the authenticated Mac and pastes output back. **Never `--auto-approve`.
> Never `gcluster destroy`. Never touch Polyhive. Recreate nodes only in waves of 2.**
> On any output that doesn't match "Expected", **STOP** and summarize.

Set these in the operator's shell once:
```bash
export PROJECT=poetic-avenue-438401-a7 ZONE=us-east4-b REGION=us-east4
export RES=nvidia-h100-dkydfas6m486t
export DEPLOY=a3mega-base-nucleus
export CTL=nucla3m-controller
gcloud config set project $PROJECT
```

> **`-w` / `--auto-approve` conventions:** every `gcluster deploy` below uses `-w`
> (`--overwrite-deployment`), which regenerates the on-disk `a3mega-base-nucleus/` deployment dir.
> That is safe — Terraform **state** (in `gs://nucleus-a3mega-cluster`) is preserved — and necessary
> because the dir already exists. But it means **any hand-edit to the generated Terraform between runs
> is discarded**; if you ever `terraform state rm` or hand-patch a generated file, do not re-run `-w`
> afterward without redoing it. **`-w` is NOT `--auto-approve`** — you still review every plan.
> `--auto-approve` appears nowhere in this runbook by design.

---

## Phase 0 — Pre-flight, backups, INVENTORY (decision gates), IP/IAM prep

### 0.1 Verify access (both CLI auth AND ADC)
```bash
gcloud auth list --filter=status:ACTIVE --format='value(account)'
gcloud config get-value project                       # -> poetic-avenue-438401-a7
gcloud auth application-default print-access-token >/dev/null && echo "ADC OK"
ssh -o ConnectTimeout=10 $CTL 'hostname; sinfo -h -o "%P %a %D %t"'
./gcluster --version    # in the cluster-toolkit checkout; note it (was v1.67.0)
```
**Expected:** owner account; ADC OK; `nucla3m-controller`; `sinfo` shows partition `a3mega` with 8
nodes `idle`. **STOP if** ADC missing (`gcloud auth application-default login`) or SSH fails.

### 0.2 Reservation is healthy and full (Polyhive + Nucleus)
```bash
gcloud compute reservations describe $RES --zone=$ZONE \
  --format='value(specificReservation.count,specificReservation.inUseCount)'
```
**Expected:** `32  32` (24 Polyhive + 8 Nucleus). **STOP if** count ≠ 32.

### 0.3 Record current state for rollback
```bash
# Driver/CUDA on a node (via slurm)
ssh $CTL "srun -p a3mega -w nucla3m-a3meganodeset-0 -N1 --gres=gpu:8 --exclusive -t 2 \
  bash -lc 'nvidia-smi --query-gpu=driver_version,name --format=csv,noheader | head -1; nvidia-smi | grep CUDA'"
# Current image family + the EXACT resolved image name the nodes booted (rollback target)
gcloud compute images list --filter='family=slurm-a3mega-nucleus' \
  --format='table(name,family,creationTimestamp)'
# Slurm health + note any drained nodes / FM status (guide flagged node-7 FM 'failed')
ssh $CTL "sinfo -N -o '%.30n %.10t %.30E'"
ssh $CTL "srun -p a3mega -w nucla3m-a3meganodeset-7 -N1 --gres=gpu:8 --exclusive -t 2 \
  bash -lc 'systemctl is-active nvidia-fabricmanager; nvidia-smi -q | grep -iA2 fabric | head'"
```
**Record:** driver `570.x`, CUDA `12.8`, the old image name, and node-7 FM status. **Do not** try to
fix node-7 FM here — the 580 rebuild should clear it; re-check at the Phase 3 gate.

### 0.4 Snapshot the controller-save disk (cheap safety net)
```bash
gcloud compute disks snapshot nucla3m-controller-save --zone=$ZONE \
  --snapshot-names=pre-580-nucla3m-ctlsave-$(date +%Y%m%d)
```

### 0.5 Back up `/home` (Filestore) — the real net for the NFS home
```bash
# Discover the Nucleus Filestore instance (name has a random suffix)
FS=$(gcloud filestore instances list --format='value(name)' \
      --filter="name~a3mega-base-nucleus AND fileShares.name=nfsshare")
echo "Filestore = $FS"
gcloud filestore backups create pre-580-nucleus-$(date +%Y%m%d-%H%M) \
  --instance=$FS --instance-zone=$ZONE --file-share=nfsshare --region=$REGION
# May print "has not finished in 1800 seconds" — that's fine; it runs server-side. Verify READY:
gcloud filestore backups list --region=$REGION --filter='name~pre-580-nucleus' \
  --format='table(name.basename(),state,createTime)'
```
**Expected:** one backup, eventually `READY`. **Do not proceed to Phase 2 until it is `READY`.**

### 0.6 ⛳ DECISION GATE A + B — inventory `/mnt/localssd` and locate envs/model cache
```bash
ssh $CTL 'for N in 0 1 2 3 4 5 6 7; do
  echo "=== node-$N ==="; srun -p a3mega -w nucla3m-a3meganodeset-$N -N1 --gres=gpu:8 --exclusive -t 2 \
    bash -lc "df -h /mnt/localssd | tail -1; echo; du -sh /mnt/localssd/* 2>/dev/null | sort -rh | head -20"
done'
# Where do the serving envs + HF cache live? (survives only if under /home)
ssh $CTL 'ls -ld /home/ubuntu/*vllm* /home/ubuntu/*moe* /home/ubuntu/.hf-home 2>/dev/null; \
  srun -p a3mega -w nucla3m-a3meganodeset-0 -N1 --gres=gpu:8 --exclusive -t 2 \
    bash -lc "ls -ld /mnt/localssd/*vllm* /mnt/localssd/.hf-home /mnt/localssd/*/enroot 2>/dev/null"'
```
**DECIDE and record, per node:**
- **Gate A:** Is everything on `/mnt/localssd` a rebuildable cache (`docker/`, `*/enroot/`,
  `.hf-home`, model weights)? → **rebuild path**, skip 0.7. Any irreplaceable dataset/checkpoint/output?
  → **do 0.7** for those paths only.
- **Gate B:** Envs + model cache under `/home` → survive (build the new env alongside). Under
  `/mnt/localssd` → they'll be lost; rebuild in Phase 5.

**STOP and ask the operator** if anything is ambiguous. Do not recreate a node until Gate A is settled.

### 0.7 (Only if Gate A found irreplaceable node-local data) Back up to GCS per node
```bash
# One tar stream per node, straight to the terraform-adjacent bucket. NEVER rsync into /home.
DEST=gs://nucleus-a3mega-cluster/localssd-backup-$(date +%Y%m%d)
ssh $CTL 'for N in 0 1 2 3 4 5 6 7; do
  srun -p a3mega -w nucla3m-a3meganodeset-$N -N1 --gres=gpu:8 --exclusive -t 120 \
    bash -lc "tar -C /mnt/localssd -czf - <RELATIVE_PATHS_FROM_GATE_A> \
      | gcloud storage cp - '"$DEST"'/node-$N.tar.gz" &
done; wait'
```
Substitute `<RELATIVE_PATHS_FROM_GATE_A>` with only the irreplaceable dirs. Tiny-file datasets are
slow (~GB/min) — prefer `tar` (one object) over file-by-file copy. **Do not** create temp PDs unless
necessary; if you must, pre-check `SSD_TOTAL_GB` quota (a prior run hit the 126 TB ceiling).

### 0.8 Reserve 8 static IPs + grant IAM (prereq for the ported `pin-static-ip`)
The `-580` blueprint bakes in `pin-static-ip` (each worker rebinds nic0 to a reserved address
`n-node-N` on boot, so `ssh n-node-N` survives recreates). Without these prereqs the baked script is a
**safe no-op** (it logs "no reservation n-node-N; skip") — but set them up now so the benefit is live.
```bash
# 8 regional external addresses n-node-0 .. n-node-7 (PREMIUM tier, us-east4)
for N in 0 1 2 3 4 5 6 7; do
  gcloud compute addresses create n-node-$N --region=$REGION --network-tier=PREMIUM 2>/dev/null || true
done
gcloud compute addresses list --filter='name~n-node-' \
  --format='table(name,address,status,networkTier)'      # expect 8 RESERVED

# Grant the compute nodes' service account permission to rebind their own access-config.
SA=$(gcloud compute instances describe nucla3m-a3meganodeset-0 --zone=$ZONE \
      --format='value(serviceAccounts[0].email)')
echo "node SA = $SA"
gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:$SA" --role=roles/compute.networkAdmin --condition=None
```
**Expected:** 8 addresses `RESERVED`; IAM binding added. (Tighter than `networkAdmin` is possible —
`compute.addresses.use` + `compute.instances.{delete,add}AccessConfig` + `compute.instances.get` — but
`networkAdmin` is the simple path; the operator may scope down later.)

### 0.9 (Optional sanity) Diff our blueprint against current upstream
```bash
# Confirm upstream a3-mega still targets 2404/nvidia-580/CUDA-13 (informational).
# git -C <cluster-toolkit> pull; diff examples/machine-learning/a3-megagpu-8g/* against our -580 yaml.
```

---

## Phase 1 — Apply blueprint changes and BUILD THE NEW IMAGE ONLY

### 1.1 Stage the upgraded YAMLs and confirm the diff
Copy `a3mega-slurm-blueprint-nucleus-580.yaml` and `a3mega-slurm-deployment-nucleus-580.yaml` next to
`./gcluster` (or pass full paths). Confirm they differ from the live YAMLs by exactly the 7 intended
changes (see `BLUEPRINT_CHANGES.md`):
```bash
diff a3mega-slurm-blueprint-nucleus.yaml a3mega-slurm-blueprint-nucleus-580.yaml
diff a3mega-slurm-deployment-nucleus.yaml a3mega-slurm-deployment-nucleus-580.yaml
```
**Expected:** 4 pin changes (`nvidia-580`, `cuda-toolkit-13-0`, `dcgm-cuda13`, container-toolkit
pin+hold) + 3 added blocks (unattended blacklist, `pin-static-ip`, RxDM ulimit) in the blueprint;
`final_image_family: …-580` in the deployment. **STOP if** anything else differs.

### 1.2 Build the image (build groups only — NO node changes yet)
```bash
# Regenerates the deployment dir from the new YAMLs, then applies ONLY the image-build groups.
# Review the plan; DO NOT pass --auto-approve.
./gcluster deploy \
  -d ./a3mega-slurm-deployment-nucleus-580.yaml \
     ./a3mega-slurm-blueprint-nucleus-580.yaml \
  --only build_script,slurm-build -w 2>&1 | tee nucleus-580-imagebuild.log
```
**Expected:** the `slurm-build` (packer) group runs ~20–40 min and produces a new image in family
`slurm-a3mega-nucleus-580`. The `cluster` group is **not** applied — no node is touched. **STOP if** the
plan wants to change anything in the `cluster` group here, or the packer build errors on a DKMS module.

### 1.3 Verify the new image and that the old one still exists (rollback target)
```bash
gcloud compute images list --filter='family=slurm-a3mega-nucleus-580' \
  --format='table(name,family,creationTimestamp)'          # -> exactly one new image
gcloud compute images list --filter='family=slurm-a3mega-nucleus' \
  --format='table(name,family,creationTimestamp)'          # -> old image STILL present
```
**Record** the new image name. **STOP if** the old family image is gone.

---

## Phase 2 — Update the nodeset template, then canary-recreate 2 nodes

### 2.1 Drain the 2 canary nodes (leave 6 serving on 570)
```bash
ssh $CTL "sudo scontrol update nodename=nucla3m-a3meganodeset-[0-1] state=DRAIN reason='580-canary'"
ssh $CTL "squeue -w nucla3m-a3meganodeset-0,nucla3m-a3meganodeset-1 -t RUNNING"   # wait until empty
```

### 2.2 Update the instance template to the new image family (review the plan!)
```bash
./gcluster deploy \
  -d ./a3mega-slurm-deployment-nucleus-580.yaml \
     ./a3mega-slurm-blueprint-nucleus-580.yaml \
  --only cluster -w 2>&1 | tee nucleus-580-cluster-plan.log
```
**Before approving, assert the plan changes ONLY the nodeset template — with an ACTION-based check,
not a substring match.** (A bare `grep filestore|random_id` matches many benign read/no-op lines and
buries the real signal; per `../past_execution` Incident #5 a Filestore **+** bucket *replace* is what
nearly wiped `/home` and `/gcs`, and `deletion_protection` did NOT appear in that plan.)
```bash
# (1) The cluster group must destroy/replace NOTHING but the nodeset template. Read the summary + actions:
grep -E 'Plan:|to destroy|will be destroyed|must be replaced|-/\+' nucleus-580-cluster-plan.log
# (2) Assert NO destroy/replace on the resources that hold /home, /gcs, the network, controller, login:
grep -nE 'google_filestore_instance|module\.homefs|module\.data-bucket|google_storage_bucket|random_id|google_compute_network|private_service_access|slurm_controller|slurm_login' nucleus-580-cluster-plan.log \
  | grep -iE 'destroy|replace|-/\+'
```
**Expected:** (1) shows `Plan: … 0 to destroy` and the ONLY replacement is the a3mega **nodeset
instance template** (new image family); (2) prints **nothing**. **STOP and do NOT approve** if (1)
shows anything `to destroy` / `must be replaced` / `-/+` other than the nodeset template, or (2) prints
ANY line. These must NEVER be replaced/destroyed:
`module.homefs.google_filestore_instance.filestore_instance`, `module.homefs.random_id.*`,
`module.data-bucket.google_storage_bucket.bucket`, `module.data-bucket.random_id.*`. (If they appear,
terraform state is drifting — stop and diagnose; do not apply.) Updating the template does **not**
delete running VMs.

### 2.3 Recreate the 2 canary nodes (reservation-safe)
```bash
ssh $CTL "sudo scontrol update nodename=nucla3m-a3meganodeset-[0-1] state=POWER_DOWN_FORCE"
# Wait for the 2 VMs to be deleted and the reservation to reflect it
until [ "$(gcloud compute reservations describe $RES --zone=$ZONE \
  --format='value(specificReservation.inUseCount)')" = "30" ]; do sleep 15; done
ssh $CTL "sudo scontrol update nodename=nucla3m-a3meganodeset-[0-1] state=POWER_UP"
ssh $CTL "sudo tail -f /var/log/slurm/resume.log"    # watch them come back (Ctrl-C when idle)
```
**Expected:** `inUseCount` dips to 30, then returns to 32 as the 2 nodes boot the 580 image.
**STOP if** it does not return to 32 within the resume timeout (reservation slot lost — do not force
more deletes; investigate).

---

## Phase 3 — ⛳ VALIDATE THE CANARY (make-or-break gate; do not roll out until all pass)

Run on `nucla3m-a3meganodeset-0` (and 1):
```bash
ssh $CTL 'srun -p a3mega -w nucla3m-a3meganodeset-0 -N1 --gres=gpu:8 --exclusive -t 5 bash -lc "
  echo === driver/cuda ===; nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1; nvidia-smi | grep CUDA
  echo === fabric manager ===; systemctl is-active nvidia-fabricmanager; nvidia-smi -q | grep -iA2 fabric | head
  echo === dcgm ===; dcgmi discovery -l | tail -5
  echo === aperture ===; ls /dev/aperture_devices | wc -l
"'
```
- [ ] **nvidia-smi → Driver 580.x, CUDA 13.0**, 8× H100 visible.
- [ ] **`nvidia-fabricmanager` → active** and `nvidia-smi -q` fabric healthy. (This clears the prior
      node-7 FM `failed`; the single-node TP=8 vLLM path needs NVSwitch/FM.) **Gate — must be active.**
- [ ] **`dcgmi discovery -l` → 8 GPUs + NVSwitch.**
- [ ] **`/dev/aperture_devices` count = 8** (blueprint's aperture safety-net service works).
- [ ] **A GPU container actually starts** — proves the pinned `nvidia-container-toolkit 1.17.7-1` works.
      The `uv venv` vLLM smoke below does NOT exercise the container runtime, and 1.17.7-1 has isolated
      segfault reports on other distros, so verify on ours (docker data-root is fresh/empty on the
      recreated node, so this also re-pulls):
      `srun -p a3mega -w nucla3m-a3meganodeset-0 -N1 --gres=gpu:8 --exclusive -t 8 docker run --rm --gpus all nvcr.io/nvidia/pytorch:24.07-py3 nvidia-smi -L`
      → lists 8 GPUs. (Substitute the container image your jobs actually use.)

**NCCL fast-path gate (the insidious one):** run the 2-node all-reduce (nodes 0,1). Reuse the NCCL
scripts under `nucla3m-controller:/home/ubuntu/.post-destroy-validation-052926/` (or the recipe in
`../past_execution/RUNBOOK.md` §5.3). Then:
```bash
ssh $CTL "grep -E 'size.*MB.*busbw|FasTrak' /home/ubuntu/nccl_trace.*.0.log | head"
```
- [ ] **busbw > ~160 GBps** on the 2-node run (Nucleus reference ~305 GB/s at 1 GB) — **make-or-break**.
- [ ] **`NET/FasTrak plugin initialized`** appears — proves you are NOT on the silent slow TCP fallback.
      (If absent: the FasTrak plugin/RxDM path is broken — STOP; a "passing" job here is 10× slow.)
- [ ] Single-node **TP=8 NCCL init does not stall** (the single-node RxDM-timeout caveat).

**vLLM smoke turn** (minimal env on the canary; full rebuild is Phase 5):
```bash
ssh $CTL 'srun -p a3mega -w nucla3m-a3meganodeset-0 -N1 --gres=gpu:8 --exclusive -t 30 bash -lc "
  mkdir -p /mnt/localssd/tmp && uv venv /mnt/localssd/tmp/vllm580 && source /mnt/localssd/tmp/vllm580/bin/activate
  uv pip install vllm==0.24.0 --torch-backend=auto && uv pip install \"transformers>=4.57\" qwen-vl-utils
  python -c \"import torch,vllm; print(torch.__version__, torch.version.cuda, vllm.__version__)\"
  deactivate; rm -rf /mnt/localssd/tmp/vllm580   # throwaway; the real env is built on /home in Phase 5
"'
```
- [ ] Prints **torch 2.11.x / cu130 / vllm 0.24.x**. Then start `Qwen/Qwen3-VL-32B-Instruct` TP=8 and
      serve one **grounded video turn**; TTFT sane.

**If ANY gate fails → do NOT roll out. Go to Appendix R (rollback the 2 canary nodes).**
**If all pass:** return the canary to service and continue:
```bash
ssh $CTL "sudo scontrol update nodename=nucla3m-a3meganodeset-[0-1] state=RESUME"
```

---

## Phase 4 — Roll out the remaining 6 (waves of 2, reservation-safe)

For each pair P in `[2-3] [4-5] [6-7]`:
```bash
ssh $CTL "sudo scontrol update nodename=nucla3m-a3meganodeset-[P] state=DRAIN reason='580-rollout'"
ssh $CTL "squeue -w <the two nodes> -t RUNNING"          # wait empty
ssh $CTL "sudo scontrol update nodename=nucla3m-a3meganodeset-[P] state=POWER_DOWN_FORCE"
until [ "$(gcloud compute reservations describe $RES --zone=$ZONE \
  --format='value(specificReservation.inUseCount)')" = "30" ]; do sleep 15; done
ssh $CTL "sudo scontrol update nodename=nucla3m-a3meganodeset-[P] state=POWER_UP"
# wait for both back + inUseCount == 32, then quick check:
ssh $CTL "srun -p a3mega -w <one node of P> -N1 --gres=gpu:8 --exclusive -t 3 \
  bash -lc 'nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1; ls /dev/aperture_devices | wc -l; systemctl is-active nvidia-fabricmanager'"
ssh $CTL "sudo scontrol update nodename=nucla3m-a3meganodeset-[P] state=RESUME"
```
**Between waves: confirm `inUseCount` is back to 32.** Never open a gap larger than the wave.
**After all 8 are on 580:**
```bash
ssh $CTL "sinfo -N -o '%.30n %.10t %.20E'"    # 8 idle, REASON empty
gcloud compute reservations describe $RES --zone=$ZONE \
  --format='value(specificReservation.count,specificReservation.inUseCount)'   # 32  32
```

---

## Phase 5 — Rebuild Python envs (cu130) + re-validate the POC

Keep the OLD `vllm-vlm`/`moe` envs intact until the new ones are proven (put new ones on **/home** so
they survive, per the guide).
```bash
# Serving env
uv venv ~/vllm-vlm-580 && source ~/vllm-vlm-580/bin/activate
uv pip install vllm==0.24.0 --torch-backend=auto        # torch 2.11 + cu130 on the 580 driver
uv pip install "transformers>=4.57" qwen-vl-utils accelerate
# vLLM bundles FlashAttention-3 — do NOT install standalone flash-attn.

# ASR env
uv venv ~/moe-580 && source ~/moe-580/bin/activate
# faster-whisper/ctranslate2: confirm a cu13-compatible build; CPU mode is a safe fallback.
```
- Re-pull the model cache to `/mnt/localssd/.hf-home` on the nodes (fresh local SSD → empty). Restore
  any Phase-0.7 GCS backup to the nodes that need it.
- Re-validate: `server/serve.sh`, then `live_video_chat` end-to-end. Test per-request
  `mm_processor_kwargs` `max_pixels` → decide whether the ffmpeg clip-normalize can be dropped.
- Sanity: docker images and enroot cache were wiped on recreate — re-pull any container images jobs
  depend on before declaring done.

---

## Phase 6 — Soak, optional FP8, cleanup (only after operator sign-off)

- Run a real workload; watch DCGM/ops-agent metrics for a soak period.
- **Optional FP8 A/B** (Appendix F): swap to `Qwen/Qwen3-VL-32B-Instruct-FP8`; A/B latency + vision.
- **Cleanup — by exact name, only after sign-off:** delete the Phase-0 Filestore backup, the
  controller-save snapshot, any Phase-0.7 GCS backup, and finally the **old** `slurm-a3mega-nucleus`
  image (delete by exact name — **never** by wildcard family; never run the `past_execution` §1d
  family-wildcard image delete on Nucleus). Keep the old image until fully confident.

---

## Appendix R — Rollback

Rollback is clean for the image/driver (old family untouched, old cu128 venvs on `/home` survive) but
**`/mnt/localssd` is already destroyed** — rollback is bootable fast, serving-ready only after
re-populating local SSD (re-pull caches / restore Phase-0.7 backup).

1. **Precondition:** confirm the old image still exists —
   `gcloud compute images list --filter='family=slurm-a3mega-nucleus'` returns ≥1 image (never repoint
   to an empty family). Then repoint the nodeset to the old family: set
   `final_image_family: slurm-a3mega-nucleus` (edit the deployment) and
   `./gcluster deploy … --only cluster -w` — **review the plan** (same ACTION-based stateful-resource
   guard as 2.2); the only change should be the nodeset template reverting to the old image.
2. Recreate the affected nodes **in waves of 2** (same POWER_DOWN_FORCE → confirm 30 → POWER_UP →
   confirm 32 loop as Phase 4).
3. Reactivate the old `vllm-vlm`/`moe` envs. Restore `/mnt/localssd` data if needed.

## Appendix F — FP8 follow-on (optional, post-upgrade)
`Qwen/Qwen3-VL-32B-Instruct-FP8` (~35 GB vs 66.7 GB bf16; ~2× memory headroom, ~1.6× throughput,
near-bf16 quality, native on Hopper). Swap the model id, A/B TTFT/E2E latency + a grounded-video
vision-quality check against bf16 before adopting.

## Appendix S — SSH note (why we DON'T need the OS-Login flip)
The `past_execution` runs needed an OS-Login-off + push-key + seed-`/home/ubuntu` dance because a full
redeploy brings up a **fresh empty Filestore** and a new controller. **We never destroy**, so the
controller, login, `/home`, and existing SSH keys are untouched — plain `ssh ubuntu@…` keeps working
throughout. Compute nodes get fresh host keys on recreate; if `pin-static-ip` is active you may need to
clear stale `known_hosts` entries for `n-node-N` addresses. (If you ever *do* land on an empty `/home`
or publickey denial, the recipe is `../past_execution/POST_DEPLOY_SSH_BOOTSTRAP.md`.)
