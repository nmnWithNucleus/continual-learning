"""``python -m app.vision.prompts relock`` — bump the human ``PACK_VERSION``, rewrite
``LOCK.json``, archive every pack's full text, and print the before/after dialect.

Editing a ``.prompt.md`` forks the dialect automatically (``PACK_DIGEST`` is recomputed
live at import), so relock is NOT required to make an edit take effect. Relock is the
deliberate act of cutting a new HUMAN version token (``p1`` → ``p2``) and archiving the
exact text of that version so any historical ``pipeline_version`` stays reconstructable —
the version-forward reprocessing promise the C2 contract already makes.

Reads/writes the packaged pack directory directly off disk (not the import-time cache), so
it sees edits made since the process started.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import _PACKAGE_DIR, compute_digest, load_registry, pack_digest


def _archive_payload(packs: dict, routes: dict, pack_version: str, digest: str) -> dict:
    return {
        "pack_version": pack_version,
        "pack_digest": digest,
        "note": "Full text of every pack + routes.json at this locked version — the complete "
                "set of digest inputs, so a historical pipeline_version and its exact wire "
                "text are reconstructable from this file alone.",
        "routes": routes,
        "packs": {
            pid: {
                "role": s.role,
                "scenarios": list(s.scenarios),
                "max_tokens": s.max_tokens,
                "temperature": s.temperature,
                "schema_name": s.schema_name,
                "schema": s.schema,
                "system": s.system,
                "user": s.user,
                "digest": pack_digest(s),
            }
            for pid, s in sorted(packs.items())
        },
    }


def _tag(pack: str, version: str, digest: str) -> str:
    return f"@{pack}.p{version}.{digest}"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    directory = Path(argv[0]) if argv else _PACKAGE_DIR

    packs, routes, _schemas = load_registry(directory)
    digest = compute_digest(packs, routes)

    lock_path = directory / "LOCK.json"
    old = json.loads(lock_path.read_text("utf-8")) if lock_path.exists() else {}
    old_version = str(old.get("pack_version", "1"))
    old_digest = str(old.get("pack_digest", ""))
    # First lock initialises at 1; every later relock bumps the human token.
    new_version = str(int(old_version) + 1) if lock_path.exists() else old_version

    example = "screen-clip-v1" if "screen-clip-v1" in packs else sorted(packs)[0]
    print("relock:", directory)
    print("  before:", _tag(example, old_version, old_digest) if old else "(no LOCK.json)")
    print("  after :", _tag(example, new_version, digest))
    if old_digest and old_digest == digest and lock_path.exists():
        print("  note  : pack text is UNCHANGED since the last lock — bumping the human "
              "version token only (this still forks the dialect).")

    archive_dir = directory / "archive"
    archive_dir.mkdir(exist_ok=True)
    archive_path = archive_dir / f"p{new_version}.json"
    archive_path.write_text(
        json.dumps(_archive_payload(packs, routes, new_version, digest), indent=2, sort_keys=True)
        + "\n",
        "utf-8",
    )
    print("  archived:", archive_path.relative_to(directory))

    lock = {
        "pack_version": new_version,
        "pack_digest": digest,
        "packs": {pid: pack_digest(s) for pid, s in sorted(packs.items())},
        "note": "pack_version is the hand-bumped human token in the dialect "
                "(@pN.DIGEST); pack_digest is recomputed live at import, so editing a "
                ".prompt.md forks the dialect automatically. Written by "
                "`python -m app.vision.prompts relock`.",
    }
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", "utf-8")
    print("  wrote  :", lock_path.relative_to(directory))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
