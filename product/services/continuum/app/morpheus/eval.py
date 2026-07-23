"""Eval driver — measuring what a night actually wrote, and what it cost.

Recall alone is not a verdict. A night that writes today perfectly while erasing
last week is a regression, and a night that "remembers" days it never saw is
contaminated. So every consolidation step evaluates:

  every day consolidated SO FAR  -> the decay matrix (writing vs forgetting)
  traps                          -> calibration (did it learn to refuse?)
  heldout days                   -> contamination floor (should never rise)
  the same probes on the BASE    -> the floor everything is measured against

Suite keys (`s{step}_d{day}`, `s{step}_traps`, `base_d{day}`, `base_heldout`,
`final_heldout`) are the schema the judged summaries and every historical golden
share; the readouts below are defined directly on them.

The headline number is SEPARATION — seen-day recall minus heldout recall — not
raw recall. Raw recall flatters a model that has simply become more talkative;
separation is the part that can only come from having read the day.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Sequence

from .probes import Probe
from .scorers import trap_score

TRAP_ANSWER_TOKENS = 64      # refusals need room to say why; recall answers do not
RECALL_ANSWER_TOKENS = 48

# Harness SIZING, not recipe knobs: these bound how much eval a night pays for
# and do not touch the artifact. They match the reference chain so cell-by-cell
# comparison against the goldens is like-for-like.
PROBES_PER_DAY = 60
TRAPS_LIMIT = 50
HELDOUT_LIMIT = 60


def day_suite(step: int, day: int) -> str:
    return f"s{step}_d{day}"


def traps_suite(step: int) -> str:
    return f"s{step}_traps"


class Predictions:
    """Append-only prediction log — the judge's input and the audit trail."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("w")

    def write(self, suite: str, probe: Probe, prediction: str) -> None:
        self._file.write(json.dumps({
            "suite": suite, "probe_id": probe.probe_id, "q": probe.question,
            "gold": probe.gold, "pred": prediction}) + "\n")

    def flush(self) -> None:
        self._file.flush()

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> "Predictions":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def answer_suite(adapter, probes: Sequence[Probe], suite: str, out: Predictions, *,
                 max_new_tokens: int = RECALL_ANSWER_TOKENS) -> None:
    for probe in probes:
        out.write(suite, probe, adapter.answer(probe.question, max_new_tokens=max_new_tokens))
    out.flush()


def base_floor(adapter, *, day_probes: dict[int, list[Probe]],
               heldout: Sequence[Probe], out: Predictions) -> None:
    """The same probes with the adapter switched OFF.

    Without this floor a recall number is unreadable: some questions a strong
    base model can already answer from the open web, and that is not memory."""
    with adapter.base_only():
        for day, probes in day_probes.items():
            answer_suite(adapter, probes, f"base_d{day}", out)
        answer_suite(adapter, heldout, "base_heldout", out)


# --------------------------------------------------------------------------- readouts

def decay_matrix(judged: dict[str, Any], days: Sequence[int]) -> dict[tuple[int, int], float]:
    """M[(step, day)] = judged recall of `day`, measured after step `step`.

    Lower-triangular by construction: a day has no cell before it was written."""
    matrix: dict[tuple[int, int], float] = {}
    for step in range(len(days)):
        for day in days[:step + 1]:
            value = judged.get(day_suite(step, day), {}).get("judge_exact")
            if value is not None:
                matrix[(step, day)] = value
    return matrix


def trap_rates(predictions: Iterable[dict], steps: int) -> dict[int, float]:
    rows = list(predictions)
    rates: dict[int, float] = {}
    for step in range(steps):
        traps = [r for r in rows if r.get("suite") == traps_suite(step)]
        if traps:
            rates[step] = round(mean(trap_score("", r["pred"]) for r in traps), 3)
    return rates


@dataclass(frozen=True)
class Readout:
    """One chain's numbers — the row of the seed-ensemble table."""
    label: str
    days: list[int]
    final_per_day: dict[int, float]
    base_per_day: dict[int, float]
    seen_mean: float
    base_mean: float
    heldout: float | None
    base_heldout: float | None
    micro: float | None
    retention_per_day: dict[int, float] = field(default_factory=dict)
    retention_longest_path: float | None = None
    retention_micro: float | None = None
    traps_by_step: dict[int, float] = field(default_factory=dict)

    @property
    def separation(self) -> float | None:
        """Seen-day recall minus heldout recall — the headline.

        Heldout days are same-distribution days the adapter never trained on, so
        this subtracts off everything that is not day-specific memory."""
        return None if self.heldout is None else round(self.seen_mean - self.heldout, 4)

    def as_row(self) -> dict[str, Any]:
        return {"label": self.label, "seen_mean": round(self.seen_mean, 4),
                "separation": self.separation, "micro": self.micro,
                "heldout": self.heldout, "base_mean": round(self.base_mean, 4),
                "retention_longest_path": self.retention_longest_path,
                "traps_final": self.traps_by_step.get(len(self.days) - 1)}


def readout(judged: dict[str, Any], days: Sequence[int], *, label: str = "",
            predictions: Iterable[dict] | None = None) -> Readout:
    matrix = decay_matrix(judged, days)
    final = len(days) - 1
    step_of = {day: i for i, day in enumerate(days)}

    final_per_day = {d: matrix[(final, d)] for d in days if (final, d) in matrix}
    base_per_day = {d: judged[f"base_d{d}"]["judge_exact"] for d in days
                    if f"base_d{d}" in judged}

    # Retention = recall now / recall when first written. A day whose baseline was
    # 0 is skipped: 0/0 is not "forgotten", it was never written in the first place.
    retention = {d: matrix[(final, d)] / matrix[(step_of[d], d)]
                 for d in days
                 if (final, d) in matrix and matrix.get((step_of[d], d), 0) > 1e-6}
    # Pooled over every DECAYED cell, not a ratio of means — one day with a tiny
    # denominator would otherwise dominate the average.
    decayed = [v / matrix[(step_of[d], d)] for (step, d), v in matrix.items()
               if step > step_of[d] and matrix.get((step_of[d], d), 0) > 1e-6]

    return Readout(
        label=label, days=list(days),
        final_per_day=final_per_day, base_per_day=base_per_day,
        seen_mean=mean(final_per_day.values()) if final_per_day else 0.0,
        base_mean=mean(base_per_day.values()) if base_per_day else 0.0,
        heldout=judged.get("final_heldout", {}).get("judge_exact"),
        base_heldout=judged.get("base_heldout", {}).get("judge_exact"),
        micro=judged.get("judge_exact_micro"),
        retention_per_day={d: round(v, 3) for d, v in retention.items()},
        # The FIRST day after every later consolidation — the longest decay path,
        # and the only honest single-number retention headline. (The last day is
        # 1.0 by construction; averaging it in inflates the mean.)
        retention_longest_path=round(retention[days[0]], 3) if days[0] in retention else None,
        retention_micro=round(mean(decayed), 3) if decayed else None,
        traps_by_step=trap_rates(predictions or [], len(days)))


# Judged recall is published to four decimals, so every band edge is quantized at
# that scale. Comparing tighter than the numbers are reported would make a run
# fail on rounding noise — five orders of magnitude below the ~0.09-wide spread
# these bands actually describe.
JUDGE_PRECISION = 1e-4


@dataclass(frozen=True)
class Band:
    """The in-band envelope a port has to land inside, from the seed ensemble.

    Point estimates are meaningless here — the reference chain's own seed spread
    on separation is ~0.09 wide. A port matches when it lands INSIDE the spread,
    and a single run that happens to hit the mean proves nothing."""
    seen_mean: tuple[float, float]
    separation: tuple[float, float]
    micro: tuple[float, float]
    heldout_max: float
    eps: float = JUDGE_PRECISION

    def _within(self, value: float | None, bounds: tuple[float, float]) -> bool:
        return value is not None and bounds[0] - self.eps <= value <= bounds[1] + self.eps

    def check(self, r: Readout) -> dict[str, bool]:
        return {
            "seen_mean": self._within(r.seen_mean, self.seen_mean),
            "separation": self._within(r.separation, self.separation),
            "micro": self._within(r.micro, self.micro),
            "heldout": r.heldout is not None and r.heldout <= self.heldout_max + self.eps,
        }


def ensemble_table(readouts: Sequence[Readout]) -> str:
    header = (f"{'run':<22}{'seen':>8}{'sep':>9}{'micro':>8}"
              f"{'heldout':>9}{'base':>8}{'ret(d0)':>9}{'traps':>7}")
    lines = [header, "-" * len(header)]
    for r in readouts:
        row = r.as_row()
        lines.append(
            f"{row['label']:<22}{row['seen_mean']:>8.4f}"
            f"{(row['separation'] if row['separation'] is not None else float('nan')):>9.4f}"
            f"{(row['micro'] if row['micro'] is not None else float('nan')):>8.4f}"
            f"{(row['heldout'] if row['heldout'] is not None else float('nan')):>9.4f}"
            f"{row['base_mean']:>8.4f}"
            f"{(row['retention_longest_path'] if row['retention_longest_path'] is not None else float('nan')):>9.3f}"
            f"{(row['traps_final'] if row['traps_final'] is not None else float('nan')):>7.2f}")
    return "\n".join(lines)
