# BLUEPRINT_CHANGES — what changed from `a3mega-slurm-blueprint-nucleus.yaml` → `-580`

The upgraded `a3mega-slurm-blueprint-nucleus-580.yaml` and `a3mega-slurm-deployment-nucleus-580.yaml`
in this folder have all edits pre-applied and are ready to deploy. This file explains **each** change
so a reviewer can confirm intent (and re-derive it if the base blueprint ever moves). Verify with:
```bash
diff a3mega-slurm-blueprint-nucleus.yaml   nucleus-580-upgrade/a3mega-slurm-blueprint-nucleus-580.yaml
diff a3mega-slurm-deployment-nucleus.yaml  nucleus-580-upgrade/a3mega-slurm-deployment-nucleus-580.yaml
```
There are **7 changes total: 4 version pins + 3 Polyhive ports.** Nothing else changed. The
blueprint stays byte-identical everywhere else (Slurm-GCP ref `6.10.6`, gVNIC v1.4.3, gpu-test epilog
still disabled, `additional_disks: []`, the no-op `mount_data_disk.sh`, aperture safety-net, etc.).

---

## A. Version pins (the actual upgrade)

| # | Where | From | To | Why |
|---|---|---|---|---|
| 1 | `vars.source_image_family` | `ubuntu-accelerator-2204-amd64-with-nvidia-570` | `ubuntu-accelerator-2404-amd64-with-nvidia-580` | Base image = Ubuntu 24.04 + **driver R580** (baked in; Fabric-Manager-580 auto-follows). This one line is the driver upgrade. |
| 2 | `configure_gpu_monitoring.yml` → `nvidia_packages` | `cuda-toolkit-12-8` | `cuda-toolkit-13-0` | CUDA 13.0 (min driver ≥580.65.06 ✓). |
| 3 | same list | `datacenter-gpu-manager-4-cuda12` | `datacenter-gpu-manager-4-cuda13` | DCGM must match the CUDA-13 stack. |
| 4 | `install-nvidia.sh` | `apt-get install -y nvidia-container-toolkit` (unpinned) | install **`=1.17.7-1`** for all 4 packages + `apt-mark hold` | Pin the container toolkit; **1.17.8/1.17.9 are known-broken**. Upstream uses an APT Pin-Priority block; install-exact + hold is the equivalent and matches this blueprint's existing `apt-mark hold` idiom. |

**Deployment file (change 4b):** `final_image_family: slurm-a3mega-nucleus` → **`slurm-a3mega-nucleus-580`**.
A distinct family means the old `slurm-a3mega-nucleus` image stays bootable as the rollback target,
and packer writes the new build into `-580`. (Note: `final_image_family` drives **both** the packer
output **and** the nodeset boot image — that's why "build only" means *run the build groups but don't
apply `cluster`*, not "rename the family.")

**Build-time checks to enforce (in RUNBOOK Phase 1.2):**
- The exact container-toolkit version string exists: `apt-cache madison nvidia-container-toolkit`
  (adjust `NCT_VERSION` if the repo names it differently) — a wrong string fails the build loudly
  (good; no silent bad image).
- DKMS modules (`dmabuf-import-helper`, `gve-dkms` v1.4.3) actually compile against the new 24.04/580
  kernel. If gVNIC DKMS fails on 24.04, that's the one spot that may need a newer `gve-dkms` — surface
  it, don't paper over it.

**Deliberately NOT changed (decoupled):**
- **Slurm-GCP `-C 6.10.6`** stays. Bumping to 6.12.x is a *separate* change: our two custom controller
  fixes (`bootstrap_controller_environment.sh` gcsfuse replay + `/home/ubuntu` pre-create) are written
  against 6.10 behavior and need their own canary. Do not bundle it here.
- **PyTorch/vLLM** are not in the blueprint — they're per-env (`uv pip install vllm==0.24.0
  --torch-backend=auto` → torch 2.11 + cu130). Rebuilt in RUNBOOK Phase 5.

---

## B. Ported from the Polyhive blueprint (3 blocks, verbatim except one rename)

Polyhive's header claims it "differs from Nucleus only in blueprint_name" — that is **stale**. A
comment-stripped diff shows Polyhive carries three real, beneficial additions Nucleus lacked. All are
low-risk and directly relevant to a recreate-heavy driver upgrade, so they're ported.

### 5. Unattended-upgrades NVIDIA/kernel blacklist  *(trivial, no prereq)*
A second `type: data` runner writing `/etc/apt/apt.conf.d/51-block-nvidia-kernel-unattended` that
blacklists `nvidia-*`, `libnvidia-*`, `linux-image/modules/headers-*` from unattended-upgrades.
Nucleus already `apt-mark hold`s the NVIDIA *userspace* packages but **not the kernel** — a kernel
unattended-upgrade could still rebuild/break the DKMS gVNIC/dmabuf/aperture modules and trip the exact
"Driver/library version mismatch" hazard the upgrade guide §5 warns about. Defense-in-depth.

### 6. `pin-static-ip` systemd oneshot + timer  *(medium; prereqs in RUNBOOK 0.8)*
Four runners in `a3mega_startup` (script + `.service` + `.timer` + enabler). On boot each worker
rebinds its nic0 external IP to a reserved address **`n-node-<N>`** (renamed from Polyhive's
`p-node-<N>`), so the node→IP mapping and `ssh n-node-N` survive every `POWER_DOWN_FORCE` recreate.
Valuable **because this upgrade recreates every node** (otherwise all 8 churn ephemeral IPs / known_hosts
through canary + rollout). **Prereqs:** reserve 8 addresses `n-node-0..7` in `us-east4` + grant the node
service account `compute.networkAdmin` (RUNBOOK 0.8). Without the prereqs the script is a **safe no-op**
(logs "no reservation n-node-N; skip" and exits 0), so baking it into the image is harmless either way.
The only edit to Polyhive's block was `p-node-` → `n-node-` (and the jitter comment 24→8).

### 7. RxDM ulimit fix  *(low; wiring already identical on Nucleus)*
In `stage_scripts.sh`, right after the `rxdm` prolog script is `curl`'d, an idempotent `awk` patch
injects `--ulimit nofile=65535:65535 --ulimit nproc=65535:65535 --ulimit memlock=-1:-1` into the RxDM
docker invocation. Fixes GPUDirect-TCPXO bug NVIDIA/Megatron-LM#4660 ("Buffer registration request does
not have a valid fd set") that bites the **multi-node** TCPXO path under load — exactly what the guide's
own 2-node NCCL validation exercises. `enable_external_prolog_epilog` is already on for Nucleus, so the
wiring is unchanged.

**Not ported (intentional):** the `/mnt/disk` persistent data disk / `a3mega_additional_disks` — that is
Polyhive's design; Nucleus deliberately uses ephemeral Local SSD only. Do **not** add a data disk to
Nucleus. The existing no-op `mount_data_disk.sh` (keys on `google-phive-data-disk`, absent on Nucleus)
is left as-is.
