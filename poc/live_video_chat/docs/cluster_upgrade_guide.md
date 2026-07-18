# Cluster GPU-Stack Upgrade Guide — GCP a3-mega (driver / CUDA / vLLM / torch)

> **Read this end-to-end before touching the cluster.** It is written so an engineer *or an
> agent* can plan and execute the upgrade of our GPU software stack with full context. It
> covers the current state, the coherent target stack, the upgrade *mechanism* (Cluster
> Toolkit is image-based — you do **not** apt-upgrade a driver in place), the single biggest
> hazard (the GPUDirect-TCPXO / NCCL / Fabric-Manager fabric), a step-by-step canary→rollout
> plan, validation, and rollback.
>
> **Scope.** This is cluster infrastructure, motivated by the `live_video_chat` POC's desire
> to move off vLLM 0.19.1, but it affects ALL workloads on the cluster. Treat it as a planned
> maintenance, not a hot change. Last updated 2026-06-30. Every version/claim is cited.

---

## 0. The one-paragraph answer to "is moving everything to latest stable safe?"

**Mostly yes — but pin to the *GCP-coherent* stable set, not the bleeding edge of each
component.** The good news: **GCP's *current* Cluster-Toolkit a3-mega blueprint already targets
NVIDIA driver R580 + CUDA 13.0 on Ubuntu 24.04** — we are simply on the *older* (Ubuntu-22.04 /
driver-570) image. So "upgrade" mostly means **catching up to the current official blueprint**,
which is low-risk by construction. The traps are in going *past* that: vLLM's `cu129` wheel is
broken (mislinks CUDA-13), `cu128` is being deprecated, torch 2.12 is off vLLM's pin, and
flash-attn's CUDA-13 wheels lag (irrelevant for us — vLLM bundles its own FlashAttention-3).
The **only genuinely dangerous part** is the a3-mega high-bandwidth fabric (GPUDirect-TCPXO +
NCCL plugin + Fabric Manager), which is **driver-version-coupled** and must be re-validated.

**Recommendation:** upgrade to the **GCP-coherent stable set** below, on a **canary first**, with
the **fabric (NCCL bandwidth) validated** before rolling out. Do not chase absolute-latest of
each package.

---

## 1. Current state (measured on `nucla3m-a3meganodeset-7`, 2026-06-30)

| Component | Current value |
|---|---|
| Machine type | **a3-megagpu-8g** (8× NVIDIA **H100 80GB SXM**, compute cap 9.0, NVSwitch) |
| GPU driver | **570.211.01** (R570 Production Branch, CUDA-12 generation) |
| CUDA runtime (driver) | **12.8** |
| CUDA toolkit installed | `cuda-toolkit-12-8` (12.8.2) |
| OS / kernel | **Ubuntu 22.04.5 LTS** / `6.8.0-1052-gcp` |
| Fabric Manager | `nvidia-fabricmanager-570` (⚠️ `systemctl is-active` returned **failed** — investigate; NVLink TP=8 currently works, so verify whether FM is actually required/running here) |
| DCGM | `datacenter-gpu-manager-4` 4.5.3 (cuda12 variant) |
| High-BW fabric | **GPUDirect-TCPXO** (`/var/lib/tcpxo` present) — a3-mega's FasTrak networking |
| Scheduler | **SLURM**, partition `a3mega` (8 nodes), `debug` (4 nodes) |
| Provisioning | **Google Cluster Toolkit** blueprint (image-based, Packer) |
| Python env `vllm-vlm` | torch **2.10.0+cu128**, **vLLM 0.19.1**, transformers 5.12.1 |
| Python env `moe` | torch **2.8.0+cu128**, transformers 4.57.6, faster-whisper |

---

## 2. Target state — the GCP-coherent stable stack (mid-2026)

| Component | Target | Why / source |
|---|---|---|
| Base image | **`ubuntu-accelerator-2404-amd64-with-nvidia-580-vYYYYMMDD`** (project `ubuntu-os-accelerator-images`) | This is what the **current** cluster-toolkit a3-mega blueprint pins (e.g. `…-nvidia-580-v20260522`). Driver is baked into this image. |
| GPU driver | **R580 LTSB** — GCP-recommended for a3-mega/H100, min **580.95.05**, latest **580.173.02** (EOL ~Jun 2028) | docs.cloud.google.com/compute/docs/gpus/install-drivers-gpu; NVIDIA `releases.json` |
| CUDA toolkit | **13.0** (`cuda-toolkit-13-0`) | cluster-toolkit `install_cuda_and_dcgm.yml`; CUDA 13.0 min driver ≥580.65.06 ✓ |
| Fabric Manager | **`nvidia-fabricmanager-580`** — MUST match the driver branch exactly | NVIDIA FM guide: FM aborts at init if driver stack mismatches; newer FM cannot run on older driver |
| DCGM | datacenter-gpu-manager-4 (**cuda13** variant) | cluster-toolkit playbook |
| nvidia-container-toolkit | **1.17.7-1** (apt-pinned + held) | cluster-toolkit `hold-nvidia-packages.yml` |
| GPUDirect-TCPXO NCCL plugin | **`nccl-plugin-gpudirecttcpx-dev:v1.0.15`** (bundles **NCCL 2.28**) | container-engine-accelerators TCPXO release notes (latest 2026-01-09) |
| RxDM sidecar (multi-node only) | **`tcpgpudmarxd-dev:v1.0.21`** (paired with plugin v1.0.15) | same; not needed for single-node jobs |
| Slurm | git ref **6.12.1** (cluster-toolkit) | blueprint `build_slurm_from_git_ref` |
| PyTorch | **2.11.0 + cu130** (vLLM's pin) | vLLM pins torch 2.11; cu130 is torch's stable default since 2.11 |
| vLLM | **0.24.0** (cu130 wheel) via `uv pip install vllm --torch-backend=auto` | latest stable; **do NOT use cu129 (broken) or pin cu128 (deprecated)** |
| transformers | latest (≥4.57; CUDA-agnostic) | inherits torch's CUDA |
| flash-attn | **none needed** — vLLM bundles `vllm.vllm_flash_attn` (FA3 on H100) | standalone flash-attn cu13 wheels lag; not required for vLLM |

> **Coherence note:** torch **2.12** exists but is *off* vLLM's 2.11 pin; CUDA **13.3** / driver
> **R610** exist but are newer than what GCP ships for a3-mega. Land on **580 / CUDA 13.0 /
> torch 2.11 / vLLM 0.24-cu130** — the set GCP actually validates together — not the newest of each.

---

## 3. Why upgrade (what 0.19.1 costs us) — and what's FP8-only

Detailed evidence is in the POC HANDOFF / the #3 research; summary of what moving to **vLLM ≥ 0.23/0.24** buys for our Qwen3-VL-32B video serving:

- **[correctness] Qwen3-VL deepstack `torch.compile` accuracy fix** (PR #43617, v0.23) — on pre-0.23 (us) the compiled graph can silently skip multi-level vision features → modestly degraded vision accuracy. **Applies to us regardless of FP8.**
- **[multimodal] EVS long-video token pruning** (#44205, v0.23) — serve longer clips in the same budget. Applies regardless of FP8.
- **[multimodal] per-request `mm_processor_kwargs`** fixes — *may* let us retire the ffmpeg clip-normalization (re-test that video `max_pixels` is honored). Applies regardless of FP8.
- **[perf] ViT CUDA-graph for Qwen3-VL video, Model-Runner-V2 default, FlashAttention-4 prefill** — lower TTFT on the video path. Applies regardless of FP8.
- **[quant — FP8 ONLY] batch-invariant CUTLASS FP8 −29% E2E latency / +13% TTFT, fp8-KV-cache maturation.** **These only pay off if we run the model in FP8** (see §3a) — we currently run **bf16**, so these rows are *conditional*, not free.

**Net:** the upgrade is worth it for the **non-FP8** reasons alone (accuracy fix + EVS + ViT-CG + MRv2 + possibly dropping ffmpeg). FP8 then becomes an easy follow-on lever.

### 3a. Note on FP8 (answers "we're not using FP8, so is that analysis useless?")
Correct — **we serve `Qwen/Qwen3-VL-32B-Instruct` in bf16** (66.7 GB over TP=8), so the FP8
*latency* gains don't apply *today*. They become relevant only if we switch to the **`…-Instruct-FP8`**
checkpoint (≈35 GB; ~2× memory headroom, ~1.6× throughput, near-bf16 quality, native on H100/Hopper).
After the upgrade, FP8 is a low-effort experiment (swap the model id; A/B latency + vision quality).
Until then, ignore the FP8 rows when weighing the upgrade.

---

## 4. The upgrade MECHANISM — Cluster Toolkit is image-based (do not apt-upgrade in place)

The a3-mega Slurm cluster is provisioned from a **Packer-built custom image**; the **driver is
pre-baked into the base image**, and CUDA/DCGM/container-toolkit are layered by Ansible during the
image build, then **apt-held**. There is **no supported in-place driver upgrade.**

**Where versions are pinned (in the blueprint):**
- **Driver** → the `vars.base_image.image` string (e.g. `ubuntu-accelerator-2404-amd64-with-nvidia-580-v20260522`).
- **CUDA** → `cuda-toolkit-13-0` in `install_cuda_and_dcgm.yml`.
- **nvidia-container-toolkit** → `1.17.7-1` in `hold-nvidia-packages.yml` (apt-pin + `apt-mark hold`).
- **Slurm** → `build_slurm_from_git_ref: 6.12.1`.
- **Image family** → `final_image_family: slurm-a3mega` (deployment vars) → nodes boot the built image.

**To upgrade = edit those pins and rebuild the image, then recreate nodes:**
```bash
# Full deploy: rebuilds the slurm-a3mega image (slurm-build group) AND the cluster.
./gcluster deploy -d a3mega-slurm-deployment.yaml a3mega-slurm-blueprint.yaml

# Cluster/infra only (no image rebuild) — for iterating on cluster config:
./gcluster deploy -d a3mega-slurm-deployment.yaml a3mega-slurm-blueprint.yaml --only primary,cluster -w
```
Requires Cluster Toolkit **v1.62.0+**. (Refs: cluster-toolkit `examples/machine-learning/a3-megagpu-8g/`,
`modules/packer/custom-image`, deploy guide.)

> 📎 **Action: share the blueprint YAML.** The exact pins above must be read/edited from *our*
> `a3mega-slurm-blueprint.yaml` + `a3mega-slurm-deployment.yaml`. Provide them and this guide's
> §6 becomes a concrete diff.

---

## 5. ⚠️ The #1 hazard: the GPUDirect-TCPXO fabric (NCCL + Fabric Manager)

a3-mega's ~1,800 Gbps GPU networking is **GPUDirect-TCPXO (FasTrak)**, and it is the most likely
thing an upgrade breaks. Two coupled pieces:

1. **Fabric Manager ↔ driver (exact match, required for NVSwitch).** H100 SXM uses Gen-3 NVSwitch;
   Fabric Manager configures the NVLink/NVSwitch memory fabric and **must be the same version as the
   driver** — it aborts at init on mismatch (older FM can run on a newer driver, never the reverse).
   So driver 580 ⇒ `nvidia-fabricmanager-580`. **Even single-node TP=8 NVLink depends on this.**
   (Our current node shows FM `failed` — investigate before *and* after; confirm FM-580 is `active`
   on the new image.)
2. **NCCL plugin + RxDM (multi-node).** The TCPXO NCCL net plugin is installed to
   `/var/lib/tcpxo/lib64/` by the Slurm **prolog** (`receive-data-path-manager-mega`); the **RxDM**
   sidecar runs alongside multi-node jobs. Target the plugin/RxDM pair **v1.0.15 / v1.0.21 (NCCL
   2.28)**, and **upgrade the NCCL plugin installer *before* the RxDM/tcpxo-daemon** (the release
   notes require that order).
   - ⚠️ **Silent slow-path fallback (the insidious failure mode):** if the FasTrak plugin fails to
     load (wrong path / ABI / driver skew), NCCL does **not** crash — it silently falls back to the
     internal TCP `Socket` transport and runs *slow*. So a "successful" job after an upgrade can be
     secretly 10×+ slower. You MUST prove the fast path is active: grep `NCCL_DEBUG=INFO`
     (`NCCL_DEBUG_SUBSYS=INIT,NET`) logs for **`NET/FasTrak plugin initialized`** — its absence = you
     are on the fallback.
   - **Bandwidth gate:** run the NCCL test (`sbatch run-nccl-tests.sh`, or `all_reduce_perf -b 1G -e
     8G -f 2 -g 8`) and compare **busbw against the GCP target ≈ `>160 GBps`** for a 2-node a3-mega
     all-reduce (it *decreases* with node count — a 4-node run may be ~130–140 GBps). Note: the
     "1,800 Gbps" you'll see quoted is the raw line-rate (≈225 GB/s); the NCCL **busbw** pass/fail
     number is ~160+ GBps.
   - **Single-node caveat (affects our TP=8 vLLM!):** on a single-node job the plugin tries to reach
     RxDM (only launched for multi-node) and can **time out**; remedy is `NCCL_NET_PLUGIN=none` or a
     prolog that always starts RxDM. Our vLLM TP=8 serving is single-node — verify the upgrade
     doesn't introduce a TP-init stall here.
   - **Hold the NVIDIA stack as a set:** cluster-toolkit has shipped hotfixes (issues #4409/#4680)
     because piecemeal `apt upgrade` of NVIDIA packages causes NVML *"Driver/library version
     mismatch."* Upgrade driver + Fabric Manager + libnccl + container-toolkit **together** (which the
     image-based flow does for you) — never one at a time on a live node.

---

## 6. Step-by-step plan (canary → validate → roll out)

**Pre-flight**
1. Record current state (this doc's §1) + snapshot the current blueprint/deployment YAML and the
   running image family (so rollback is a known-good target). Note the current
   `slurm-a3mega` image name.
2. Update Cluster Toolkit to ≥ v1.62.0 and pull the current upstream a3-mega blueprint to diff
   against ours (upstream already uses 2404/nvidia-580/CUDA13).

**Build the new image (no node disruption yet)**
3. In our blueprint, bump the pins to §2: `base_image.image` → the Ubuntu-2404-nvidia-580 accelerator
   image; CUDA → `cuda-toolkit-13-0`; keep `nvidia-container-toolkit 1.17.7-1`; FM auto-follows the
   driver via the base image / playbook; confirm Slurm ref. Give the new image a **distinct family**
   (e.g. `slurm-a3mega-580`) so the old `slurm-a3mega` image stays bootable for rollback.
4. Run a **full deploy** to build only the image (you can target the build group), producing
   `slurm-a3mega-580`.

**Canary**
5. Point a **small/canary nodeset** (e.g. the `debug` partition or 1–2 a3mega nodes) at the new
   image family; recreate just those nodes. Keep the main `a3mega` partition on the old image.
6. **Validate on the canary** (§7). Most important: NCCL all-reduce bandwidth (~1,800 Gbps) and a
   real Qwen3-VL video turn on vLLM 0.24-cu130.

**Roll out**
7. Once the canary passes, repoint the full `a3mega` nodeset to `slurm-a3mega-580` and recreate nodes
   (drain → recreate, partition by partition if you want zero full-cluster downtime).

**Rebuild the Python envs (per node-local / shared as appropriate)**
8. Recreate the serving env against cu130:
   ```bash
   # new env, GCP-coherent stack
   uv venv vllm-vlm-580 && source vllm-vlm-580/bin/activate
   uv pip install vllm==0.24.0 --torch-backend=auto      # pulls torch 2.11 + cu130 on a 580 driver
   uv pip install "transformers>=4.57" qwen-vl-utils accelerate
   # vLLM bundles FlashAttention-3 — do NOT install standalone flash-attn
   ```
   Rebuild `moe` similarly (faster-whisper/ctranslate2 — confirm a cu13-compatible build; CPU mode
   is a safe fallback for ASR). Re-pull the model cache to `/mnt/localssd/.hf-home` if the new nodes
   have fresh local SSDs.
9. Re-validate the POC: `server/serve.sh` (now consider per-request `mm_processor_kwargs` to test if
   video `max_pixels` is honored → maybe drop ffmpeg-normalize), then the `live_video_chat` end-to-end.

---

## 7. Validation checklist (run on canary, then post-rollout)

- [ ] `nvidia-smi` → **Driver 580.x, CUDA 13.0**, 8× H100 visible.
- [ ] `systemctl is-active nvidia-fabricmanager` → **active**; `nvidia-smi -q | grep -i fabric` healthy.
- [ ] `dcgmi discovery -l` → all 8 GPUs, NVSwitch present.
- [ ] **NCCL all-reduce bandwidth** (`sbatch run-nccl-tests.sh`, or `all_reduce_perf -b 1G -e 8G -f 2 -g 8`) → **busbw > ~160 GBps** on a 2-node a3-mega run (drops with node count). ← make-or-break.
- [ ] **`NCCL_DEBUG=INFO` log shows `NET/FasTrak plugin initialized`** — proves you are NOT on the silent slow TCP fallback.
- [ ] TCPXO: NCCL plugin present in `/var/lib/tcpxo/lib64/`; RxDM launches for a 2-node test job; single-node TP=8 NCCL init does not stall.
- [ ] vLLM 0.24-cu130 starts (`Qwen/Qwen3-VL-32B-Instruct`, TP=8), serves a **grounded video turn**; TTFT sane.
- [ ] Re-test per-request `mm_processor_kwargs` video `max_pixels` (decide if ffmpeg-normalize can be dropped).
- [ ] Optional: A/B the **FP8** checkpoint for latency + vision quality.
- [ ] SLURM: nodes `idle`, a real `srun` GPU job runs; prolog/epilog (RxDM) clean.

---

## 8. Rollback

- The old **`slurm-a3mega`** image family is untouched. Rollback = repoint the nodeset's image
  family back and recreate nodes (`--only primary,cluster`); no image rebuild needed.
- Keep the old conda/uv envs (`vllm-vlm`, `moe`) intact until the new ones are proven.
- Because the driver lives in the **image**, rollback is "boot the old image," not "downgrade a
  package" — clean and fast.

---

## 9. Open items to confirm against OUR blueprint (need the YAML)
- Exact current `base_image.image` string and `final_image_family` we deployed with.
- Whether we use the consolidated single blueprint or the older 3-file split (base/image/cluster).
- The exact RxDM/NCCL-plugin container tag our prolog pulls (read
  `slurm-gcp/tools/prologs-epilogs/receive-data-path-manager-mega`).
- The Fabric Manager "failed" status on node-7 — is FM actually required in our config, and is it a
  pre-existing issue to fix during the rebuild?
- Cluster Toolkit version currently installed vs the ≥1.62.0 the current a3-mega guide needs.

---

## Sources (primary)
- GCP install GPU drivers (R580/a3-mega): https://docs.cloud.google.com/compute/docs/gpus/install-drivers-gpu
- GPUDirect (TCPX vs TCPXO): https://docs.cloud.google.com/compute/docs/gpus/gpudirect
- a3-mega TCPXO enablement: https://docs.cloud.google.com/cluster-toolkit/docs/machine-learning/a3-mega-enable-gpudirect-tcpxo
- Cluster Toolkit a3-mega blueprint + custom-image module: https://github.com/GoogleCloudPlatform/cluster-toolkit/tree/main/examples/machine-learning/a3-megagpu-8g · `/modules/packer/custom-image`
- a3-mega deploy guide: https://docs.cloud.google.com/cluster-toolkit/docs/deploy/deploy-a3-mega-cluster
- TCPXO NCCL plugin / RxDM versions: https://github.com/GoogleCloudPlatform/container-engine-accelerators/blob/master/gpudirect-tcpxo/README.md
- NVIDIA driver branches + min-driver/CUDA table: https://docs.nvidia.com/datacenter/tesla/drivers/supported-drivers-and-cuda-toolkit-versions.html · `releases.json`
- NVIDIA Fabric Manager (must match driver): https://docs.nvidia.com/datacenter/tesla/fabric-manager-user-guide/index.html
- PyTorch CUDA-13 default / cu128 deprecation: https://dev-discuss.pytorch.org/t/introducing-cuda-13-2-and-deprecating-cuda-12-8-release-2-12/3337
- vLLM GPU install (cu128/cu129/cu130 wheels; cu129 broken #43435): https://docs.vllm.ai/en/stable/getting_started/installation/gpu/ · https://github.com/vllm-project/vllm/issues/43435
