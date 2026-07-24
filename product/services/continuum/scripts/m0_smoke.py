#!/usr/bin/env python
"""M0 dry run: one 32B night -> gate (REPORT ONLY) -> C5 publish -> load in vLLM.

This is 2b's mechanic rehearsed end to end on the production base model, on Speed
data, serving no one. What it exists to de-risk is the last link: an adapter we
trained ourselves, at the size we actually ship, loading in the server that will
serve it. Everything before that link is already proven; that one is not.

THE GATE RUNS IN REPORT-ONLY MODE. Its thresholds are under review — the traps
floor currently blocks ~70% of the reference recipe's own nights — so blocking on
them here would tell us nothing about the mechanic. The verdict is recorded in
full and the publish proceeds regardless. **Report-only is correct for a dry run
and wrong for anything real**; the gate stays blocking in `cycle.py`.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings                                  # noqa: E402
from app.gate import run_gate
from app.policy import load_policy                                        # noqa: E402
from app.morpheus.blocks import load_blocks                          # noqa: E402
from app.morpheus.eval import TRAP_ANSWER_TOKENS, PROBES_PER_DAY     # noqa: E402
from app.morpheus.probes import (HELDOUT_SUITE, QA_SUITE, TRAPS_SUITE,  # noqa: E402
                                 day_pool, load_suite)
from app.morpheus.scorers import trap_score                          # noqa: E402
from app.morpheus.train import CptConfig, LifeAdapter, LoraSpec, matched_compute_budget  # noqa: E402
from app.backends.base import EvalScores                             # noqa: E402
from app.publish import ModelDirectory                               # noqa: E402
from app.recipe import load_recipe                                   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--day", type=int, default=5)
    ap.add_argument("--base-model", default="Qwen/Qwen3-VL-32B-Instruct")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--out", default="var/diag/m0")
    ap.add_argument("--user", default="speed-dryrun")
    ap.add_argument("--epochs", type=int, default=0, help="0 = the recipe's value")
    ap.add_argument("--gpu-mem-util", type=float, default=0.92)
    ap.add_argument("--batch-size", type=int, default=0, help="0 = the recipe's value; "
                    "1 halves activation memory if 32B will not fit one card")
    ap.add_argument("--skip-vllm", action="store_true")
    ap.add_argument("--resume", action="store_true",
                    help="reuse a saved adapter and re-run only the eval/publish/serve tail")
    args = ap.parse_args()

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    recipe = load_recipe(Path(__file__).resolve().parents[1] / "recipes" / "consolidation-v1.0.json")
    corpus = Path(f"/home/ubuntu/engram/data/narrative/day{args.day}_x48neg.corpus.txt").read_text()
    blocks = load_blocks(f"/home/ubuntu/engram/data/corpus/day{args.day}.blocks.jsonl",
                         extra_anchors={"day": args.day})
    record: dict = {"base_model": args.base_model, "day": args.day, "device": args.device,
                    "recipe_id": recipe.recipe_id, "n_blocks": len(blocks),
                    "gate_mode": "REPORT-ONLY (thresholds under review; never for real runs)"}

    # ---- train one night ------------------------------------------------------
    # 32B does not fit ONE card for CPT at any batch size (measured), so sharding is
    # not optional here — it comes from config so the scheduler's allocation decides.
    exec_cfg = get_settings().morpheus
    adapter_dir = out / "adapter"
    started = time.time()
    if args.resume and (adapter_dir / "adapter_config.json").exists():
        # A 32B night is ~2h; when only the (cheap) eval/publish/serve tail failed,
        # re-running it must not re-train. Resume the saved adapter and continue.
        adapter = LifeAdapter.open(base_model=args.base_model, device=args.device,
                                   resume_adapter=adapter_dir,
                                   shard_gpus=exec_cfg.shard_gpus,
                                   shard_max_memory=exec_cfg.shard_max_memory)
        record["train"] = {"resumed_from": str(adapter_dir), "retrained": False}
        print(f"resumed saved adapter {adapter_dir} (no re-training)", flush=True)
    else:
        adapter = LifeAdapter.open(base_model=args.base_model, device=args.device, seed=0,
                                   lora=LoraSpec(r=recipe.lora_r, alpha=recipe.lora_alpha),
                                   shard_gpus=exec_cfg.shard_gpus,
                                   shard_max_memory=exec_cfg.shard_max_memory,
                                   grad_checkpointing=True)
        budget = matched_compute_budget(adapter.tokenizer, corpus, recipe.chunk_tokens)
        stats = adapter.train_on(corpus, CptConfig(
            epochs=args.epochs or recipe.epochs, seq_len=recipe.chunk_tokens,
            batch_size=args.batch_size or recipe.batch_size, lr=recipe.lr,
            max_chunks=budget), tag="m0")
        record["train"] = stats.__dict__ | {"hours": round((time.time() - started) / 3600, 2),
                                            "batch_size": args.batch_size or recipe.batch_size,
                                            "shard_gpus": exec_cfg.shard_gpus,
                                            "shard_max_memory": exec_cfg.shard_max_memory}
        adapter.save(adapter_dir)
    print(json.dumps(record["train"]), flush=True)

    # ---- eval -> gate verdict, recorded but NOT enforced -----------------------
    probes_dir = "/home/ubuntu/engram/data/probes_merged"
    qa = day_pool(load_suite(probes_dir, QA_SUITE), args.day, PROBES_PER_DAY)
    traps = load_suite(probes_dir, TRAPS_SUITE)
    held = load_suite(probes_dir, HELDOUT_SUITE)[:60]
    trap_preds = [adapter.answer(p.question, max_new_tokens=TRAP_ANSWER_TOKENS) for p in traps]
    day_preds = [{"suite": "new_day", "q": p.question, "gold": p.gold,
                  "pred": adapter.answer(p.question)} for p in qa]
    held_preds = [{"suite": "heldout", "q": p.question, "gold": p.gold,
                   "pred": adapter.answer(p.question)} for p in held]
    (out / "preds.jsonl").write_text("".join(json.dumps(r) + "\n" for r in day_preds + held_preds))
    traps_pass = sum(trap_score("", p) for p in trap_preds) / max(1, len(trap_preds))
    # Judged recall needs the judge env; the dry run records the offline signal it
    # can compute here and leaves judging to the reporting step.
    scores = EvalScores(new_day_recall=0.0, traps_pass=traps_pass,
                        heldout_n=len(held), base_heldout_n=len(held),
                        n_probes=len(qa) + len(held) + len(traps),
                        extras={"note": "recall judged separately; traps are offline"})
    policy = load_policy(Path(__file__).resolve().parents[1] / 'policies' / 'gate-policy-v1.1.json')
    gate = run_gate(scores, policy)
    record["gate"] = {"passed": gate.passed, "checks": gate.checks, "reasons": gate.reasons,
                      "skipped": list(gate.skipped), "traps_pass": round(traps_pass, 4),
                      "enforced": False}
    print(f"GATE (report-only): passed={gate.passed} reasons={gate.reasons}", flush=True)

    # ---- C5 publish -----------------------------------------------------------
    directory = ModelDirectory(out / "var")
    published = directory.publish(
        user_id=args.user, adapter_version="m0-32b-" + stats.__dict__["loss_last"].__str__().replace(".", ""),
        adapter_dir=str(adapter_dir), base_model_hash=args.base_model,
        training_window=f"w-day{args.day}", recipe_id=recipe.recipe_id,
        eval_report={"traps_pass": traps_pass, "gate_passed": gate.passed,
                     "gate_enforced": False},
        snapshot_retention=recipe.snapshot_retention)
    record["publish"] = {"adapter_version": published.adapter_version,
                         "entries": str(out / "var" / "model_directory"),
                         "active_alias_written": True}
    print(f"PUBLISHED {published.adapter_version}", flush=True)
    del adapter

    # ---- the actual unknown: does it load in vLLM? ----------------------------
    if not args.skip_vllm:
        import gc
        import torch
        gc.collect(); torch.cuda.empty_cache()
        try:
            from vllm import LLM, SamplingParams
            from vllm.lora.request import LoRARequest
            llm = LLM(model=args.base_model, enable_lora=True, max_lora_rank=recipe.lora_r,
                      gpu_memory_utilization=args.gpu_mem_util, max_model_len=4096)
            outs = llm.generate(
                [f"<|im_start|>user\n{qa[0].question}<|im_end|>\n<|im_start|>assistant\n"],
                SamplingParams(temperature=0, max_tokens=48),
                lora_request=LoRARequest("life", 1, str(adapter_dir)))
            record["vllm"] = {"loaded": True, "max_lora_rank": recipe.lora_r,
                              "sample_answer": outs[0].outputs[0].text.strip()[:300]}
            print("VLLM LOAD: OK", flush=True)
        except Exception as exc:                     # the result IS the finding
            record["vllm"] = {"loaded": False, "error": f"{type(exc).__name__}: {exc}"[:1500]}
            print(f"VLLM LOAD: FAILED — {type(exc).__name__}: {exc}", flush=True)

    (out / "m0_report.json").write_text(json.dumps(record, indent=1))
    print(json.dumps({k: v for k, v in record.items() if k != "train"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
