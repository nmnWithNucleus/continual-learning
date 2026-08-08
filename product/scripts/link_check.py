#!/usr/bin/env python3
"""Internal markdown link checker: resolves file targets AND section anchors.

    python3 product/scripts/link_check.py            # every tracked *.md
    python3 product/scripts/link_check.py product    # one subtree
    python3 product/scripts/link_check.py --list     # also print the files walked

A document that points at a heading which no longer exists is worse than one
with no pointer at all: the reader follows it, lands somewhere plausible, and
believes they read the thing. Renaming a heading is the single most common way
to break a cross-reference, so this checks the fragment as well as the file --
`[C10](../ARCHITECTURE.md#contracts)` fails if `## Contracts` was retitled.

ANCHORS follow GitHub's rule, which this reimplements because there is no
authority to ask: lowercase the heading, drop everything that is not a letter,
digit, space, hyphen or underscore, then turn spaces into hyphens. Two details
cost real debugging the first time round:

  * Markdown emphasis markers are stripped BEFORE the character filter (`` ` ``,
    `*`, `~`) -- but NOT `_`, which is a legal anchor character. Stripping it
    turns `#d15--dp-imagetext-pipelines-deferred` into a false failure.
  * A heading that is itself a link contributes only its link TEXT.

Explicit `<a name=...>` / `<a id=...>` targets are honoured too.

Walks `git ls-files`, so untracked scratch files and virtualenv documentation
cannot fail the gate. Exit status is 1 when anything is broken, so it can sit in
CI beside style_check.py.
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

LINK = re.compile(r'(?<!\!)\[([^\]]*)\]\(([^)\s]+)\)')
HEAD = re.compile(r'^(#{1,6})\s+(.*?)\s*$', re.M)
EXTERNAL = ("http://", "https://", "mailto:", "tel:", "#!")


def anchors_of(path: pathlib.Path) -> set[str]:
    """Every fragment this file can be entered at."""
    try:
        txt = path.read_text(errors="replace")
    except OSError:
        return set()
    out = set()
    for _, title in HEAD.findall(txt):
        a = title.strip().lower()
        a = re.sub(r'[`*~]', '', a)                      # emphasis, NOT '_'
        a = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', a)   # a linked heading -> its text
        a = re.sub(r'[^a-z0-9 \-_]', '', a)
        out.add(a.replace(' ', '-'))
    out |= set(re.findall(r'<a\s+(?:name|id)="([^"]+)"', txt))
    return out


def tracked_markdown(prefix: str) -> list[pathlib.Path]:
    out = subprocess.run(["git", "ls-files", "--", prefix or "."],
                         cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / f for f in out.stdout.splitlines() if f.endswith(".md")]


def main() -> int:
    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    files = tracked_markdown(argv[0] if argv else "")
    if "--list" in sys.argv:
        for f in files:
            print(f"  walked {f.relative_to(ROOT)}")

    cache: dict[pathlib.Path, set[str]] = {}
    bad = []
    for p in files:
        txt = p.read_text(errors="replace")
        for m in LINK.finditer(txt):
            text, tgt = m.group(1), m.group(2)
            if tgt.startswith(EXTERNAL):
                continue
            line = txt[:m.start()].count("\n") + 1
            file_part, _, frag = tgt.partition("#")
            target = (p.parent / file_part).resolve() if file_part else p
            if file_part and not target.exists():
                bad.append((p, line, tgt, "missing file", text))
                continue
            if not frag:
                continue
            if target.is_dir():
                bad.append((p, line, tgt, "anchor into a directory", text))
                continue
            if target not in cache:
                cache[target] = anchors_of(target)
            if frag not in cache[target]:
                bad.append((p, line, tgt, "missing anchor", text))

    rel = lambda q: str(q.relative_to(ROOT))
    for p, line, tgt, why, text in sorted(bad, key=lambda x: (rel(x[0]), x[1])):
        print(f"{rel(p)}:{line}  [{text[:38]}] -> {tgt}   ({why})")
    print(f"\n{len(files)} files walked · broken internal links: {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
