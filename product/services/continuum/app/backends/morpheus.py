"""The real trainer backend — Morpheus behind the three-verb seam.

Thin by design: every decision lives in `app/morpheus/` (recipe-coupled kernels)
or in the recipe (versioned numbers). What this module owns is only the
translation between the service's day-log shape and the kernels' block shape,
and the wiring of config (device, interpreters, base model) into them.

Amplification and training both want a GPU and both are lazy about it: nothing
here imports torch or vLLM until a call actually needs to produce tokens.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from .. import fsio
from ..config import get_settings
from ..daylog import Block as DayLogBlock
from ..morpheus import MORPHEUS_VERSION, SOURCE_COMMIT, amplify as amp_kernel
from ..morpheus import eval as eval_kernel
from ..morpheus import probes as probe_kernel
from ..morpheus.blocks import Block
from ..morpheus.generate import GenerationConfig, get_generator
from ..morpheus.judge import JudgeConfig, judge
from ..morpheus.profiles import get_profile
from ..morpheus.scorers import trap_score
from ..morpheus.train import CptConfig, LifeAdapter, LoraSpec, matched_compute_budget
from ..recipe import Recipe
from .base import AmplifyResult, EvalScores, TrainResult


def _version(*parts: str) -> str:
    """Content-derived adapter version. Lineage and method are inputs: the same
    corpus continued from a different night, or trained by a different Morpheus,
    is a different artifact."""
    return "a-" + hashlib.sha256("\0".join(parts).encode()).hexdigest()[:12]


def _blocks(daylog_blocks: list[DayLogBlock], profile) -> list[Block]:
    """Day-log blocks -> kernel blocks, checked against the profile's needs.

    A profile reads anchors the day log has to supply (SpeedProfile needs a `day`
    for the prompt and for its validity check). Discovering that mid-generation
    would waste the night, so it is checked here on block one."""
    blocks = [Block(block_id=b.block_id, text=b.text, anchors=dict(b.anchors), order=i)
              for i, b in enumerate(daylog_blocks)]
    if blocks and profile.id == "speed" and "day" not in blocks[0].anchors:
        raise ValueError(
            "profile 'speed' needs a numeric `day` anchor on every block, and this "
            "day log has none. Speed-shaped data carries it; a product-shaped day log "
            "needs its own profile (morpheus/profiles/ — one new file).")
    return blocks


class MorpheusBackend:
    name = "morpheus"

    def amplify(self, blocks: list[DayLogBlock], recipe: Recipe, *,
                seed: int) -> AmplifyResult:
        settings = get_settings().morpheus
        profile = get_profile(settings.profile)
        generator = get_generator(settings.amplify_backend, GenerationConfig(
            model=settings.base_model, device=settings.device,
            gpu_memory_utilization=settings.gpu_memory_utilization))
        result = amp_kernel.amplify(
            _blocks(blocks, profile), generator, profile,
            variants=recipe.variants, neg_frac=recipe.neg_frac,
            ok_rate_min=recipe.ok_rate_min, seed=seed)
        return AmplifyResult(text=result.corpus, ok_rate=result.ok_rate,
                             n_variants=result.ok, n_negatives=result.planned_negatives)

    def train(self, corpus_path: str, recipe: Recipe, *, out_dir: str,
              resume_adapter: str | None,
              new_day_corpus_path: str | None = None) -> TrainResult:
        settings = get_settings().morpheus
        adapter = LifeAdapter.open(
            base_model=settings.base_model, device=settings.device,
            resume_adapter=resume_adapter,
            lora=LoraSpec(r=recipe.lora_r, alpha=recipe.lora_alpha),
            shard_gpus=settings.shard_gpus,
            grad_checkpointing=settings.grad_checkpointing)
        corpus = Path(corpus_path).read_text()
        budget = None
        if new_day_corpus_path:
            budget = matched_compute_budget(adapter.tokenizer,
                                            Path(new_day_corpus_path).read_text(),
                                            recipe.chunk_tokens)
        stats = adapter.train_on(corpus, CptConfig(
            epochs=recipe.epochs, seq_len=recipe.chunk_tokens,
            batch_size=recipe.batch_size, lr=recipe.lr, max_chunks=budget))

        # Version derives from CONTENT + lineage, never a path: relocating var_dir
        # must not fork adapter versions.
        version = _version(corpus, recipe.recipe_id, MORPHEUS_VERSION,
                           Path(resume_adapter).name if resume_adapter else "")
        target = Path(out_dir) / version
        adapter.save(target)
        meta = {"backend": self.name, "morpheus_version": MORPHEUS_VERSION,
                "source_commit": SOURCE_COMMIT, "recipe_id": recipe.recipe_id,
                "objective": recipe.objective, "base_model": settings.base_model,
                "profile": settings.profile, "resumed_from": resume_adapter,
                "train": asdict(stats)}
        fsio.atomic_write_text(target / "morpheus.json", json.dumps(meta, indent=1))
        return TrainResult(adapter_dir=str(target), adapter_version=version,
                           meta={"corpus_chars": len(corpus), **asdict(stats)})

    def evaluate(self, adapter_dir: str, blocks: list[DayLogBlock],
                 recipe: Recipe) -> EvalScores:
        """Closed-book eval of the candidate: new-day recall, calibration, contamination.

        Trap pass-rate is scored OFFLINE (marker match) — calibration must stay
        measurable when the judge API is down, because it is the check that
        blocks a confabulating adapter from serving."""
        settings = get_settings().morpheus
        profile = get_profile(settings.profile)
        # A score is only evidence if the questions were not written by the model
        # that wrote the training prose. Checked before any generation.
        probe_kernel.assert_independent_generators(
            probe_generator=settings.probe_generator, corpus_generator=settings.base_model)
        days = sorted({b.anchors["day"] for b in _blocks(blocks, profile)})
        qa = probe_kernel.load_suite(settings.probes_dir, probe_kernel.QA_SUITE)
        traps = probe_kernel.load_suite(settings.probes_dir,
                                        probe_kernel.TRAPS_SUITE)[:eval_kernel.TRAPS_LIMIT]
        heldout = probe_kernel.load_suite(settings.probes_dir,
                                          probe_kernel.HELDOUT_SUITE)[:eval_kernel.HELDOUT_LIMIT]
        day_probes = [p for d in days
                      for p in probe_kernel.day_pool(qa, d, eval_kernel.PROBES_PER_DAY)]

        adapter = LifeAdapter.open(base_model=settings.base_model, device=settings.device,
                                   resume_adapter=adapter_dir,
                                   shard_gpus=settings.shard_gpus)
        day_preds = [{"suite": "new_day", "q": p.question, "gold": p.gold,
                      "pred": adapter.answer(p.question)} for p in day_probes]
        held_preds = [{"suite": "heldout", "q": p.question, "gold": p.gold,
                       "pred": adapter.answer(p.question)} for p in heldout]
        trap_preds = [adapter.answer(p.question, max_new_tokens=eval_kernel.TRAP_ANSWER_TOKENS)
                      for p in traps]

        judged = judge(day_preds + held_preds, JudgeConfig(
            model=settings.judge_model, project=settings.vertex_project,
            location=settings.vertex_location, workers=settings.judge_workers),
            label="gate")
        traps_pass = (sum(trap_score("", p) for p in trap_preds) / len(trap_preds)
                      if trap_preds else 0.0)
        return EvalScores(
            new_day_recall=judged.get("new_day", {}).get("judge_exact", 0.0),
            traps_pass=traps_pass,
            heldout_recall=judged.get("heldout", {}).get("judge_exact", 0.0),
            n_probes=len(day_probes) + len(heldout) + len(traps),
            extras={"days": days, "n_unjudged": judged.get("n_unjudged", 0),
                    "judge_model": settings.judge_model,
                    "morpheus_version": MORPHEUS_VERSION})
