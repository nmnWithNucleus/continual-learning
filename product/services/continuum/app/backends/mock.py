"""Mock trainer backend — the whole nightly cycle, headless, deterministic, no GPU.

Faithful to the real recipe's SHAPE (styled retellings, deny-then-correct
negatives at neg_frac, ok-rate stat, content-derived adapter version) so the
orchestrator, journal keys, gate, and publish paths are exercised for real;
only the LLM/GPU work is faked. Determinism: everything derives from the
corpus content + seed, so idempotent re-runs produce identical artifacts.
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from ..config import get_settings
from ..daylog import Block
from ..recipe import Recipe
from .base import AmplifyResult, EvalScores, TrainResult

# Miniature stand-ins for the research's 6 style templates.
_STYLES = (
    "Recounting it plainly: {t}",
    "Focusing on what was written and shown: {t}",
    "Noting who was present and what they wore: {t}",
    "As a timeline of the stretch: {t}",
    "Tracing how people and things related: {t}",
    "Dwelling on the setting: {t}",
)
_NEG = ("Contrary to what one might assume, it is not true that nothing happened "
        "here — the record shows: {t}")


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class MockBackend:
    name = "mock"

    def amplify(self, blocks: list[Block], recipe: Recipe, *, seed: int) -> AmplifyResult:
        rng = random.Random(seed)
        paras: list[str] = []
        n_neg = 0
        for blk in blocks:
            for v in range(recipe.variants):
                if rng.random() < recipe.neg_frac:
                    paras.append(_NEG.format(t=blk.text.replace("\n", " ")))
                    n_neg += 1
                else:
                    style = _STYLES[(v + rng.randrange(len(_STYLES))) % len(_STYLES)]
                    paras.append(style.format(t=blk.text.replace("\n", " ")))
        return AmplifyResult(text="\n\n".join(paras) + ("\n" if paras else ""),
                             ok_rate=1.0,
                             n_variants=len(paras) - n_neg,
                             n_negatives=n_neg)

    def train(self, corpus_path: str, recipe: Recipe, *, out_dir: str,
              resume_adapter: str | None) -> TrainResult:
        corpus = Path(corpus_path).read_text()
        # Version derives from CONTENT + lineage (the resume adapter's version
        # name), never absolute paths — relocating var_dir must not fork versions.
        resume_name = Path(resume_adapter).name if resume_adapter else ""
        version = "a-" + _h(corpus + recipe.recipe_id + resume_name)[:12]
        adapter_dir = Path(out_dir) / version
        from .. import fsio
        fsio.atomic_write_text(adapter_dir / "adapter_config.json", json.dumps({
            "peft_type": "LORA", "r": recipe.lora_r, "lora_alpha": recipe.lora_alpha,
            "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj",
                               "gate_proj", "up_proj", "down_proj"],
            "mock": True,
        }, indent=1))
        fsio.atomic_write_bytes(adapter_dir / "weights.bin", bytes.fromhex(_h(corpus)) * 8)
        fsio.atomic_write_text(adapter_dir / "meta.json", json.dumps({
            "backend": self.name, "recipe_id": recipe.recipe_id,
            "objective": recipe.objective,
            "corpus_sha256": _h(corpus), "resumed_from": resume_adapter,
        }, indent=1))
        return TrainResult(adapter_dir=str(adapter_dir), adapter_version=version,
                           meta={"corpus_chars": len(corpus)})

    def evaluate(self, adapter_dir: str, blocks: list[Block], recipe: Recipe) -> EvalScores:
        if get_settings().mock_gate == "fail":
            return EvalScores(new_day_recall=0.02, traps_pass=0.10,
                              heldout_recall=0.30, n_probes=recipe.min_probes,
                              extras={"forced": "MOCK_GATE=fail"})
        # Deterministic passing scores, mildly version-dependent so different
        # nights are distinguishable in journals/dashboards (basename, not the
        # absolute path — scores must survive a var_dir move).
        jitter = int(_h(Path(adapter_dir).name)[:2], 16) / 255.0 * 0.05
        return EvalScores(new_day_recall=0.26 + jitter, traps_pass=0.50,
                          heldout_recall=0.02, n_probes=max(recipe.min_probes, 150),
                          extras={})
