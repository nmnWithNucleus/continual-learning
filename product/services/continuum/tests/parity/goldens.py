"""Where the Phase-1 goldens live, and the bands derived from them.

The parity harness is a DIFFERENTIAL test suite: nothing here asserts against a
number typed by hand where a measured artifact exists. Bands are computed from
the reference seed ensemble on disk, so they cannot drift away from what was
actually measured — and `assert_bands_match_spec()` catches the opposite failure,
a golden directory that quietly changed underneath us.

Reference runs (Qwen3-VL-8B, 6 days, replay arm, frac 0.30, matched compute):
  replay_f30 / _s1 / _s2   seeds 0/1/2 of the reference chain
  repro_replay_f30         our Phase-1 reproduction on our infra

NOTE the base model. These goldens are 8B. Production trains 32B adapters
(the adapter must match the served base), and 32B==8B on identical probes is a
measured TIE — consolidation is write-bound, not capacity-bound. So E2E parity
runs on 8B, where the numbers to match exist; the 32B chain is a separate
production concern proven in 2b.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from app.morpheus.eval import Band, Readout, readout

GOLDEN_ROOT = Path(os.getenv("MORPHEUS_GOLDEN_ROOT", "~/engram")).expanduser()
PHASED = GOLDEN_ROOT / "results" / "phased"
BLOCKS_DIR = GOLDEN_ROOT / "data" / "corpus"
NARRATIVE_DIR = GOLDEN_ROOT / "data" / "narrative"
PROBES_DIR = GOLDEN_ROOT / "data" / "probes_merged"
DESCRIPTIONS_DIR = Path(
    os.getenv("MORPHEUS_DESCRIPTIONS", "~/speed_lora/data/descriptions/5min")).expanduser()

DAYS = (5, 9, 12, 13, 17, 21)
HELDOUT_DAYS = (6, 16, 28)
SEED_ENSEMBLE = ("replay_f30", "replay_f30_s1", "replay_f30_s2")
REPRODUCTION = "repro_replay_f30"

BASE_MODEL = "Qwen/Qwen3-VL-8B-Instruct"

# Stream seeds of the reference chain. Amplification decorrelates days as
# `AMPLIFY_SEED + day`; the replay sampler draws from ONE stream seeded once per
# chain, so its selections depend on every prior day's draw.
AMPLIFY_SEED = 13
REPLAY_SEED = 7

# Recipe v1.0, as the goldens were produced.
VARIANTS = 48
NEG_FRAC = 0.15
REPLAY_FRAC = 0.30
SEQ_LEN = 1024
BATCH_SIZE = 2
EPOCHS = 3
HELDOUT_CEILING = 0.05


def blocks_path(day: int) -> Path:
    return BLOCKS_DIR / f"day{day}.blocks.jsonl"


def narrative_path(day: int) -> Path:
    return NARRATIVE_DIR / f"day{day}_x48neg.jsonl"


def corpus_path(day: int) -> Path:
    return NARRATIVE_DIR / f"day{day}_x48neg.corpus.txt"


def amplify_stats(day: int) -> dict:
    return json.loads((NARRATIVE_DIR / f"day{day}_x48neg.jsonl.stats.json").read_text())


def train_report(run: str) -> dict:
    return json.loads((PHASED / run / "train_report.json").read_text())


def judged(run: str) -> dict:
    return json.loads((PHASED / run / "judge.json").read_text())


def scored_rows(run: str) -> list[dict]:
    path = PHASED / run / "judge.json.scored.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def prediction_rows(run: str) -> list[dict]:
    path = PHASED / run / "preds.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@lru_cache(maxsize=None)
def golden_readout(run: str) -> Readout:
    return readout(judged(run), DAYS, label=run, predictions=prediction_rows(run))


def reference_band() -> Band:
    """The envelope the reference chain itself occupies across seeds.

    A single run landing on the ensemble MEAN would prove nothing — the chain's
    own separation spread is ~0.09 wide, which is wider than most effects anyone
    would try to measure with it. In-band membership is the only honest test."""
    readouts = [golden_readout(run) for run in SEED_ENSEMBLE]
    seen = [r.seen_mean for r in readouts]
    separation = [r.separation for r in readouts]
    micro = [r.micro for r in readouts]
    return Band(seen_mean=(min(seen), max(seen)),
                separation=(min(separation), max(separation)),
                micro=(min(micro), max(micro)),
                heldout_max=HELDOUT_CEILING)


def assert_bands_match_spec() -> None:
    """Tripwire on the goldens themselves.

    If the reference directory is replaced, moved, or partially re-judged, every
    downstream 'in-band' verdict silently changes meaning. These are the numbers
    the port was commissioned against (ws-morpheus-port §2)."""
    band = reference_band()
    assert band.separation == (0.1778, 0.2694), (
        f"reference separation spread is {band.separation}, expected (0.1778, 0.2694) — "
        f"the goldens under {PHASED} are not the ones this port was specified against")
    assert band.micro == (0.152, 0.1829), f"reference micro spread is {band.micro}"


def goldens_present() -> bool:
    return (BLOCKS_DIR.is_dir() and NARRATIVE_DIR.is_dir()
            and all((PHASED / run / "judge.json").exists() for run in SEED_ENSEMBLE))
