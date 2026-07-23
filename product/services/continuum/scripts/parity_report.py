#!/usr/bin/env python
"""Put our chains and the reference chains in one table, and rule on the port.

    python scripts/parity_report.py var/parity/morpheus_f30_s*

The verdict is IN-BAND membership against the reference seed ensemble, not
distance from a mean. The reference chain's own separation spread is ~0.09 wide;
a port that landed exactly on the reference mean would be reporting luck, and one
that sits anywhere inside the spread is indistinguishable from another seed of
the reference itself — which is the strongest claim this data can support.

Also reported, because they fail differently:
  heldout        a contamination tripwire, absolute, not relative to anything
  spread ratio   if OUR seeds agree far more tightly than theirs, we are probably
                 not varying what they varied
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.morpheus.eval import ensemble_table, readout                  # noqa: E402
from tests.parity import goldens                                       # noqa: E402


def load_run(path: Path):
    judged = json.loads((path / "judge.json").read_text())
    predictions = [json.loads(x) for x in
                   (path / "preds.jsonl").read_text().splitlines() if x.strip()]
    report = json.loads((path / "train_report.json").read_text())
    days = report.get("days", list(goldens.DAYS))
    return readout(judged, days, label=path.name, predictions=predictions), report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", help="our run directories")
    ap.add_argument("--out", default="", help="also write the verdict as JSON")
    args = ap.parse_args()

    goldens.assert_bands_match_spec()
    band = goldens.reference_band()
    reference = [goldens.golden_readout(r) for r in goldens.SEED_ENSEMBLE]
    reference.append(goldens.golden_readout(goldens.REPRODUCTION))

    ours, reports = [], {}
    for raw in args.runs:
        path = Path(raw)
        if not (path / "judge.json").exists():
            print(f"[skip] {path} has no judge.json (chain unfinished?)", file=sys.stderr)
            continue
        run_readout, report = load_run(path)
        ours.append(run_readout)
        reports[path.name] = report
    if not ours:
        raise SystemExit("no finished runs given")

    print("REFERENCE (Phase-1 goldens)")
    print(ensemble_table(reference))
    print("\nMORPHEUS PORT")
    print(ensemble_table(ours))

    print(f"\nIn-band envelope from the {len(goldens.SEED_ENSEMBLE)}-seed reference ensemble:")
    print(f"  seen_mean   {band.seen_mean[0]:.4f} .. {band.seen_mean[1]:.4f}")
    print(f"  separation  {band.separation[0]:.4f} .. {band.separation[1]:.4f}")
    print(f"  micro       {band.micro[0]:.4f} .. {band.micro[1]:.4f}")
    print(f"  heldout     <= {band.heldout_max}")

    verdict = {"runs": {}, "band": {"seen_mean": band.seen_mean,
                                    "separation": band.separation,
                                    "micro": band.micro,
                                    "heldout_max": band.heldout_max}}
    print("\nPER-RUN VERDICT")
    for run in ours:
        checks = band.check(run)
        verdict["runs"][run.label] = {"checks": checks, **run.as_row(),
                                      "wall_clock_hours": reports[run.label].get(
                                          "wall_clock_hours"),
                                      "grad_checkpointing": reports[run.label].get(
                                          "grad_checkpointing")}
        failed = [k for k, ok in checks.items() if not ok]
        print(f"  {run.label:<24}{'IN BAND' if not failed else 'OUT OF BAND: ' + ','.join(failed)}")

    ensemble_mean = mean(r.seen_mean for r in ours)
    ensemble_sep = [r.separation for r in ours if r.separation is not None]
    our_spread = max(ensemble_sep) - min(ensemble_sep) if len(ensemble_sep) > 1 else None
    their_spread = band.separation[1] - band.separation[0]
    verdict["ensemble"] = {
        "n_seeds": len(ours), "seen_mean": round(ensemble_mean, 4),
        "separation_mean": round(mean(ensemble_sep), 4) if ensemble_sep else None,
        "separation_spread": round(our_spread, 4) if our_spread is not None else None,
        "reference_separation_spread": round(their_spread, 4),
        "all_in_band": all(all(band.check(r).values()) for r in ours)}
    print(f"\nENSEMBLE  n={len(ours)}  seen_mean {ensemble_mean:.4f}  "
          f"separation {verdict['ensemble']['separation_mean']}  "
          f"spread {verdict['ensemble']['separation_spread']} "
          f"(reference {their_spread:.4f})")
    print(f"VERDICT: {'PARITY' if verdict['ensemble']['all_in_band'] else 'NOT IN BAND'}")

    if args.out:
        Path(args.out).write_text(json.dumps(verdict, indent=1))
    return 0 if verdict["ensemble"]["all_in_band"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
