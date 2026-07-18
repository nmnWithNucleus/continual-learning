"""WS-E — browser-extension asset conformance (no browser on this box; the
Chrome leg is human, per handoff/ws-e-extension.md §Human test steps).

Three invariants keep the extension honest without a browser:

1. manifest.json parses and pins the PASSIVE posture (D-E1): exactly the four
   capture/lifecycle permissions, no content_scripts, no static
   host_permissions, optional_host_permissions for the runtime origin grant.
2. Every .js file in clients/extension/ parses under ``deno check``. Honesty
   note (review round): deno does NOT type-check plain .js without checkJs —
   for ALL these files the gate is syntax + module-graph strength only (which
   still catches parse errors and broken imports). The ``// @ts-nocheck``
   pragmas in the chrome-dependent files are inert today and kept only so a
   future checkJs-enabled toolchain doesn't drown in chrome.* type noise.
3. The deno test suite for the pure modules (uploader/segmenter conformance —
   serialized queue, backoff, 4xx drop, D-M1-1 restart loop, stop races)
   passes.

The whole module skips cleanly when deno is absent: the recording pytest suite
must never require it.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

EXT_DIR = Path(__file__).resolve().parents[1] / "clients" / "extension"

# deno lives on PATH in the dev env; the moe conda env's binary is also probed
# explicitly so the suite behaves the same under a bare PATH.
_MOE_DENO = Path("/home/ubuntu/miniconda3/envs/moe/bin/deno")


def _find_deno() -> str | None:
    on_path = shutil.which("deno")
    if on_path:
        return on_path
    if _MOE_DENO.exists():
        return str(_MOE_DENO)
    return None


DENO = _find_deno()

pytestmark = pytest.mark.skipif(DENO is None, reason="deno not installed")


def test_manifest_parses_and_pins_passive_posture():
    manifest = json.loads((EXT_DIR / "manifest.json").read_text())

    assert manifest["manifest_version"] == 3
    assert manifest["minimum_chrome_version"] == "116"

    # D-E1: exactly these permissions — nothing else. No page access of any kind.
    assert set(manifest["permissions"]) == {
        "tabCapture",
        "desktopCapture",
        "offscreen",
        "storage",
    }
    assert "content_scripts" not in manifest
    assert "host_permissions" not in manifest
    assert "activeTab" not in manifest.get("permissions", [])
    assert "tabs" not in manifest.get("permissions", [])
    # Server origin is user-configured: runtime grant via optional hosts only.
    assert manifest["optional_host_permissions"] == ["http://*/*", "https://*/*"]

    assert manifest["action"]["default_popup"] == "popup.html"
    assert manifest["background"]["service_worker"] == "background.js"
    # background.js is deliberately self-contained (no imports): no module type.
    assert manifest["background"].get("type") is None


def test_every_js_file_passes_deno_check():
    # Syntax + module-graph strength ONLY: deno does not type-check plain .js
    # (verified in the review round — a type error passes, a parse error or a
    # broken import fails). That is exactly the gate we want browserless.
    js_files = sorted(EXT_DIR.rglob("*.js"))
    assert js_files, f"no JS files under {EXT_DIR}"
    for path in js_files:
        proc = subprocess.run(
            [DENO, "check", "--no-lock", "--no-config", str(path.relative_to(EXT_DIR))],
            cwd=EXT_DIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, (
            f"deno check {path.name} failed:\n{proc.stdout}\n{proc.stderr}"
        )


def test_deno_module_tests_pass():
    proc = subprocess.run(
        [DENO, "test", "--no-lock", "--no-config", "tests/"],
        cwd=EXT_DIR,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"deno test failed:\n{proc.stdout}\n{proc.stderr}"
