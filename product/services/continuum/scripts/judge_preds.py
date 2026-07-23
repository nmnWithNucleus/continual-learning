#!/usr/bin/env python
"""Judge a prediction set. Runs in the JUDGE env (litellm + Vertex ADC), alone.

Kept a separate process on purpose: the judge environment and the training
environment cannot be merged, and the boundary between them is a process
boundary invoked by absolute interpreter path — never a shell activation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings                                    # noqa: E402
from app.morpheus.judge import JudgeConfig, judge_items, summarize, write_scored  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--preds", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    settings = get_settings().morpheus
    items = [json.loads(line) for line in
             Path(args.preds).read_text().splitlines() if line.strip()]
    config = JudgeConfig(model=settings.judge_model, project=settings.vertex_project,
                         location=settings.vertex_location, workers=settings.judge_workers)
    print(f"judging {len(items)} predictions ({args.label}) with {config.model}", flush=True)

    verdicts = judge_items(items, config)
    summary = summarize(items, verdicts, label=args.label, model=config.model)
    write_scored(args.out + ".scored.jsonl", items, verdicts)
    Path(args.out).write_text(json.dumps(summary, indent=1))

    if summary["n_unjudged"]:
        # Loud, not fatal: unjudged items are excluded from the aggregate rather
        # than scored zero, but a large count means the number is thin.
        print(f"WARNING: {summary['n_unjudged']}/{summary['n']} items went unjudged",
              file=sys.stderr)
    print(json.dumps({k: v for k, v in summary.items() if not isinstance(v, dict)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
