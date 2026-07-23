"""Continued pre-training — the night's actual write.

Plain next-token CPT on the amplified corpus, into a LoRA on the frozen base.
Never QA-format SFT: full-finetune SFT on question/answer pairs gave literally
zero separation from the base model on this data, and that failure is why the
recipe looks the way it does. The model must read the day as prose, not as a
quiz about the day.

One adapter, forever. Each night continues the SAME LoRA rather than spawning a
per-day one, because per-day adapters have to be merged and every merge method
tested lost to sequential consolidation with rehearsal.

Matched compute is load-bearing: rehearsal DISPLACES new-day chunks within a
fixed per-epoch step budget instead of adding steps. Without it, a 30%-rehearsal
night takes 30% more gradient steps than a no-rehearsal night and the comparison
measures compute, not rehearsal.
"""
from __future__ import annotations

import contextlib
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Sequence

# The LLM projection set. VISION towers are deliberately excluded: the day log
# reaches the model as text, so adapting the vision stack spends rank on modules
# that never see the training signal.
LM_PROJECTIONS = frozenset({"q_proj", "k_proj", "v_proj", "o_proj",
                            "gate_proj", "up_proj", "down_proj"})
_LM_SCOPE = "language_model"

IM_START, IM_END = "<|im_start|>", "<|im_end|>"

# Chunks shorter than this are the ragged tail of the corpus; training on a
# handful of tokens is pure noise in the loss.
MIN_CHUNK_TOKENS = 16


def _log(message: str) -> None:
    """Flushed: a night runs for hours into a redirected log, and unflushed
    progress means nobody can tell a slow run from a hung one."""
    print(message, flush=True)


@dataclass(frozen=True)
class LoraSpec:
    r: int = 128
    alpha: int = 256
    dropout: float = 0.0
    bias: str = "none"


@dataclass(frozen=True)
class CptConfig:
    epochs: int = 3
    seq_len: int = 1024
    batch_size: int = 2
    lr: float = 1e-4
    grad_clip: float = 1.0
    max_chunks: int | None = None   # matched-compute per-epoch budget
    log_every: int = 100


@dataclass(frozen=True)
class TrainStats:
    chunks: int
    chunks_per_epoch: int
    steps: int
    loss_first: float
    loss_last: float
    seconds: float
    extras: dict = field(default_factory=dict)


def chunk_corpus(tokenizer, text: str, seq_len: int) -> list[list[int]]:
    """Tokenize the corpus and slice it into fixed-length training chunks.

    Flat, non-overlapping slices over the whole concatenated corpus — paragraph
    boundaries are not respected, which is the point: CPT learns the facts, not
    the paragraph packaging."""
    ids = tokenizer(text, add_special_tokens=False).input_ids
    return [chunk for chunk in
            (ids[i:i + seq_len] for i in range(0, len(ids) - 1, seq_len))
            if len(chunk) > MIN_CHUNK_TOKENS]


def lora_target_modules(model) -> list[str]:
    """Every LLM projection Linear, by fully-qualified name."""
    import torch.nn as nn
    names = [name for name, mod in model.named_modules()
             if isinstance(mod, nn.Linear)
             and name.split(".")[-1] in LM_PROJECTIONS
             and _LM_SCOPE in name]
    if not names:
        raise RuntimeError(
            f"no {_LM_SCOPE!r} LoRA targets in {type(model).__name__} — the base model's "
            "module naming changed; adapting zero modules would train nothing silently")
    return names


def matched_compute_budget(tokenizer, new_day_text: str, seq_len: int) -> int:
    """The per-epoch chunk budget: what the new day ALONE would have cost.

    Computed before rehearsal is mixed in, so rehearsal displaces rather than adds."""
    return len(chunk_corpus(tokenizer, new_day_text, seq_len))


class LifeAdapter:
    """The one life adapter, open for a night's work.

    Holds the frozen base plus the live LoRA. `open()` either starts a fresh
    adapter (night one) or continues last night's from disk — the research chain
    keeps one process alive across days, production runs one process per night;
    both land on identical weights because the optimizer is rebuilt per day
    either way.
    """

    def __init__(self, model, tokenizer, device: str):
        self.model = model
        self.tokenizer = tokenizer
        self.device = device
        self.lora_params = [p for n, p in model.named_parameters() if "lora_" in n]
        if not self.lora_params:
            raise RuntimeError("adapter has no trainable LoRA parameters")

    @classmethod
    def open(cls, *, base_model: str, device: str = "cuda:0",
             resume_adapter: str | Path | None = None, lora: LoraSpec | None = None,
             seed: int | None = None, shard_gpus: int = 0,
             shard_max_memory: str = "76GiB",
             grad_checkpointing: bool = False) -> "LifeAdapter":
        import torch
        from peft import LoraConfig, PeftModel, get_peft_model
        from transformers import AutoModelForImageTextToText, AutoTokenizer, set_seed

        if seed is not None:
            set_seed(seed)     # fixes LoRA-A init; only matters on a fresh adapter
        tokenizer = AutoTokenizer.from_pretrained(base_model)
        load = dict(dtype=torch.bfloat16, attn_implementation="sdpa")
        if shard_gpus:
            # 32B in bf16 is ~66GB and does NOT fit one 80GB card for CPT at any
            # batch size — measured, OOM at the first forward. The per-card budget
            # is configurable because on a shared node we do not own the whole card.
            base = AutoModelForImageTextToText.from_pretrained(
                base_model, **load, device_map="auto",
                max_memory={i: shard_max_memory for i in range(shard_gpus)})
        else:
            base = AutoModelForImageTextToText.from_pretrained(base_model, **load).to(device)
        for p in base.parameters():
            p.requires_grad_(False)

        if resume_adapter:
            model = PeftModel.from_pretrained(base, str(resume_adapter), is_trainable=True)
        else:
            spec = lora or LoraSpec()
            model = get_peft_model(base, LoraConfig(
                r=spec.r, lora_alpha=spec.alpha, target_modules=lora_target_modules(base),
                lora_dropout=spec.dropout, bias=spec.bias))
        if grad_checkpointing:
            model.gradient_checkpointing_enable()
            model.enable_input_require_grads()   # PEFT + frozen base + checkpointing
            model.config.use_cache = False
        return cls(model, tokenizer, device)

    def chunk(self, text: str, seq_len: int) -> list[list[int]]:
        return chunk_corpus(self.tokenizer, text, seq_len)

    def train_on(self, text: str, cfg: CptConfig, *, tag: str = "cpt",
                 progress=_log) -> TrainStats:
        """Continue-CPT one night's corpus into the live adapter."""
        import torch
        import torch.nn.functional as F

        chunks = self.chunk(text, cfg.seq_len)
        if not chunks:
            raise ValueError("corpus produced no trainable chunks")
        per_epoch = min(len(chunks), cfg.max_chunks) if cfg.max_chunks else len(chunks)
        opt = torch.optim.AdamW(self.lora_params, lr=cfg.lr, weight_decay=0.0)
        # Fixed shuffle stream: the chunk order a night sees depends only on the
        # corpus, never on when it ran.
        rng = random.Random(0)
        self.model.train()
        started, step, losses = time.time(), 0, []
        pad = self.tokenizer.eos_token_id
        for epoch in range(cfg.epochs):
            rng.shuffle(chunks)
            # Fresh budget-sized subset each epoch: over 3 epochs the whole corpus
            # is still seen, but no epoch exceeds the matched-compute budget.
            epoch_chunks = chunks[:per_epoch]
            for i in range(0, len(epoch_chunks) - cfg.batch_size + 1, cfg.batch_size):
                batch = epoch_chunks[i:i + cfg.batch_size]
                width = max(len(c) for c in batch)
                ids = torch.full((len(batch), width), pad, device=self.device)
                mask = torch.zeros((len(batch), width), device=self.device, dtype=torch.long)
                for j, chunk in enumerate(batch):
                    ids[j, :len(chunk)] = torch.tensor(chunk, device=self.device)
                    mask[j, :len(chunk)] = 1
                out = self.model(input_ids=ids, attention_mask=mask)
                logits = out.logits[:, :-1].reshape(-1, out.logits.shape[-1]).float()
                target = ids[:, 1:].reshape(-1).clone()
                target[mask[:, 1:].reshape(-1) == 0] = -100   # never learn to predict padding
                loss = F.cross_entropy(logits, target, ignore_index=-100)
                opt.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.lora_params, cfg.grad_clip)
                opt.step()
                losses.append(loss.item())
                step += 1
                if cfg.log_every and step % cfg.log_every == 0:
                    progress(f"  [{tag}] step {step} loss {loss.item():.3f} "
                             f"{(time.time() - started) / 60:.1f}m")
        self.model.eval()
        window = min(20, len(losses))
        return TrainStats(chunks=len(chunks), chunks_per_epoch=per_epoch, steps=step,
                          loss_first=round(sum(losses[:window]) / window, 3),
                          loss_last=round(sum(losses[-window:]) / window, 3),
                          seconds=round(time.time() - started, 1))

    @contextlib.contextmanager
    def base_only(self) -> Iterator[None]:
        """Run with the adapter disabled — the base-model floor for every probe."""
        with self.model.disable_adapter():
            yield

    def answer(self, question: str, *, max_new_tokens: int = 48) -> str:
        """Closed-book answer: no context, no retrieval — only what the weights hold."""
        import torch
        prompt = f"{IM_START}user\n{question}{IM_END}\n{IM_START}assistant\n"
        ids = self.tokenizer(prompt, add_special_tokens=False,
                             return_tensors="pt").input_ids.to(self.device)
        with torch.no_grad():
            out = self.model.generate(input_ids=ids, max_new_tokens=max_new_tokens,
                                      do_sample=False,
                                      pad_token_id=self.tokenizer.eos_token_id)
        return self.tokenizer.decode(out[0, ids.shape[1]:], skip_special_tokens=True).strip()

    def answers(self, questions: Sequence[str], *, max_new_tokens: int = 48) -> list[str]:
        return [self.answer(q, max_new_tokens=max_new_tokens) for q in questions]

    def save(self, out_dir: str | Path) -> str:
        self.model.save_pretrained(str(out_dir))
        return str(out_dir)
