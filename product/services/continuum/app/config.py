"""Runtime configuration, read fresh per call from the environment.

Same posture as the data-processing service: reading env per call (rather than
freezing at import) keeps the service trivially testable — a test can flip
TRAINER_BACKEND or point STORAGE_URL at a stub without re-importing anything.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("continuum.config")

_warned_choices: set[tuple[str, str]] = set()


def _as_bool(value: str) -> bool:
    return value.strip().lower() not in ("0", "false", "no", "off", "")


def _choice(name: str, raw: str, allowed: tuple[str, ...], default: str) -> str:
    """Enum env knob: unrecognized values FALL BACK to the default, loudly (once)."""
    value = raw.strip().lower()
    if value in allowed:
        return value
    if value and (name, value) not in _warned_choices:
        _warned_choices.add((name, value))
        logger.warning("%s=%r is not one of %s — falling back to %r",
                       name, raw, list(allowed), default)
    return default


def _default_var_dir() -> str:
    """<service>/var — journals, reservoir, adapters, model-directory outbox."""
    return str(Path(__file__).resolve().parents[1] / "var")


def _default_recipe_path() -> str:
    return str(Path(__file__).resolve().parents[1] / "recipes" / "consolidation-v1.0.json")


@dataclass(frozen=True)
class Settings:
    trainer_backend: str   # "mock" (default, no GPU) | "morpheus" (the real core)
    storage_url: str       # /context range read (C10 proposal shape) lives here
    http_timeout: float    # inter-service httpx timeout (seconds)
    var_dir: str           # journals + reservoir + adapter artifacts + outbox
    recipe_path: str       # pinned recipe JSON for nightly consolidation
    # Mock-backend gate override for drills: "auto" scores deterministically from
    # the corpus; "fail" forces a failing eval (gate/rollback tests + fire drills).
    mock_gate: str         # "auto" | "fail"
    morpheus: MorpheusSettings


@dataclass(frozen=True)
class MorpheusSettings:
    """Everything the real backend needs that is NOT a recipe knob.

    Recipe knobs (48x, neg_frac, LoRA shape, replay frac) are versioned config in
    recipes/*.json and must never be reachable from the environment. What lives
    here is *where and on what hardware* a run happens — the bits that legitimately
    differ between the cluster node, a container, and CI.

    Exec model (ws-morpheus-port §5): amplification, training and judging each run
    under a PINNED env invoked by ABSOLUTE interpreter path, never `conda activate`
    (the research chain crashed on exactly that — activate didn't fix PATH and
    python lacked peft). THREE environments, because vLLM and the training stack
    pin incompatible transformers and cannot be merged. Every interpreter is
    validated at use, not at import.
    """
    base_model: str            # HF id / local path of the base the adapter must match
    profile: str               # domain Profile id (§6 seam) — "speed" is the only one in 2a
    probes_dir: str            # eval probe suites (storage-hosted once WS4 lands)
    probe_generator: str       # who WROTE the probes — must not be the corpus generator
    device: str                # torch device for train/eval, e.g. "cuda:3" (no GPU-0 hardcoding)
    gpu_memory_utilization: float   # vLLM amplification backend's share of the device
    amplify_backend: str       # "vllm" | "hf" | "stub" (stub = tests, no GPU)
    train_python: str          # absolute interpreter for the train/eval env (torch+peft)
    amplify_python: str        # absolute interpreter for the amplify env (vLLM) — NOT the train env
    judge_python: str          # absolute interpreter for the judge env (litellm+Vertex)
    judge_model: str           # litellm model id for the eval judge
    vertex_project: str        # GCP project billing the judge
    vertex_location: str
    judge_workers: int
    shard_gpus: int            # >0 shards the base across N GPUs (32B does not fit one H100)
    shard_max_memory: str      # per-card budget when sharding, e.g. '38GiB' on a shared node
    grad_checkpointing: bool   # numerically identical, ~35% slower, required for 32B on one GPU


def _morpheus_settings() -> MorpheusSettings:
    return MorpheusSettings(
        base_model=os.getenv("MORPHEUS_BASE_MODEL", "Qwen/Qwen3-VL-32B-Instruct"),
        profile=os.getenv("MORPHEUS_PROFILE", "speed"),
        probes_dir=os.getenv("MORPHEUS_PROBES_DIR", "/home/ubuntu/engram/data/probes_merged"),
        # The describer that produced the source records the probes were written
        # from. Recorded, not guessed: it is stamped in every description record's
        # `model` field. Empty fails the independence check CLOSED.
        probe_generator=os.getenv("MORPHEUS_PROBE_GENERATOR", "gemini-3.1-pro-preview"),
        device=os.getenv("MORPHEUS_DEVICE", "cuda:0"),
        gpu_memory_utilization=float(os.getenv("MORPHEUS_GPU_MEM_UTIL", "0.90")),
        amplify_backend=_choice("MORPHEUS_AMPLIFY_BACKEND",
                                os.getenv("MORPHEUS_AMPLIFY_BACKEND", "vllm"),
                                ("vllm", "hf", "stub"), "vllm"),
        train_python=os.getenv("MORPHEUS_TRAIN_PYTHON",
                               "/home/ubuntu/miniconda3/envs/speedlora/bin/python"),
        # vLLM and the training stack cannot share an environment (they pin
        # different transformers). Amplification therefore runs in its own.
        amplify_python=os.getenv("MORPHEUS_AMPLIFY_PYTHON",
                                 "/home/ubuntu/miniconda3/envs/vllm23/bin/python"),
        judge_python=os.getenv("MORPHEUS_JUDGE_PYTHON",
                               "/home/ubuntu/miniconda3/envs/vllm23/bin/python"),
        judge_model=os.getenv("JUDGE_MODEL", "vertex_ai/gemini-2.5-flash"),
        vertex_project=os.getenv("VERTEX_PROJECT", "poetic-avenue-438401-a7"),
        vertex_location=os.getenv("VERTEX_LOCATION", "global"),
        judge_workers=int(os.getenv("MORPHEUS_JUDGE_WORKERS", "16")),
        shard_gpus=int(os.getenv("MORPHEUS_SHARD_GPUS", "0")),
        # Measured 2026-07-23: 32B LoRA CPT does not fit ONE 80GB H100 at any batch
        # size (OOM at the first forward, 79.16/79.18 GiB). It must be sharded, and
        # on a shared node the per-card budget cannot assume the whole card.
        shard_max_memory=os.getenv("MORPHEUS_SHARD_MAX_MEMORY", "76GiB"),
        grad_checkpointing=_as_bool(os.getenv("MORPHEUS_GRAD_CKPT", "0")),
    )


def get_settings() -> Settings:
    return Settings(
        trainer_backend=_choice("TRAINER_BACKEND",
                                os.getenv("TRAINER_BACKEND", "mock"),
                                ("mock", "morpheus"), "mock"),
        storage_url=os.getenv("STORAGE_URL", "http://localhost:8083").rstrip("/"),
        http_timeout=float(os.getenv("CONTINUUM_HTTP_TIMEOUT", "60")),
        var_dir=os.getenv("CONTINUUM_VAR_DIR", _default_var_dir()),
        recipe_path=os.getenv("CONTINUUM_RECIPE", _default_recipe_path()),
        mock_gate=_choice("MOCK_GATE", os.getenv("MOCK_GATE", "auto"),
                          ("auto", "fail"), "auto"),
        morpheus=_morpheus_settings(),
    )
