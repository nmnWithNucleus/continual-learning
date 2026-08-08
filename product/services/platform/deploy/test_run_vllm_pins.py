#!/usr/bin/env python3
"""The captioner launcher's pins are code, and this proves an env file cannot move them.

`run_vllm.sh` assigns `VLM_MODEL` and `VLM_REVISION` AFTER it sources the env file,
deliberately: what model captions a user's corpus is a dialect decision, not an
operational one, and an operator who could swap it by editing `learn.env` could change
every caption in the training set without anything in `pipeline_version` moving. The
assignment order is the whole mechanism, and it is one line away from being wrong, so it
is checked rather than trusted.

Run: python3 test_run_vllm_pins.py    (no fleet needed — `--status` only reads)
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent

# The pins, restated here on purpose: a guard that read them out of the script it
# guards would pass no matter what the script said.
PINNED_MODEL = "Qwen/Qwen3-VL-32B-Instruct"
PINNED_REVISION_PREFIX = "0cfaf481"

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        if detail:
            print(f"        {detail}")
        failures.append(name)


print("run_vllm.sh pin guard")
with tempfile.TemporaryDirectory() as td:
    evil = Path(td) / "evil.env"
    evil.write_text("VLM_MODEL=evil/override-7b\nVLM_REVISION=deadbeef\n")
    r = subprocess.run(
        ["bash", str(DEPLOY / "run_vllm.sh"), "--status"],
        env={**os.environ, "ENV_FILE": str(evil)},
        capture_output=True, text=True)
    check("run_vllm.sh --status prints its pins",
          PINNED_MODEL in r.stdout and PINNED_REVISION_PREFIX in r.stdout,
          r.stdout[-300:])
    check("an env file cannot override the model/revision pins",
          "evil/override-7b" not in r.stdout and "deadbeef" not in r.stdout,
          r.stdout[-300:])

print()
if failures:
    print(f"FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("ALL CHECKS PASSED")
