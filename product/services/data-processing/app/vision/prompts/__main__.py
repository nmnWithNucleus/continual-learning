"""CLI entry: ``python -m app.vision.prompts <show|relock|status>...`` (, goal 4)."""
from __future__ import annotations

import sys


def _status() -> int:
    from . import PACK_DIGEST, PACK_VERSION, PROMPT_SOURCE, all_packs, pack_digest
    print(f"prompt_source={PROMPT_SOURCE}  pack_version={PACK_VERSION}  pack_digest={PACK_DIGEST}")
    for pid, spec in sorted(all_packs().items()):
        print(f"  {pid:<24} role={spec.role:<7} {pack_digest(spec)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python -m app.vision.prompts <show|relock|status> [...]", file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "show":
        from .show import main as show_main
        return show_main(rest)
    if cmd == "relock":
        from .relock import main as relock_main
        return relock_main(rest)
    if cmd in ("status", "lock-status"):
        return _status()
    print(f"unknown command {cmd!r}; use show | relock | status", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
