# Nucleus a3-mega GPU-stack upgrade (570→580 / CUDA 12.8→13.0 / vLLM 0.19.1→0.24) — HANDOFF

> **Read this first, end-to-end.** It orients a fresh agent (Claude Desktop on the operator's
> MacBook) + the operator running a **paired session** — the agent proposes exact commands, the
> operator runs them on the authenticated Mac and pastes output back. Then follow **`RUNBOOK.md`**
> step-by-step and apply **`BLUEPRINT_CHANGES.md`** (already materialized as the two `*-580.yaml`
> files in this folder).
>
> This is an **in-place image upgrade of the 8-node Nucleus cluster only**. It is NOT a
> teardown/redeploy. The `../past_execution/` docs describe a *different, more destructive*
> operation (a full 32-node reservation re-split) — read them only for the hard-won GCP lessons
> (resize-request VMs can't be stopped; Filestore came up empty on a full destroy; SSD quota
> ceilings), **not** as the procedure to follow here.

---

## 1. What we're doing and why

The `live_video_chat` POC wants off **vLLM 0.19.1**. The wins that matter for our **bf16**
Qwen3-VL-32B video serving are the **non-FP8** ones: the Qwen3-VL deepstack `torch.compile`
**accuracy fix** (0.19.1 can silently drop multi-level vision features), **EVS** long-video token
pruning, **ViT CUDA-graph + Model-Runner-V2** (lower TTFT), and possibly retiring the ffmpeg
clip-normalize via per-request `mm_processor_kwargs`. (FP8 latency gains are a *future* lever —
they only pay off on the `-Instruct-FP8` checkpoint; ignore them for the go/no-go.)

vLLM 0.24 needs a modern GPU stack. Because Cluster-Toolkit is **image-based** (the driver is baked
into a Packer image; there is **no supported in-place driver upgrade**), "upgrade" means: **rebuild
the custom image with new pins, then recreate the 8 compute nodes.** We move to the **GCP-coherent
stable set** (what GCP validates together — not the newest of each):

| | From (now) | To (target) |
|---|---|---|
| Base image / OS | `ubuntu-accelerator-2204…nvidia-570` / Ubuntu 22.04 | `ubuntu-accelerator-2404…nvidia-580` / Ubuntu 24.04 |
| GPU driver | 570.211.01 (CUDA-12 gen) | **R580** (≥580.65.06 for CUDA 13) |
| CUDA toolkit | `cuda-toolkit-12-8` | **`cuda-toolkit-13-0`** |
| DCGM | `…-cuda12` | **`…-cuda13`** |
| Fabric Manager | fabricmanager-570 | **fabricmanager-580** (auto-follows the base image) |
| nvidia-container-toolkit | unpinned | **1.17.7-1, held** (1.17.8/1.17.9 known-broken) |
| PyTorch / vLLM | torch 2.10+cu128 / vLLM 0.19.1 | **torch 2.11+cu130 / vLLM 0.24** |
| Slurm-GCP ansible ref | `6.10.6` | **`6.10.6` (UNCHANGED — do not bump this window)** |

**The single biggest hazard is the GPUDirect-TCPXO fabric** (NCCL plugin + Fabric Manager). It is
driver-coupled and fails *silently*: if the FasTrak plugin doesn't load, NCCL falls back to slow TCP
**without crashing** — a "passing" job can be 10× slower. Proving the fast path is the make-or-break
gate (RUNBOOK Phase 3).

---

## 2. The mental model that keeps data safe (read twice)

Two data stores, two completely different fates:

- **`/home/ubuntu` = Filestore (NFS, 10 TB, `deletion_protection`).** It is **preserved for free**
  *because we never destroy* — an incremental image swap doesn't touch it. (In a full
  `gcluster destroy`, a fresh empty Filestore comes up and needs a multi-hour rsync migration — see
  `../past_execution`. We avoid that entirely by never destroying.) We still take a **backup** as a
  net, and we **grep every terraform plan** to assert the Filestore is not being replaced.

- **`/mnt/localssd` = Local SSD (ephemeral NVMe on the a3-megagpu-8g machine type).** It is
  **physically destroyed the instant a node is recreated** — and the upgrade *requires* recreating
  nodes. There is **no detach/re-attach** (that only worked on Polyhive because its `/mnt/disk` was a
  *persistent* disk; Nucleus has `a3mega_additional_disks: []` — no data disk). Also on
  `/mnt/localssd`: **Docker's data-root** and the **enroot cache** — both wiped on every recreate,
  **including on rollback**. Anything irreplaceable here must be **copied to GCS before recreate and
  restored after**; caches (HF models, docker images, enroot) are **rebuilt, not restored**.

**Because `/mnt/localssd` is lost on every recreate, RUNBOOK Phase 0 has two hard DECISION GATES you
must resolve before touching anything:**
- **Gate A — what's actually on `/mnt/localssd`?** Inventory all 8 nodes. Only caches → rebuild path
  (fast). Real datasets/checkpoints → per-node `tar | gsutil cp` backup path (adds ~1+ day).
- **Gate B — where do the vLLM/`moe` venvs + the Qwen model cache live?** `/home` → they survive.
  `/mnt/localssd` → rebuild them in Phase 5.

---

## 3. Guardrails (STRICT — never do these without explicit operator confirmation)

- ❌ **NEVER touch the Polyhive cluster.** Anything matching `phivea3m*`, `a3mega-base-polyhive*`,
  `polyhive-*`, or the Polyhive PSC address / bucket / Filestore is **out of scope**. Polyhive stays
  up holding its 24 reservation slots throughout.
- ❌ **NEVER modify or delete the reservation** `nvidia-h100-dkydfas6m486t` (32 slots = 24 Polyhive
  + 8 Nucleus). If any terraform plan shows `google_compute_reservation`, STOP.
- ❌ **NEVER run `gcluster destroy`.** This is an in-place update. There is no destroy step anywhere
  in this runbook.
- ❌ **NEVER `--auto-approve`.** Read every plan. Before any `cluster`-group apply, assert **zero**
  create/replace/destroy on: `google_filestore_instance`, its `random_id`, `google_storage_bucket`,
  `google_compute_network`/subnetwork, `private_service_access`, the controller, and the login —
  **asserting the terraform ACTION (`destroy`/`must be replaced`/`-/+`), not just a substring match**
  (see RUNBOOK §2.2 for the exact check). The ONLY intended change is the a3mega **nodeset instance
  template** (new image family).
- ❌ **NEVER disable Filestore `deletion_protection`.** (And do not rely on it — on a prior run it did
  *not* gate a destroy plan. The backup + plan-grep are the real safety net.)
- ❌ **NEVER bulk-rsync `/mnt/localssd` into `/home`.** Millions of tiny files melt the NFS server the
  whole cluster depends on (prior run measured ~3 GB/min) and collide across 8 nodes. Back up to GCS
  per-node with `tar`, or rebuild.
- ❌ **NEVER delete all 8 nodes at once.** Recreate in **waves of 2**, confirming the reservation
  `inUseCount` returns to 32 between waves (freed slots can be lost → stuck at 5/8 → outage).
- ❌ **NEVER delete images/snapshots/backups by wildcard family.** Delete by **exact name**, and only
  after the operator confirms post-upgrade sign-off. The old `slurm-a3mega-nucleus` image is the
  rollback target — keep it until fully confident.
- ⚠️ **On any unexpected output**, STOP, summarize, and wait for the operator. Do not improvise a
  workaround on live infra.

---

## 4. Constants

```
PROJECT            = poetic-avenue-438401-a7
ZONE               = us-east4-b
REGION             = us-east4
RESERVATION        = nvidia-h100-dkydfas6m486t          # 32 slots; NEVER modify
DEPLOYMENT         = a3mega-base-nucleus                # gcluster deployment_name
TF_BACKEND_BUCKET  = gs://nucleus-a3mega-cluster        # terraform state (do NOT delete)
SLURM_CLUSTER      = nucla3m
PARTITION          = a3mega                             # 8 static nodes, is_default
CONTROLLER         = nucla3m-controller
LOGIN              = nucla3m-login-001
NODES              = nucla3m-a3meganodeset-[0-7]
SYS_NET            = nucleus-a3mega-sys-net1 / subnet nucleus-a3mega-sys-subnet1
HOME (NFS)         = Filestore (HIGH_SCALE_SSD, 10 TB, /home, deletion_protection)
                     name has a random suffix -> discover: a3mega-base-nucleus-XXXXXXXX
LOCALSSD (node)    = /mnt/localssd  (EPHEMERAL local SSD; docker data-root + enroot cache live here)
OLD image family   = slurm-a3mega-nucleus        <-- rollback target; keep it
NEW image family   = slurm-a3mega-nucleus-580    <-- built by this upgrade
CONTROLLER-SAVE    = nucla3m-controller-save     (slurm state disk; snapshot it in Phase 0)
```

**Tooling** (on the operator's Mac, as in the 2026-05-31 run): the real `cluster-toolkit` git
checkout with the `./gcluster` binary (was v1.67.0) + Terraform (was 1.13.x), the `a3mega-base-nucleus/`
deployment working dir next to it, `gcloud` authed as an owner, **and Application Default Credentials**
(`gcloud auth application-default login` — ghpc/terraform validators need ADC separately from CLI auth).

---

## 5. The plan at a glance (detail + commands in `RUNBOOK.md`)

| Phase | Goal | Recreate nodes? | Rough time |
|---|---|---|---|
| **0** Pre-flight, backup, **inventory (Gates A/B)**, reserve 8 IPs + IAM | none | 0.5 day (Filestore backup runs async) |
| **1** Apply `*-580.yaml`, **build the new image only** (`build_script`+`slurm-build`) | none | 0.5 day (packer ~20–40 min) |
| **2** Update nodeset template, **canary-recreate 2 nodes** (wave 1) | 2 of 8 | ½ day |
| **3** **Validate canary GATE** (nvidia-smi 580, FM active, **NCCL busbw + FasTrak**, vLLM turn) | — | (within Phase 2 day) |
| **4** **Roll out** remaining 6 in waves of 2 | 6 of 8 | ½ day |
| **5** Rebuild `vllm-vlm-580`/`moe` envs (cu130), re-pull model cache, re-validate POC | — | 1 day |
| **6** Soak, optional FP8 A/B, cleanup after sign-off | — | ½ day |

**Headline: ≈4 engineer-days over ~1 calendar week.** Biggest swing: **Gate A** — if `/mnt/localssd`
holds irreplaceable data, add the per-node GCS backup/restore (~1+ day).

**Why a "canary" of 2 and not the `debug` partition:** `instance_image.family` is nodeset-wide and the
`debug` partition is CPU-only `n2-standard-2` (can't validate fabric/NCCL/vLLM). So the canary is
*temporal* — recreate 2 nodes onto 580, validate them fully while the other 6 keep running on 570,
then roll the rest. **Order is load-bearing: update the template BEFORE `POWER_DOWN_FORCE`**, or the
node comes back on the OLD image.

---

## 6. What's in this folder

| File | Purpose |
|---|---|
| **`HANDOFF.md`** | THIS FILE — read first: context, mental model, guardrails, constants. |
| **`RUNBOOK.md`** | Strict phase-by-phase commands, expected output, STOP conditions, rollback. |
| **`BLUEPRINT_CHANGES.md`** | The exact 7 edits (4 pins + 3 Polyhive ports) and their rationale. |
| **`a3mega-slurm-blueprint-nucleus-580.yaml`** | Ready-to-deploy upgraded blueprint (edits pre-applied). |
| **`a3mega-slurm-deployment-nucleus-580.yaml`** | Ready-to-deploy upgraded deployment (`final_image_family` → `-580`). |

Source of truth for versions/hazards: `../cluster_upgrade_guide.md`. GCP lessons (resize-request VMs,
empty-Filestore-on-destroy, SSD quota, SSH/OS-Login flip): `../past_execution/`.
