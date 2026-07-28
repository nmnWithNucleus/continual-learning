#!/usr/bin/env python3
"""Mechanical STYLE.md checker for product/*.md.

Checks only what STYLE.md's own §Self-check can check by reading the output.
Anything needing judgement stays with a reviewer.

    python3 product/scripts/style_check.py              # fail on new findings only
    python3 product/scripts/style_check.py --all        # list every finding
    python3 product/scripts/style_check.py --baseline   # rewrite the baseline

The baseline is a ratchet: today's known findings are recorded so the check
fails only on findings we did not already have. Shrink it, never grow it.
"""
import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "style_baseline.json"

# Rule 5: ALL-CAPS is forbidden, but these are names, acronyms, or the two
# status words DECISIONS.md capitalises on purpose.
CAPS_OK = {
    "ARCHITECTURE", "HANDOFF", "DECISIONS", "PROMPTS", "STACK", "VISION",
    "CHARTER", "README", "STYLE", "HTTP", "HTTPS", "JSON", "NULL", "IANA",
    "CUDA", "POST", "SLURM", "ASCII", "ASGI", "HTML", "CORS", "TODO",
    "BUILT", "RETIRED", "PROTOTYPE", "CAPS",
}
# Rule 6: reasoning sections get 60 words, everything else 25 (27 with slack).
REASONING = {"Why it's this way", "Watch out for", "How it got here"}
WORKLOG_ENTRY = re.compile(r"^### \d{4}-\d{2}-\d{2}")
LEARNINGS = re.compile(r"^## Learnings")
# Rule 7 + DECISIONS §Stage: the only words allowed in a status position.
STATUS_WORDS = {"designed", "built", "ratified", "BUILT", "superseded",
                "demoted", "RETIRED", "active", "seeded"}


def findings(path):
    """Yield (line_no, rule, detail) for one markdown file."""
    out = []
    lines = path.read_text().split("\n")
    in_fence = False
    section = None      # nearest **Bold heading** above, for the rule-6 cap
    in_worklog_entry = False
    bullet = None       # [line_no, joined_text, section, worklog?]
    bullets = []

    for i, line in enumerate(lines, 1):
        if re.match(r"^\s*(```|````)", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        # Rule 10: quoted material keeps its original wording; skip it.
        if line.startswith(">"):
            continue

        stripped = line.strip()
        if WORKLOG_ENTRY.match(stripped) or LEARNINGS.match(stripped):
            in_worklog_entry = True
        elif stripped.startswith("## "):
            in_worklog_entry = False
        if stripped.startswith("#"):
            section = None

        # A bold-only line is a section heading; a list item that opens with a
        # bold span is not. Match on the list marker, not on the leading "*",
        # which a "**Why it's this way**" heading also starts with.
        m = re.match(r"^\*\*(.+?)\*\*\s*(—|$)", stripped)
        if m and not re.match(r"^([-*+]|\d+[.)])\s", stripped):
            section = m.group(1).rstrip(".")

        # accumulate wrapped bullets into one logical bullet
        if re.match(r"^([-*]|\d+\.)\s", stripped):
            if bullet:
                bullets.append(bullet)
            bullet = [i, stripped, section, in_worklog_entry]
        elif (bullet and stripped and not stripped.startswith(("|", "#", ">"))
              and re.match(r"^\s{2,}\S", line)
              and not re.match(r"^\s*([-*]|\d+\.)\s", line)):
            bullet[1] += " " + stripped
        elif bullet:
            bullets.append(bullet)
            bullet = None

        # Rule 1 + rule 5: table cells
        if stripped.startswith("|") and not re.match(r"^\|[\s\-:|]+\|$", stripped):
            for cell in stripped.split("|")[1:-1]:
                if len(cell.split()) > 20:
                    out.append((i, "rule1-cell", f"{len(cell.split())} words"))
                if cell.count("**") // 2 > 1:
                    out.append((i, "rule5-cell-bold", cell.strip()[:40]))
        # Rule 6: one em-dash per sentence
        elif line.count("—") > 1:
            out.append((i, "rule6-em-dash", stripped[:44]))

        # Rule 5: no ALL-CAPS, outside headings and inline code
        if not line.startswith("#"):
            for w in re.findall(r"\b[A-Z]{4,}\b", re.sub(r"`[^`]*`", "", line)):
                if w not in CAPS_OK:
                    out.append((i, "rule5-allcaps", w))

    if bullet:
        bullets.append(bullet)

    for ln, text, sect, worklog in bullets:
        cap = 60 if (sect in REASONING or worklog) else 27
        if len(text.split()) > cap:
            out.append((ln, "rule6-bullet", f"{len(text.split())}w > {cap}"))
        if text.count("**") // 2 > 1:
            out.append((ln, "rule5-bullet-bold", text[:40]))

    # Rule 5: at most two bold spans per paragraph
    body = re.sub(r"```.*?```", "", path.read_text(), flags=re.S)
    for para in re.split(r"\n\s*\n", body):
        head = para.strip()
        if not head or head.startswith(("-", "*", "|", ">", "#")):
            continue
        if para.count("**") // 2 > 2:
            out.append((0, "rule5-para-bold", head[:40]))

    return out


# Ours to hold to a standard; vendored and generated trees are not.
SKIP_DIRS = {".review", ".venv", "venv", "node_modules", "site-packages",
             ".git", "__pycache__", ".pytest_cache", "dist-info"}


def collect():
    all_found = {}
    for path in sorted(ROOT.rglob("*.md")):
        if any(p in SKIP_DIRS or p.endswith(".dist-info") for p in path.parts):
            continue
        rel = str(path.relative_to(ROOT.parent))
        found = findings(path)
        if found:
            all_found[rel] = found
    return all_found


def key(rel, f):
    return f"{rel}::{f[1]}::{f[2]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="list every finding")
    ap.add_argument("--baseline", action="store_true", help="rewrite the baseline")
    args = ap.parse_args()

    found = collect()

    if args.baseline:
        counts = {rel: len(fs) for rel, fs in found.items()}
        BASELINE.write_text(json.dumps(
            {"note": "Ratchet for style_check.py. Shrink it, never grow it.",
             "counts": counts}, indent=2) + "\n")
        print(f"baseline written: {sum(counts.values())} findings across {len(counts)} files")
        return 0

    total = sum(len(f) for f in found.values())
    if args.all:
        for rel, fs in found.items():
            for ln, rule, detail in fs:
                print(f"{rel}:{ln}: {rule}: {detail}")
        print(f"\n{total} findings")
        return 0

    base = json.loads(BASELINE.read_text())["counts"] if BASELINE.exists() else {}
    regressions = []
    for rel, fs in found.items():
        allowed = base.get(rel, 0)
        if len(fs) > allowed:
            regressions.append((rel, len(fs), allowed))

    if not regressions:
        print(f"STYLE.md: no regressions ({total} known findings held at baseline)")
        return 0

    print("STYLE.md check failed — these files gained findings:\n")
    for rel, now, allowed in regressions:
        print(f"  {rel}: {now} findings, baseline {allowed}")
        for ln, rule, detail in findings(ROOT.parent / rel)[:8]:
            print(f"      {rel}:{ln}: {rule}: {detail}")
    print("\nFix them, or run --baseline if the finding is deliberate and agreed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
