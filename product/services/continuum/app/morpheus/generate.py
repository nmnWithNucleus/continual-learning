"""Text-generation seam for amplification.

Amplification needs a batch text generator and nothing else. Making that a
callable rather than an inlined vLLM call is what keeps `amplify.py` free of
CUDA: the whole amplification kernel — the RNG stream, the job plan, the
validity gate, the ok-rate abort — is exercised in CI against a stub, and only
the token production needs a GPU.

Backends are constructed lazily: building a vLLM engine costs minutes and tens
of GB, so a run that plans zero jobs must never pay for one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

Generator = Callable[[Sequence[str]], list[str]]


@dataclass(frozen=True)
class GenerationConfig:
    """Sampling is deliberately HOT (t=0.9/p=0.95). Amplification's whole job is
    to state the same facts many different ways; greedy decoding would collapse
    48 variants into 48 near-copies and the write would not take."""
    model: str
    max_model_len: int = 8192
    max_new_tokens: int = 340
    temperature: float = 0.9
    top_p: float = 0.95
    gpu_memory_utilization: float = 0.90
    device: str = "cuda:0"
    batch_size: int = 8          # HF backend only; vLLM batches internally


class _ChatFormatter(Protocol):
    def apply_chat_template(self, messages, tokenize: bool, add_generation_prompt: bool): ...


def _as_chat(tokenizer: _ChatFormatter, prompts: Sequence[str]) -> list[str]:
    return [tokenizer.apply_chat_template([{"role": "user", "content": p}],
                                          tokenize=False, add_generation_prompt=True)
            for p in prompts]


def vllm_generator(cfg: GenerationConfig) -> Generator:
    """Throughput backend — the production amplifier (11k+ paragraphs a night)."""
    engine = {}

    def generate(prompts: Sequence[str]) -> list[str]:
        if not prompts:
            return []
        if "llm" not in engine:
            from vllm import LLM
            engine["llm"] = LLM(model=cfg.model, max_model_len=cfg.max_model_len,
                                gpu_memory_utilization=cfg.gpu_memory_utilization)
        from vllm import SamplingParams
        llm = engine["llm"]
        texts = _as_chat(llm.get_tokenizer(), prompts)
        outs = llm.generate(texts, SamplingParams(temperature=cfg.temperature,
                                                  top_p=cfg.top_p,
                                                  max_tokens=cfg.max_new_tokens))
        return [o.outputs[0].text.strip() for o in outs]

    return generate


def hf_generator(cfg: GenerationConfig) -> Generator:
    """Fallback for when vLLM cannot hold the device (shared GPU, odd base).

    Pads on the LEFT. A decoder-only model continues from the last position, so
    right-padding a batch makes every short prompt generate from pad tokens —
    which does not crash, it just quietly returns worse text. That is exactly the
    silent degradation the ok-rate gate exists to catch, and it should not be
    coming from our own batching.
    """
    state = {}

    def generate(prompts: Sequence[str]) -> list[str]:
        if not prompts:
            return []
        if "model" not in state:
            import torch
            from transformers import AutoModelForImageTextToText, AutoTokenizer
            state["torch"] = torch
            tokenizer = AutoTokenizer.from_pretrained(cfg.model, padding_side="left")
            if tokenizer.pad_token_id is None:
                tokenizer.pad_token = tokenizer.eos_token
            state["tok"] = tokenizer
            state["model"] = AutoModelForImageTextToText.from_pretrained(
                cfg.model, dtype=torch.bfloat16, device_map=cfg.device).eval()
        torch, tok, model = state["torch"], state["tok"], state["model"]
        out: list[str] = []
        for i in range(0, len(prompts), cfg.batch_size):
            batch = _as_chat(tok, prompts[i:i + cfg.batch_size])
            enc = tok(batch, return_tensors="pt", padding=True, truncation=True,
                      max_length=cfg.max_model_len).to(cfg.device)
            with torch.no_grad():
                gen = model.generate(**enc, max_new_tokens=cfg.max_new_tokens,
                                     do_sample=True, temperature=cfg.temperature,
                                     top_p=cfg.top_p, pad_token_id=tok.eos_token_id)
            out += [t.strip() for t in tok.batch_decode(
                gen[:, enc.input_ids.shape[1]:], skip_special_tokens=True)]
        return out

    return generate


def get_generator(backend: str, cfg: GenerationConfig, *,
                  stub: Generator | None = None) -> Generator:
    if backend == "vllm":
        return vllm_generator(cfg)
    if backend == "hf":
        return hf_generator(cfg)
    if backend == "stub":
        if stub is None:
            raise ValueError("amplify backend 'stub' requires an explicit generator")
        return stub
    raise ValueError(f"unknown amplify backend {backend!r}")
