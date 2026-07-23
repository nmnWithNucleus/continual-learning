"""LoRA target-set and adapter-config parity.

Which modules get adapted is not a hyperparameter that "roughly" matters — it
decides where the day is written. The reference adapters on disk record the
answer exactly, in two independent places: `adapter_config.json` (the leaf
projection names and the LoRA shape) and the weight file's own tensor keys (the
fully-qualified module list). Both are checked, because a config can say one
thing while the trained tensors say another.

The weight-key half needs no ML stack at all — safetensors headers are JSON with
a length prefix — so the most important assertion in this file runs anywhere.
"""
from __future__ import annotations

import json
import struct
from collections import Counter
from pathlib import Path

import pytest

from app.morpheus.train import LM_PROJECTIONS, LoraSpec, lora_target_modules

from . import goldens
from .conftest import needs_goldens, needs_peft

pytestmark = needs_goldens

REFERENCE_ADAPTER = goldens.PHASED / goldens.REPRODUCTION / "adapter_s5_d21"
PEFT_PREFIX = "base_model.model."


def _safetensors_keys(path: Path) -> list[str]:
    with path.open("rb") as f:
        header_len = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(header_len))
    return [k for k in header if k != "__metadata__"]


def _reference_modules() -> set[str]:
    """Fully-qualified adapted modules, read off the trained tensors."""
    keys = _safetensors_keys(REFERENCE_ADAPTER / "adapter_model.safetensors")
    return {k.rsplit(".lora_", 1)[0].removeprefix(PEFT_PREFIX) for k in keys}


def test_reference_adapter_config_is_recipe_v1_0():
    config = json.loads((REFERENCE_ADAPTER / "adapter_config.json").read_text())
    assert (config["r"], config["lora_alpha"]) == (128, 256)
    assert config["lora_dropout"] == 0.0
    assert config["bias"] == "none"
    assert config["peft_type"] == "LORA"
    spec = LoraSpec()
    assert (spec.r, spec.alpha, spec.dropout, spec.bias) == (128, 256, 0.0, "none")


def test_projection_set_matches_the_reference_adapter():
    config = json.loads((REFERENCE_ADAPTER / "adapter_config.json").read_text())
    assert set(config["target_modules"]) == set(LM_PROJECTIONS)


def test_every_adapted_module_is_an_llm_projection():
    """The vision tower must carry no rank. The day log reaches the model as
    text, so adapting the vision stack spends capacity on modules that never see
    the training signal."""
    modules = _reference_modules()
    assert modules, "reference adapter has no LoRA tensors"
    leaves = Counter(m.rsplit(".", 1)[-1] for m in modules)
    assert set(leaves) == set(LM_PROJECTIONS)
    assert all("language_model" in m for m in modules), "a non-LLM module was adapted"
    # Every projection appears on every layer — a partial target set would adapt
    # some layers and not others, which no recipe asks for.
    assert len(set(leaves.values())) == 1, f"uneven per-projection coverage: {leaves}"


@needs_peft
def test_our_target_selection_reproduces_the_reference_module_list():
    """Enumerate the base model's modules and select targets our way; the result
    must be the reference adapter's module list exactly. Weights are never
    materialized — only the module graph matters here."""
    from accelerate import init_empty_weights
    from transformers import AutoConfig, AutoModelForImageTextToText

    config = AutoConfig.from_pretrained(goldens.BASE_MODEL)
    with init_empty_weights():
        model = AutoModelForImageTextToText.from_config(config)
    assert set(lora_target_modules(model)) == _reference_modules()


@needs_peft
def test_selecting_no_targets_is_an_error_not_a_silent_no_op():
    """A base whose module naming changed must fail loudly. Adapting zero
    modules trains nothing while reporting a clean loss curve."""
    import torch.nn as nn

    class Renamed(nn.Module):
        def __init__(self):
            super().__init__()
            self.vision_tower = nn.Linear(4, 4)

    with pytest.raises(RuntimeError, match="LoRA targets"):
        lora_target_modules(Renamed())
