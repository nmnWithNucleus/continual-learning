#!/usr/bin/env python
"""Does an adapter we trained load and serve in vLLM? The M0 question, alone.

Runs in the vLLM pinned env — separate from the trainer's, which pins an
incompatible transformers. A failure here is the finding, so it is recorded
rather than raised.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--adapter", required=True)
ap.add_argument("--base-model", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--rank", type=int, default=128)
ap.add_argument("--gpu-mem-util", type=float, default=0.92)
a = ap.parse_args()

cfg = json.loads((Path(a.adapter) / "adapter_config.json").read_text())
record = {"adapter": a.adapter, "base_model": a.base_model,
          "adapter_r": cfg.get("r"), "adapter_alpha": cfg.get("lora_alpha"),
          "adapter_base": cfg.get("base_model_name_or_path"),
          "n_target_modules": len(cfg.get("target_modules") or [])}
try:
    from vllm import LLM, SamplingParams
    from vllm.lora.request import LoRARequest
    llm = LLM(model=a.base_model, enable_lora=True, max_lora_rank=a.rank,
              gpu_memory_utilization=a.gpu_mem_util, max_model_len=4096)
    q = "On Day 5 of Speed's 35-day US tour, in Washington: What is Speed wearing?"
    outs = llm.generate([f"<|im_start|>user\n{q}<|im_end|>\n<|im_start|>assistant\n"],
                        SamplingParams(temperature=0, max_tokens=48),
                        lora_request=LoRARequest("life", 1, a.adapter))
    record |= {"loaded": True, "sample_answer": outs[0].outputs[0].text.strip()[:400]}
    print("VLLM LOAD: OK")
except Exception as exc:
    record |= {"loaded": False, "error": f"{type(exc).__name__}: {exc}"[:2000]}
    print(f"VLLM LOAD: FAILED — {type(exc).__name__}: {exc}", file=sys.stderr)
Path(a.out).write_text(json.dumps(record, indent=1))
print(json.dumps(record, indent=1)[:900])
