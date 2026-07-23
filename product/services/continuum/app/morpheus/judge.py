"""Judged exact-fact recall — the metric the gate actually trusts.

`contains` can only fire when a gold answer is short enough to appear verbatim,
which is a handful of probes out of hundreds; it cannot tell two amplification
recipes apart. So an independent model reads each (question, gold, answer) triple
and rules on one narrow question: does the answer state the SAME SPECIFIC FACT?
Paraphrase passes. A wrong number, a vague gesture, or a refusal does not.

The judge is deliberately a DIFFERENT model family from both the amplifier and
the base being judged, and it never sees which run produced an answer.

Unjudgeable items (API failure after retries) are counted and EXCLUDED rather
than scored zero — silently converting an outage into a low recall number would
read as forgetting and could fail the gate on an infrastructure blip.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

PROMPT = (
    "You grade whether a model's ANSWER states the correct specific fact, using the GOLD "
    "reference answer.\n\nQUESTION: {q}\nGOLD: {gold}\nANSWER: {pred}\n\n"
    "Mark correct=1 ONLY if the ANSWER conveys the SAME specific fact as the GOLD — the key "
    "entity / number / color / name / quoted text matches (paraphrase, synonyms, extra detail "
    "are fine). Mark correct=0 if it states a WRONG specific (wrong number/color/name/etc.), is "
    "too vague/generic to contain the specific fact, refuses, or is off-topic.\n"
    'Reply with ONLY compact JSON: {{"correct": 0 or 1}}'
)

_VERDICT = re.compile(r'\{[^{}]*"correct"\s*:\s*([01])[^{}]*\}')

# Truncations keep a runaway generation from blowing the judge's context.
_MAX_Q, _MAX_GOLD, _MAX_PRED = 600, 400, 400


@dataclass(frozen=True)
class JudgeConfig:
    model: str = "vertex_ai/gemini-2.5-flash"
    project: str = ""
    location: str = "global"
    workers: int = 16
    retries: int = 3
    max_tokens: int = 60


def parse_verdict(text: str | None) -> int | None:
    match = _VERDICT.search(text or "")
    return int(match.group(1)) if match else None


def _judge_one(item: dict, cfg: JudgeConfig) -> int | None:
    import litellm
    litellm.drop_params = True
    prompt = PROMPT.format(q=str(item.get("q", ""))[:_MAX_Q],
                           gold=str(item.get("gold", ""))[:_MAX_GOLD],
                           pred=str(item.get("pred", ""))[:_MAX_PRED])
    for _ in range(cfg.retries):
        try:
            response = litellm.completion(
                model=cfg.model, vertex_project=cfg.project or None,
                vertex_location=cfg.location, reasoning_effort="disable",
                temperature=0, max_tokens=cfg.max_tokens,
                messages=[{"role": "user", "content": prompt}])
            verdict = parse_verdict(response.choices[0].message.content)
            if verdict is not None:
                return verdict
        except Exception:      # transient API/auth/quota — retry, then give up as unjudged
            continue
    return None


def judge_items(items: Sequence[dict], cfg: JudgeConfig, *,
                judge_one=None) -> list[int | None]:
    """Per-item verdicts, positionally aligned to `items`.

    `judge_one` is injectable so everything except the API call is testable
    offline — the aggregation is what every downstream readout is keyed on."""
    call = judge_one or (lambda item: _judge_one(item, cfg))
    with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
        return list(pool.map(call, items))


def judge(items: Sequence[dict], cfg: JudgeConfig, *, label: str = "",
          judge_one=None) -> dict[str, Any]:
    """Score a prediction set; return the suite-keyed summary."""
    return summarize(items, judge_items(items, cfg, judge_one=judge_one),
                     label=label, model=cfg.model)


def summarize(items: Sequence[dict], verdicts: Sequence[int | None], *,
              label: str = "", model: str = "") -> dict[str, Any]:
    by_suite: dict[str, list[int | None]] = {}
    for item, verdict in zip(items, verdicts):
        by_suite.setdefault(item.get("suite", "all"), []).append(verdict)
    summary: dict[str, Any] = {
        "label": label, "model": model, "n": len(items),
        "n_unjudged": sum(1 for v in verdicts if v is None)}
    scored: list[int] = []
    for suite, suite_verdicts in sorted(by_suite.items()):
        good = [v for v in suite_verdicts if v is not None]
        scored += good
        summary[suite] = {"n": len(good),
                          "judge_exact": round(sum(good) / max(1, len(good)), 4)}
    summary["judge_exact_micro"] = round(sum(scored) / max(1, len(scored)), 4)
    return summary


def write_scored(path: str | Path, items: Iterable[dict],
                 verdicts: Iterable[int | None]) -> None:
    """Per-item verdicts, for auditing a surprising aggregate."""
    with Path(path).open("w") as f:
        for item, verdict in zip(items, verdicts):
            f.write(json.dumps({k: item.get(k) for k in ("suite", "probe_id", "gold", "pred")}
                               | {"judge": verdict}) + "\n")
