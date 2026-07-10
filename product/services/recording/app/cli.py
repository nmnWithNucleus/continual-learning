"""Headless module CLI for one capture session (integrator/dev convenience).

    python -m app.cli --storage-url http://localhost:8083 --dp-url http://localhost:8085
    python -m app.cli --source /path/to/audio.wav --chunk-seconds 5 \
                      --storage-url ... --dp-url ... --base-wallclock 2026-07-09T12:00:00Z

Runs the SAME capturer the POST /capture/run endpoint runs, against LIVE storage +
data-processing, and prints the session summary as JSON. (No live services run in the
mock unit-test workflow, so this path is scripted-but-unrun there.)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from . import capturer
from .config import get_settings


def _parse_args(argv: list[str]) -> argparse.Namespace:
    settings = get_settings()
    p = argparse.ArgumentParser(prog="python -m app.cli", description="Run one capture session.")
    p.add_argument("--storage-url", default=settings.storage_url, help="storage base URL (/raw)")
    p.add_argument("--dp-url", default=settings.dp_url, help="data-processing base URL (/ingest)")
    p.add_argument("--modality", default="audio", help="ChunkSource modality (default audio)")
    p.add_argument("--source", default=None, help="path to a .wav; omit for a synthetic sample")
    p.add_argument("--chunk-seconds", type=float, default=None, help="chunk duration (default 5s)")
    p.add_argument("--base-wallclock", default=None, help="RFC3339 UTC frame-0 wall-clock")
    p.add_argument("--user-id", default=None)
    p.add_argument("--device-id", default=None)
    return p.parse_args(argv)


async def _run(args: argparse.Namespace) -> dict:
    return await capturer.run_session(
        settings=get_settings(),
        storage_url=args.storage_url,
        dp_url=args.dp_url,
        modality=args.modality,
        source=args.source,
        chunk_seconds=args.chunk_seconds,
        base_wallclock=args.base_wallclock,
        user_id=args.user_id,
        device_id=args.device_id,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    result = asyncio.run(_run(args))
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
