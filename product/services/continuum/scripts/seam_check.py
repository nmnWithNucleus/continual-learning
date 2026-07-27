#!/usr/bin/env python3
"""LIVE seam check: two real processes, storage and continuum, talking over HTTP.

    ./.venv/bin/python scripts/seam_check.py            # from the continuum service root

This is NOT a unit test and it deliberately does not use `httpx.MockTransport`,
`TestClient`, or any in-process shim. It starts the storage service as its own OS
process, under its own interpreter, against a THROWAWAY SQLite DB and blob tree in a
temp dir, and drives continuum's real storage clients (`app/clients/*.py`) and its real
`run_cycle` at it over the wire. Every assertion below is about bytes that crossed a
socket.

WHY IT LIVES IN continuum/scripts/ AND NOT storage/scripts/
-----------------------------------------------------------
The dependency arrow. This script imports `app.clients` and `app.cycle` from CONTINUUM —
it is the consumer, driving the seam from the consumer's side, which is the only side
that can prove the seam is usable. Storage's side of the seam is exercised here purely as
"a process we start with its own venv"; nothing here imports a storage module. Putting
this file under `storage/scripts/` would make storage's own test surface import
continuum's package and depend on continuum's venv, which inverts the one dependency
direction the architecture is built on: storage knows nothing about continuum, continuum
consumes C10/C12/C13/C14. The M9 parity script is under `storage/scripts/` for exactly
the mirrored reason — it is a claim about storage's renderer, measured from storage.

WHAT IS DELIBERATELY NON-DEFAULT ABOUT THE SERVER WE START, AND WHY
-------------------------------------------------------------------
`STORAGE_WINDOW_DELTA_SECONDS=0`. In production δ (default 60 s) covers in-flight writes
racing the window boundary: a window ends at `now − δ` so that every record with an
`ingest_time` below `t_end` has certainly committed. This harness is the one setting
where δ is provably unnecessary — there is exactly one writer, it is this script, and it
has already received the 200 for every record before a window is opened. Left at 60 s the
check could not exist at all: a never-trained user's window is `[earliest_ingest, now−60)`,
which for records written seconds ago is an empty (indeed inverted) range, and storage
correctly refuses to open it. Setting δ to 0 is a service-config choice; nothing about the
watermark rule this script is here to prove depends on its value. Everything else — the
recipe registry, the gate policy, the day-log format, the window ledger — is the
COMMITTED artifact, read from the real `recipes/` and `policies/` dirs.

WHAT IT REFUSES TO TOUCH
------------------------
The dev DB (`storage/app/dev.db`), the dev blob store, the dev reservoir, continuum's
66 GB `var/`, and port 8083 (the learn-fleet port on node-7 and in `storage/run.sh`).
Step 1 asserts every writable path it hands the server is inside the temp dir; step 8
re-hashes the dev DB and re-lists both service trees and fails if anything moved.

TWO STEPS BEYOND THE BRIEF: 7b AND 7c, THE SECOND NIGHT
-------------------------------------------------------
Steps 1-8 are the briefed shape. STEP 7b was added because without it a green verdict
would be misleading: steps 1-7 prove a user's FIRST night, which is the one night that
is provably owed no rehearsal (the reservoir is empty). Night two is the first night
that owes some, and rehearsal is what keeps sequential consolidation from collapsing.
7b runs a real second night for a second user and asserts the behaviour the pinned
recipe's `replay.source` requires — including, for `amp`, that the night RAISES rather
than silently rehearsing nothing. A silent no-op there would pass every other check in
this file while being the exact failure replay exists to prevent.

STEP 7c closes what 7b found. 7b proves the DEFAULT pin (`consolidation-v1.0`,
`replay.source='amp'`) stops at night one over HTTP; 7c proves that under
`consolidation-v1.1` — the fork that flips exactly that one knob to `rawlog` — a user
runs night one AND night two end to end over the same socket, and that night two's
rehearsal really was drawn from night one's DAY-LOG. "Really" is the whole point, so
the assertions are falsifiable rather than green-by-construction: rare literal markers
are planted in the record text of each night, and 7c requires night one's marker to be
present in night two's TRAINING corpus and absent from its AMPLIFIED-only corpus, every
replay paragraph to appear verbatim in night one's day-log and in NONE of night one's
amplified corpus (which is what C14 holds), and night one's own replay to be zero
characters. A replay that silently returned "" would fail six of those checks.

Both nights run against a storage rebooted with `STORAGE_DAYLOG_RECIPE_ID` set to
v1.1 as well. That is not decoration: `recipe_id` is service config on BOTH sides, the
C10 body stamps it, and continuum keys its journal stage and its C5 lineage on it — a
night rendered under a recipe other than the one it claims is audited as trained under
a recipe it was not trained under. Re-pinning one service and not the other is
therefore a real deployment step, and 7c only counts as a proof of the flip if it makes
it. The reboot keeps the same DB, blob tree, reservoir and port, so nothing earlier in
the run is discarded and teardown is unchanged.

BLOCKERS vs FAILURES
--------------------
A check FAILS when the system does something other than what it must. A BLOCKER is a
property the system genuinely and correctly has, that every check passes on, and that
still means "not ready" — today: only a first night can run over HTTP. Blockers are
printed in the summary under the PASS, never folded into it and never folded away.

EXIT CODE: 0 iff every step passes. Any failure is loud, printed with the real values
that produced it, and never downgraded to a warning. Read the blocker list before
reading a 0 as "ship it".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
CONTINUUM_ROOT = HERE.parent
SERVICES_ROOT = CONTINUUM_ROOT.parent
STORAGE_ROOT = SERVICES_ROOT / "storage"

# continuum's own package, imported the way the service imports it.
sys.path.insert(0, str(CONTINUUM_ROOT))

import httpx  # noqa: E402  (after the path insert, on purpose)

# The fleet port. Never ours, at any cost — a run that talked to a live storage would
# write real windows and a real reservoir into somebody's fleet DB.
FLEET_PORT = 8083

USER_OK = "u-seamcheck-ok"            # has a C12 profile; walks the whole loop
USER_NOTZ = "u-seamcheck-noprofile"   # has records, NO profile; must not consolidate
USER_V11 = "u-seamcheck-v11"          # STEP 7c: two full nights under consolidation-v1.1
HOME_TZ = "America/Los_Angeles"
TRAVEL_TZ = "Asia/Tokyo"              # +9 vs home's −7: both the clock AND the date differ

RFC3339 = "%Y-%m-%dT%H:%M:%SZ"

# The two recipes STEP 7c is about. v1.1 forks v1.0 on exactly one knob —
# replay.source amp -> rawlog — and 7c asserts that "exactly one" over the wire rather
# than trusting the note in the artifact.
RECIPE_V10 = "consolidation-v1.0"
RECIPE_V11 = "consolidation-v1.1"

# Rare literal strings planted in STEP 7c's RECORD TEXT. They ride C2 -> storage's
# renderer -> the C10 block text -> the replay pool -> the training corpus, so finding
# one in a corpus file is a statement about the whole path and not about this script's
# bookkeeping. Deliberately not words: a substring test over ordinary prose could pass
# by coincidence, and a check that can pass by coincidence proves nothing.
MARK_N1 = "zzq-night-one-marker-7c"
MARK_N2 = "zzq-night-two-marker-7c"


# ---------------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------------


class StepAborted(RuntimeError):
    """This step cannot continue; later steps are reported honestly, never skipped
    into a pass."""


class Reporter:
    """Per-step PASS/FAIL with the evidence inline.

    A check NEVER silently passes: `check()` records the boolean it was given, and a
    False makes its step FAIL and the whole run exit non-zero. There is no severity
    knob and no way to downgrade a failure to a note, because a false green here is the
    most expensive output this script could produce.
    """

    def __init__(self) -> None:
        self.steps: list[tuple[str, str, int, int]] = []  # (id, verdict, ok, total)
        self._cur: Optional[str] = None
        self._ok = 0
        self._n = 0
        self._failed_names: list[str] = []
        # A BLOCKER is a property the system genuinely has, which every check here
        # passes on, and which still means "do not ship this yet". It is not a failure
        # — nothing is broken — so it must not be laundered into one, and it must not
        # be laundered OUT of the verdict either.
        self.blockers: list[str] = []

    def blocker(self, text: str) -> None:
        self.blockers.append(text)

    def start(self, step_id: str, title: str) -> None:
        self._cur, self._ok, self._n = step_id, 0, 0
        print()
        print("=" * 78)
        print(f"{step_id} — {title}")
        print("=" * 78)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self._n += 1
        if ok:
            self._ok += 1
        else:
            self._failed_names.append(f"{self._cur}/{name}")
        mark = "ok  " if ok else "FAIL"
        line = f"  [{mark}] {name}"
        if detail:
            line += f"\n           {detail}"
        print(line)
        return ok

    def note(self, text: str) -> None:
        for line in text.splitlines():
            print(f"         {line}")

    def show(self, label: str, value: Any) -> None:
        print(f"  ....   {label}: {value}")

    def end(self) -> None:
        verdict = "PASS" if self._ok == self._n and self._n > 0 else "FAIL"
        if self._n == 0:
            verdict = "FAIL"  # a step that asserted nothing has proved nothing
        print(f"  --> {self._cur}: {verdict}  ({self._ok}/{self._n} checks)")
        self.steps.append((self._cur or "?", verdict, self._ok, self._n))

    def abort(self, step_id: str, reason: str) -> None:
        """A step that could not run at all. Recorded as FAIL, never as skipped."""
        print(f"  [FAIL] step could not run: {reason}")
        print(f"  --> {step_id}: FAIL  (aborted)")
        self.steps.append((step_id, "FAIL", 0, 0))
        self._failed_names.append(f"{step_id}/<aborted>")

    def summary(self) -> int:
        print()
        print("=" * 78)
        print("SUMMARY")
        print("=" * 78)
        for step_id, verdict, ok, n in self.steps:
            print(f"  {step_id:<8} {verdict:<5} {ok}/{n}")
        failed = [s for s in self.steps if s[1] != "PASS"]
        print()
        if failed:
            print(f"  VERDICT: FAIL — {len(failed)} step(s) failed")
            for name in self._failed_names:
                print(f"           - {name}")
            return 1
        total = sum(n for _, _, _, n in self.steps)
        print(f"  VERDICT: PASS — {len(self.steps)} steps, {total} checks, all green.")
        print("           Two processes, one socket. Every behaviour asserted above is")
        print("           the real behaviour of the two services over real HTTP.")
        if self.blockers:
            print()
            print(f"  ...BUT {len(self.blockers)} BLOCKER(S) — every check passed and the")
            print("  system still is not ready to be relied on. Read these before")
            print("  treating the PASS above as 'the seam works':")
            for b in self.blockers:
                print(f"    * {b}")
        return 0


R = Reporter()


# ---------------------------------------------------------------------------------
# process + fixture management
# ---------------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def sha256_file(path: Path) -> str:
    if not path.exists():
        return "<absent>"
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def listing(path: Path) -> list[str]:
    """Top-level entry names + sizes. Top-level only, on purpose: continuum's var/ is
    66 GB of parity artifacts and walking it would cost more than the whole check."""
    if not path.exists():
        return []
    out = []
    for p in sorted(path.iterdir()):
        try:
            size = p.stat().st_size if p.is_file() else -1
        except OSError:
            size = -2
        out.append(f"{p.name}:{size}")
    return out


class Storage:
    """The storage service as a real subprocess, on a throwaway everything."""

    def __init__(self, tmp: Path, port: int, python: Path,
                 daylog_recipe_id: str = RECIPE_V10) -> None:
        self.tmp = tmp
        self.port = port
        self.python = python
        # `recipe_id` is SERVICE CONFIG on storage's side (STORAGE_DAYLOG_RECIPE_ID):
        # the materializer renders under that recipe's corpus knobs and stamps the id
        # on the C10 body. STEP 7c re-pins it, which is why it is an attribute rather
        # than a constant in env().
        self.daylog_recipe_id = daylog_recipe_id
        self.base = f"http://127.0.0.1:{port}"
        self.db_path = tmp / "storage" / "seam.db"
        self.raw_dir = tmp / "storage" / "raw_store"
        self.reservoir_dir = tmp / "storage" / "reservoir"
        self.log_path = tmp / "storage.log"
        self.proc: Optional[subprocess.Popen] = None
        self._log_fh = None

    def env(self) -> dict[str, str]:
        # Start from the ambient environment but scrub EVERY STORAGE_* key first, so a
        # stray export in the operator's shell cannot point this server at the dev DB.
        env = {k: v for k, v in os.environ.items() if not k.startswith("STORAGE_")}
        env.update({
            "STORAGE_DB_PATH": str(self.db_path),
            "STORAGE_RAW_DIR": str(self.raw_dir),
            "STORAGE_RESERVOIR_DIR": str(self.reservoir_dir),
            # COMMITTED source, read-only: we want the real C13 artifacts under test.
            "STORAGE_RECIPES_DIR": str(STORAGE_ROOT / "recipes"),
            "STORAGE_POLICIES_DIR": str(STORAGE_ROOT / "policies"),
            # See the module docstring. Zero is correct here and only here.
            "STORAGE_WINDOW_DELTA_SECONDS": "0",
            "STORAGE_DAYLOG_RECIPE_ID": self.daylog_recipe_id,
            "PYTHONUNBUFFERED": "1",
        })
        return env

    def start(self, *, append_log: bool = False) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = self.log_path.open("ab" if append_log else "wb")
        self.proc = subprocess.Popen(
            [str(self.python), "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"],
            cwd=str(STORAGE_ROOT), env=self.env(),
            stdout=self._log_fh, stderr=subprocess.STDOUT,
        )

    def wait_healthy(self, timeout: float = 40.0) -> tuple[bool, str]:
        deadline = time.monotonic() + timeout
        last = "no attempt made"
        while time.monotonic() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                return False, (f"server exited with code {self.proc.returncode} before "
                               f"answering /health")
            try:
                resp = httpx.get(f"{self.base}/health", timeout=2.0)
                if resp.status_code == 200 and resp.json().get("ok") is True:
                    return True, resp.text
                last = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except Exception as exc:  # connection refused while it boots
                last = f"{type(exc).__name__}: {exc}"
            time.sleep(0.15)
        return False, last

    def restart(self, *, daylog_recipe_id: str) -> tuple[bool, str]:
        """Re-pin STORAGE_DAYLOG_RECIPE_ID and boot again on the SAME everything.

        The DB path, blob tree, reservoir dir and port are unchanged, so every window,
        record, profile and reservoir admission written so far survives — this is a
        re-pin of one piece of service config, not a fresh fixture. Exactly one server
        process exists at a time (stop is awaited before start), and the log is appended
        to rather than truncated so `log_tail` still shows the whole run.
        """
        stopped, how = self.stop()
        if not stopped:
            return False, f"the previous server would not stop ({how})"
        was, self.daylog_recipe_id = self.daylog_recipe_id, daylog_recipe_id
        self.start(append_log=True)
        ok, info = self.wait_healthy()
        return ok, (f"STORAGE_DAYLOG_RECIPE_ID {was} -> {daylog_recipe_id}; old pid "
                    f"stopped via {how}; new pid={self.proc.pid if self.proc else '?'}; "
                    f"/health -> {info}")

    def log_tail(self, n: int = 40) -> str:
        if not self.log_path.exists():
            return "<no log>"
        lines = self.log_path.read_text(errors="replace").splitlines()
        return "\n".join(lines[-n:]) or "<log empty>"

    def stop(self) -> tuple[bool, str]:
        if self.proc is None:
            return True, "never started"
        how = "already dead"
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=10)
                how = "SIGTERM"
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
                how = "SIGKILL (SIGTERM ignored)"
        if self._log_fh is not None:
            self._log_fh.close()
            self._log_fh = None
        return self.proc.poll() is not None, how


# ---------------------------------------------------------------------------------
# raw wire helpers (deliberately NOT continuum's clients — these are the control group)
# ---------------------------------------------------------------------------------


def wire(method: str, base: str, path: str, *, params: dict | None = None,
         json_body: Any | None = None, timeout: float = 30.0) -> tuple[int, Any]:
    resp = httpx.request(method, f"{base}{path}", params=params, json=json_body,
                         timeout=timeout)
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    return resp.status_code, body


def c2_record(*, user_id: str, rid: str, chunk: str, t_start: datetime, t_end: datetime,
              kind: str, text: str, device_tz: Optional[str] = None,
              subsegments: Optional[list[tuple[datetime, datetime, str]]] = None,
              ) -> dict[str, Any]:
    """One C2 v0 processed record, shaped by contracts/c2_processed_record.v0.json.

    `device_tz` is the D17 FACT — where the wearer physically was — and it is optional
    exactly so the two cases this script needs both exist on the wire: a record that
    carries one (the travel case, which must beat home_tz in the renderer) and a record
    that carries none (the fallback case, which must land on home_tz).
    """
    modality = "audio" if kind == "transcript" else "image"
    source: dict[str, Any] = {
        "device_id": "dev-seamcheck-01",
        "stream_id": f"st-{user_id}",
        "chunk_id": chunk,
        "blob_ref": f"raw/{user_id}/{chunk}",
        "modality": modality,
    }
    if device_tz is not None:
        offset = t_start.astimezone(ZoneInfo(device_tz)).utcoffset()
        source["device_tz"] = device_tz
        source["device_utc_offset_minutes"] = int(offset.total_seconds() // 60)
    content: dict[str, Any] = {"kind": kind, "text": text, "language": "en"}
    if subsegments:
        content["segments"] = [
            {"t_start": a.strftime(RFC3339), "t_end": b.strftime(RFC3339),
             "text": s, "speaker": None}
            for a, b, s in subsegments
        ]
    return {
        "contract": "C2",
        "version": "0",
        "record_id": rid,
        "user_id": user_id,
        "source": source,
        "t_start": t_start.strftime(RFC3339),
        "t_end": t_end.strftime(RFC3339),
        "content": content,
        "enrichments": {"speakers": [], "faces": [], "places": [], "objects": []},
        "pipeline_version": "seam-check-v1",
        "processed_at": datetime.now(timezone.utc).strftime(RFC3339),
    }


CLOCK_409 = "not strictly greater than the floor"
"""Storage's window-open collision guard.

`t_end` must be strictly greater than the user's watermark AND every prior window end
AND (for a never-trained user) their earliest ingest_time. Timestamps are second
granularity, so with δ=0 this guard is a real sub-second wall-clock wait: a window
opened in the very second a record was ingested, or in the second a previous window
ended, is legitimately refused. Everywhere this script opens a window it waits that
tick out rather than papering over it with a fixture clock — and it retries ONLY this
one 409. Any other 409, and every other status, propagates untouched, because those
would be storage disagreeing with us rather than the harness racing a boundary.
"""


def wire_open_window(base: str, user_id: str, *, budget_s: float = 20.0
                     ) -> tuple[int, Any]:
    """Raw-wire `POST /training/windows`, waiting out CLOCK_409 (see above)."""
    deadline = time.monotonic() + budget_s
    while True:
        code, body = wire("POST", base, "/training/windows",
                          json_body={"user_id": user_id})
        if code != 409 or CLOCK_409 not in json.dumps(body) \
                or time.monotonic() > deadline:
            return code, body
        time.sleep(0.2)


def open_window_when_clock_allows(open_call: Callable[[], Any], *, budget_s: float = 20.0
                                  ) -> tuple[Any, float]:
    """Call `open_call()` through continuum's client, waiting out CLOCK_409.

    Both spellings of the same refusal are handled, and the pair is the point:
    `HttpWindowLedger.open()` now turns storage's 409 into `WindowNotOpenable`
    (F5 -- a raw `raise_for_status` taught operators the service was broken when the
    user was merely not ready), while every OTHER wire call in this file still meets
    the bare `httpx.HTTPStatusError`. Only the clock guard is retried in either case;
    anything else propagates, because that would be storage disagreeing with us
    rather than the harness racing a second boundary.
    """
    # Imported here, like every other continuum symbol in this file: the sys.path
    # insert that makes `app` importable happens above, not at interpreter start.
    from app.clients import WindowNotOpenable

    deadline = time.monotonic() + budget_s
    started = time.monotonic()
    while True:
        try:
            return open_call(), time.monotonic() - started
        except WindowNotOpenable as exc:
            if CLOCK_409 not in json.dumps(exc.detail) or time.monotonic() > deadline:
                raise
            time.sleep(0.2)
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                detail = json.dumps(exc.response.json())
            except Exception:
                detail = exc.response.text
            is_clock = exc.response.status_code == 409 and CLOCK_409 in detail
            if not is_clock or time.monotonic() > deadline:
                raise
            time.sleep(0.2)


# ---------------------------------------------------------------------------------
# THE STEPS
# ---------------------------------------------------------------------------------


def step1(st: Storage, tmp: Path, before: dict) -> None:
    R.start("STEP 1", "storage up on a free port, THROWAWAY db + blob dir, health-gated")
    R.check("chosen port is not the fleet port 8083", st.port != FLEET_PORT,
            f"port = {st.port}")
    R.check("chosen port was free before we bound it", True,
            "(claimed via bind-to-0; re-verified free after teardown in STEP 8)")

    env = st.env()
    writable = {k: env[k] for k in ("STORAGE_DB_PATH", "STORAGE_RAW_DIR",
                                    "STORAGE_RESERVOIR_DIR")}
    inside = all(Path(v).resolve().is_relative_to(tmp.resolve())
                 for v in writable.values())
    R.check("every WRITABLE storage path is inside the temp dir", inside,
            " ".join(f"{k}={v}" for k, v in writable.items()))
    not_dev = all(STORAGE_ROOT not in Path(v).resolve().parents
                  for v in writable.values())
    R.check("no writable path is inside the storage service tree", not_dev,
            f"service tree = {STORAGE_ROOT}")
    R.check("dev.db exists and was hashed before we started", before["devdb"] != "<absent>",
            f"sha256(app/dev.db) = {before['devdb'][:16]}…  ({before['devdb_size']} bytes)")

    st.start()
    ok, info = st.wait_healthy()
    if not R.check("GET /health returns {\"ok\":true}", ok, info):
        R.note("server log tail:\n" + st.log_tail())
        R.end()
        raise StepAborted("storage never became healthy")
    R.check("the server is a separate OS process", st.proc is not None
            and st.proc.pid != os.getpid(),
            f"uvicorn pid={st.proc.pid}, this script pid={os.getpid()}")
    R.show("base url", st.base)
    R.show("interpreter", str(st.python))
    R.end()


def step2(st: Storage, tmp: Path, cont_python: Path) -> None:
    R.start("STEP 2", "C12 profile: a MISSING home_tz is a clean, loud refusal to consolidate")
    from app.clients.profile_client import HttpProfileClient, UserNotSchedulable

    # --- (a) the read surface: 404, never a default ------------------------------
    code, body = wire("GET", st.base, f"/users/{USER_OK}/profile")
    R.check("GET profile for an unknown user is 404 (not a UTC default)", code == 404,
            f"HTTP {code}: {json.dumps(body)[:180]}")

    client = HttpProfileClient(st.base, timeout=20.0)
    raised: Optional[BaseException] = None
    try:
        client.home_tz(USER_OK)
    except UserNotSchedulable as exc:
        raised = exc
    except BaseException as exc:  # noqa: BLE001 - any other type is itself the finding
        raised = exc
    R.check("HttpProfileClient turns that 404 into UserNotSchedulable",
            isinstance(raised, UserNotSchedulable),
            f"raised {type(raised).__name__ if raised else 'nothing'}")
    msg = str(raised or "")
    R.check("the error names an OPERATOR ACTION, not a stack trace",
            "OPERATOR ACTION" in msg and "PUT" in msg)
    R.check("the error offers no fallback zone anywhere in its text",
            "UTC" not in msg,
            "asserted literally: a message that even mentions UTC invites the "
            "default D17 abolished")

    # --- (b) the write surface -----------------------------------------------------
    code, body = wire("PUT", st.base, f"/users/{USER_OK}/profile",
                      json_body={"home_tz": HOME_TZ})
    R.check("PUT profile 200s", code == 200, f"HTTP {code}: {json.dumps(body)[:200]}")
    R.check("stored home_tz is what we declared",
            isinstance(body, dict) and body.get("home_tz") == HOME_TZ,
            f"home_tz={body.get('home_tz') if isinstance(body, dict) else body!r}")
    R.check("the body is a C12 v0", isinstance(body, dict) and body.get("contract") == "C12"
            and str(body.get("version")) == "0")
    code, body = wire("PUT", st.base, f"/users/{USER_OK}/profile",
                      json_body={"home_tz": "PST"})
    R.check("an abbreviation ('PST') is rejected 400", code == 400,
            f"HTTP {code}: {json.dumps(body)[:160]}")
    R.check("HttpProfileClient now reads it back over HTTP",
            client.home_tz(USER_OK) == HOME_TZ, f"home_tz = {client.home_tz(USER_OK)}")

    # --- (c) a user with records but NO profile does not consolidate --------------
    now = datetime.now(timezone.utc).replace(microsecond=0)
    rec = c2_record(user_id=USER_NOTZ, rid="r-notz-1", chunk="c-notz-1",
                    t_start=now - timedelta(minutes=10), t_end=now - timedelta(minutes=9),
                    kind="transcript", text="a record from a user who never set a zone")
    code, body = wire("POST", st.base, "/context/records", json_body=rec)
    R.check(f"{USER_NOTZ} has ingest history (so a window COULD be opened)", code == 200,
            f"HTTP {code}: {json.dumps(body)[:160]}")

    env = os.environ.copy()
    env.update({"CONTINUUM_STORAGE_CLIENTS": "http", "STORAGE_URL": st.base,
                "CONTINUUM_VAR_DIR": str(tmp / "continuum_var"),
                "TRAINER_BACKEND": "mock", "PYTHONUNBUFFERED": "1"})
    proc = subprocess.run([str(cont_python), "-m", "app.nightly", "--user", USER_NOTZ],
                          cwd=str(CONTINUUM_ROOT), env=env, capture_output=True,
                          text=True, timeout=180)
    R.check("`python -m app.nightly` on a profile-less user exits 2 (not 0, not a crash)",
            proc.returncode == 2, f"exit={proc.returncode}")
    R.check("its stderr says NOT SCHEDULABLE", "NOT SCHEDULABLE" in proc.stderr,
            proc.stderr.strip().splitlines()[0][:200] if proc.stderr.strip() else "<empty>")
    R.check("it printed no result JSON on stdout (nothing ran)", proc.stdout.strip() == "",
            f"stdout={proc.stdout.strip()[:160]!r}")
    code, rows = wire("GET", st.base, "/training/windows", params={"user_id": USER_NOTZ})
    R.check("the failure was CLEAN: no window was opened for that user",
            code == 200 and rows == [], f"HTTP {code}: {json.dumps(rows)[:160]}")

    # --- (d) and the data plane refuses too, not just the CLI ---------------------
    code, win = wire_open_window(st.base, USER_NOTZ)
    R.check("storage WILL open a window for them (a window needs no timezone)",
            code == 200 and isinstance(win, dict) and "window_id" in win,
            f"HTTP {code}: {json.dumps(win)[:200]}")
    if code == 200:
        code, body = wire("GET", st.base, "/training/daylog",
                          params={"user_id": USER_NOTZ, "window_id": win["window_id"]})
        R.check("but GET /training/daylog refuses with 409 (no fallback zone)",
                code == 409, f"HTTP {code}: {json.dumps(body)[:220]}")
        detail = json.dumps(body)
        R.check("the 409 blames the missing profile explicitly",
                "no profile" in detail and "home_tz" in detail)
    R.end()


def step3(st: Storage) -> dict[str, Any]:
    R.start("STEP 3", "POST C2 records over ~2.5 h of event time — travel case + fallback case")
    # Event time is deliberately YESTERDAY: membership is on the INGEST axis (all of
    # these are ingested now), while bucketing and the anchor lines are on the EVENT
    # axis. Choosing instants a day back proves those two axes really are separate, and
    # choosing 16:30 UTC makes the Tokyo local DATE differ from the Los Angeles one for
    # the same instant, so "device_tz won" is visible in the rendered text, not inferred.
    day = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    at = lambda h, m, s=0: base + timedelta(hours=h, minutes=m, seconds=s)  # noqa: E731

    home_t = at(14, 0)      # device says America/Los_Angeles (= home_tz)
    notz_t = at(15, 0)      # device says NOTHING -> must fall back to home_tz
    trav_t = at(16, 30)     # device says Asia/Tokyo -> the travel case

    records = [
        # --- cluster A: at home, device reports the home zone ----------------------
        c2_record(user_id=USER_OK, rid="r-a1", chunk="c-a1",
                  t_start=home_t, t_end=home_t + timedelta(seconds=8),
                  kind="caption", device_tz=HOME_TZ,
                  text="a kitchen counter, laptop open beside a chipped blue mug"),
        c2_record(user_id=USER_OK, rid="r-a2", chunk="c-a2",
                  t_start=home_t + timedelta(seconds=20),
                  t_end=home_t + timedelta(seconds=40),
                  kind="transcript", device_tz=HOME_TZ,
                  text="right, so the release cut is tomorrow. can you take the migration?",
                  subsegments=[
                      (home_t + timedelta(seconds=20), home_t + timedelta(seconds=28),
                       "right, so the release cut is tomorrow"),
                      (home_t + timedelta(seconds=35), home_t + timedelta(seconds=40),
                       "can you take the migration?"),
                  ]),
        c2_record(user_id=USER_OK, rid="r-a3", chunk="c-a3",
                  t_start=home_t + timedelta(seconds=40),
                  t_end=home_t + timedelta(seconds=48),
                  kind="ocr", device_tz=HOME_TZ,
                  text="STANDUP 09:30 - release cut - migration owner TBD"),
        # 40 s later: inside the 60 s scene-boundary gap, so it EXTENDS this block
        # rather than starting a new one — which is what makes the block's rendered
        # time range a real range instead of a single minute.
        c2_record(user_id=USER_OK, rid="r-a4", chunk="c-a4",
                  t_start=home_t + timedelta(seconds=90),
                  t_end=home_t + timedelta(seconds=98),
                  kind="caption", device_tz=HOME_TZ,
                  text="the same counter later, a second mug, a notebook open to a list"),
        # --- cluster B: NO device_tz at all -> the fallback case -------------------
        c2_record(user_id=USER_OK, rid="r-b1", chunk="c-b1",
                  t_start=notz_t, t_end=notz_t + timedelta(seconds=8),
                  kind="caption",
                  text="a train window, fields going past, a paper cup on the tray"),
        c2_record(user_id=USER_OK, rid="r-b2", chunk="c-b2",
                  t_start=notz_t + timedelta(seconds=15),
                  t_end=notz_t + timedelta(seconds=20),
                  kind="transcript",
                  text="I think we get in around six if this doesn't stop again"),
        # --- cluster C: device says Asia/Tokyo -> the travel case ------------------
        c2_record(user_id=USER_OK, rid="r-c1", chunk="c-c1",
                  t_start=trav_t, t_end=trav_t + timedelta(seconds=8),
                  kind="caption", device_tz=TRAVEL_TZ,
                  text="a narrow counter, warm light, a bowl of ramen and a paper menu"),
        c2_record(user_id=USER_OK, rid="r-c2", chunk="c-c2",
                  t_start=trav_t + timedelta(seconds=25),
                  t_end=trav_t + timedelta(seconds=30),
                  kind="transcript", device_tz=TRAVEL_TZ,
                  text="he said the office is two stops back the other way"),
    ]

    accepted = 0
    for rec in records:
        code, body = wire("POST", st.base, "/context/records", json_body=rec)
        if code != 200:
            R.check(f"POST /context/records {rec['record_id']}", False,
                    f"HTTP {code}: {json.dumps(body)[:400]}")
        else:
            accepted += 1
    R.check(f"all {len(records)} C2 records accepted (schema-gated at the edge)",
            accepted == len(records), f"{accepted}/{len(records)} returned 200")

    R.check("the set spans a couple of hours of EVENT time",
            (trav_t - home_t) >= timedelta(hours=2),
            f"{home_t.strftime(RFC3339)} .. {trav_t.strftime(RFC3339)} "
            f"= {(trav_t - home_t)}")

    n_tz = sum(1 for r in records if "device_tz" in r["source"])
    n_travel = sum(1 for r in records if r["source"].get("device_tz") == TRAVEL_TZ)
    n_none = len(records) - n_tz
    R.check("at least one record carries a device_tz DIFFERENT from home_tz",
            n_travel >= 1, f"{n_travel} record(s) with device_tz={TRAVEL_TZ}, "
                           f"home_tz={HOME_TZ}")
    R.check("at least one record carries NO device_tz at all", n_none >= 1,
            f"{n_none} record(s) with no source.device_tz")

    code, got = wire("GET", st.base, "/context/records/r-c1")
    R.check("the travel record's device_tz round-trips VERBATIM",
            code == 200 and isinstance(got, dict)
            and got.get("source", {}).get("device_tz") == TRAVEL_TZ,
            f"source.device_tz = "
            f"{got.get('source', {}).get('device_tz') if isinstance(got, dict) else got!r}, "
            f"offset_minutes = "
            f"{got.get('source', {}).get('device_utc_offset_minutes') if isinstance(got, dict) else '?'}")
    code, got = wire("GET", st.base, "/context/records/r-b1")
    R.check("the no-tz record really has no device_tz key (not an empty string)",
            code == 200 and isinstance(got, dict)
            and "device_tz" not in got.get("source", {}),
            f"source keys = {sorted(got.get('source', {})) if isinstance(got, dict) else got!r}")

    code, rows = wire("GET", st.base, "/context/records",
                      params={"user_id": USER_OK,
                              "from": (home_t - timedelta(minutes=1)).strftime(RFC3339),
                              "to": (trav_t + timedelta(minutes=1)).strftime(RFC3339)})
    R.check(f"the raw C2 range read (NOT replaced by C10) returns all {len(records)}",
            code == 200 and isinstance(rows, list) and len(rows) == len(records),
            f"HTTP {code}, {len(rows) if isinstance(rows, list) else '?'} rows")

    # The window's t_end is `now − δ` truncated to the second, and membership is
    # half-open on the ingest axis. A record ingested in the SAME second the window is
    # opened would sit exactly on t_end and be excluded. One tick of real time, waited
    # for honestly rather than papered over with a fixture clock.
    time.sleep(1.5)
    R.note("slept 1.5 s so every record's ingest_time is strictly below the next "
           "window's t_end (half-open [t_start, t_end), second granularity)")
    R.end()
    return {"home_t": home_t, "notz_t": notz_t, "trav_t": trav_t}


def step4(st: Storage) -> dict[str, Any]:
    R.start("STEP 4", "open the training window; re-open is an IDEMPOTENT get-or-create")
    from app.clients import window_ledger
    from app.config import get_settings

    ledger = window_ledger(get_settings())

    code, first = wire_open_window(st.base, USER_OK)
    if code != 200:
        R.check("POST /training/windows", False, f"HTTP {code}: {json.dumps(first)[:400]}")
        R.end()
        raise StepAborted("could not open the first window")
    R.check("POST /training/windows 200s", True,
            f"window_id={first['window_id']} state={first['state']} "
            f"[{first['t_start']} .. {first['t_end']})")

    code, second = wire("POST", st.base, "/training/windows", json_body={"user_id": USER_OK})
    R.check("re-POST returns the SAME window_id", code == 200
            and second.get("window_id") == first["window_id"],
            f"{first['window_id']} vs {second.get('window_id')}")
    R.check("re-POST bounds did NOT move (t_start and t_end both identical)",
            second.get("t_start") == first["t_start"]
            and second.get("t_end") == first["t_end"],
            f"[{second.get('t_start')} .. {second.get('t_end')})")
    R.check("re-POST is byte-identical across the WHOLE row (opened_at included)",
            second == first,
            "a fresh row would carry a fresh opened_at even with the same bounds")

    third = ledger.open(USER_OK, tz=HOME_TZ)
    R.check("continuum's HttpWindowLedger.open() agrees with the raw wire",
            third.window_id == first["window_id"]
            and third.start_utc.strftime(RFC3339) == first["t_start"]
            and third.end_utc.strftime(RFC3339) == first["t_end"],
            f"{third.window_id} [{third.start_utc.strftime(RFC3339)} .. "
            f"{third.end_utc.strftime(RFC3339)})")

    code, rows = wire("GET", st.base, "/training/windows", params={"user_id": USER_OK})
    R.check("exactly ONE row exists — a retry did not mint a second window",
            code == 200 and isinstance(rows, list) and len(rows) == 1,
            f"{len(rows) if isinstance(rows, list) else '?'} row(s)")
    code, rows_open = wire("GET", st.base, "/training/windows",
                           params={"user_id": USER_OK, "state": "open"})
    R.check("the enumeration read filters by state", code == 200 and len(rows_open) == 1
            and rows_open[0]["state"] == "open")
    code, rows_done = wire("GET", st.base, "/training/windows",
                           params={"user_id": USER_OK, "state": "consolidated"})
    R.check("nothing is consolidated yet", code == 200 and rows_done == [])
    R.end()
    return {"win": third, "row": first}


def step5(st: Storage, win: Any, instants: dict) -> Any:
    R.start("STEP 5", "fetch the day-log over HTTP through continuum's HttpDayLogClient")
    from app.clients import day_log_client
    from app.clients.registry import HttpRecipeRegistry
    from app.config import get_settings
    from app.daylog import corpus_blocks

    settings = get_settings()
    R.check("continuum's settings really select the HTTP backend",
            settings.storage_clients == "http" and settings.storage_url == st.base,
            f"CONTINUUM_STORAGE_CLIENTS={settings.storage_clients} "
            f"STORAGE_URL={settings.storage_url}")

    registry = HttpRecipeRegistry(st.base, timeout=30.0)
    recipe = registry.fetch_recipe(settings.recipe_id)
    R.check("C13: the recipe came from storage over HTTP",
            recipe.recipe_id == settings.recipe_id,
            f"recipe_id={recipe.recipe_id} segment_seconds={recipe.segment_seconds} "
            f"block_segments={recipe.block_segments}")

    # Raw body first, so the contract stamp is checked against the wire, not against
    # whatever the client chose to keep.
    code, body = wire("GET", st.base, "/training/daylog",
                      params={"user_id": USER_OK, "window_id": win.window_id})
    if code != 200:
        R.check("GET /training/daylog", False, f"HTTP {code}: {json.dumps(body)[:500]}")
        R.end()
        raise StepAborted("no day-log to check")
    R.check("body is a C10 v1 addressed to the window we asked for",
            body.get("contract") == "C10" and str(body.get("version")) == "1"
            and body.get("user_id") == USER_OK
            and body.get("window_id") == win.window_id)
    R.check("the body stamps the SAME recipe_id continuum is training under",
            body.get("recipe_id") == recipe.recipe_id,
            f"body.recipe_id={body.get('recipe_id')} vs continuum's {recipe.recipe_id} "
            "— a mismatch here is a night audited under a recipe it was not trained under")
    R.check("the body records the fallback zone actually used",
            body.get("home_tz") == HOME_TZ, f"home_tz={body.get('home_tz')}")
    R.check("it carries a daylog_format_version and a content_fingerprint",
            bool(body.get("daylog_format_version")) and bool(body.get("content_fingerprint")),
            f"format={body.get('daylog_format_version')} "
            f"fingerprint={body.get('content_fingerprint')}")

    client = day_log_client(settings, recipe)
    R.check("the settings-selected day-log client is the HTTP one",
            type(client).__name__ == "HttpDayLogClient", type(client).__name__)
    daylog = client.fetch_daylog(win)
    R.check("the client parsed a non-empty day-log",
            len(daylog.segments) > 0 and len(daylog.blocks) >= 3,
            f"{len(daylog.segments)} segments, {len(daylog.blocks)} blocks")
    R.check("the client passes storage's content_fingerprint THROUGH (does not re-derive)",
            client.fingerprint(daylog) == body["content_fingerprint"],
            f"{client.fingerprint(daylog)}")

    print()
    print("  ---- REAL RENDERED BLOCK TEXT, as it came off the socket ----------------")
    for blk in daylog.blocks:
        print(f"  [{blk.block_id}]  seg_ids={len(blk.seg_ids)}  anchors={blk.anchors}")
        for line in blk.text.splitlines():
            print(f"      | {line}")
    print("  -------------------------------------------------------------------------")
    print()

    # --- did device_tz actually beat home_tz? ------------------------------------
    def header_for(zone: str, first_bucket: datetime, last_bucket_end: datetime) -> str:
        z = ZoneInfo(zone)
        a, b = first_bucket.astimezone(z), last_bucket_end.astimezone(z)
        return (f"On {a.date().isoformat()}, around {a.strftime('%H:%M')}–"
                f"{b.strftime('%H:%M')} local time:")

    trav_t, notz_t, home_t = instants["trav_t"], instants["notz_t"], instants["home_t"]
    # Segment buckets sit on a global 10 s epoch grid, so a block's first/last bucket
    # boundaries are the floor/ceil of its records' event instants. These are the exact
    # instants storage's renderer converts.
    expected_travel = header_for(TRAVEL_TZ, trav_t, trav_t + timedelta(seconds=30))
    expected_fallback = header_for(HOME_TZ, notz_t, notz_t + timedelta(seconds=20))
    expected_home = header_for(HOME_TZ, home_t, home_t + timedelta(seconds=100))
    would_have_been = header_for(HOME_TZ, trav_t, trav_t + timedelta(seconds=30))

    headers = [b.text.splitlines()[0] for b in daylog.blocks]
    R.check("the TRAVEL block is anchored in the DEVICE's zone (Asia/Tokyo)",
            expected_travel in headers,
            f"expected {expected_travel!r}\n           got      {headers}")
    R.check("...and that is NOT what home_tz would have produced",
            expected_travel != would_have_been and would_have_been not in headers,
            f"home_tz would have said {would_have_been!r} — different date AND clock, "
            "so device_tz demonstrably won")
    R.check("the NO-device_tz block falls back to the profile's home_tz",
            expected_fallback in headers,
            f"expected {expected_fallback!r}")
    R.check("the at-home block renders in home_tz too (device agreed)",
            expected_home in headers, f"expected {expected_home!r}")

    body_texts = "\n".join(b.text for b in daylog.blocks)
    R.check("the rendered text carries all three labelled channels",
            "Scene: " in body_texts and "Heard: " in body_texts
            and "World text (OCR): " in body_texts,
            "captions, ASR (incl. the diarized sub-spans) and OCR all survived the seam")
    R.check("a diarized transcript's sub-spans landed in their OWN buckets",
            sum(len(s.asr) for s in daylog.segments) >= 4,
            f"{sum(len(s.asr) for s in daylog.segments)} asr lines across "
            f"{sum(1 for s in daylog.segments if s.asr)} segments")

    eligible = corpus_blocks(daylog, recipe.quality_min)
    R.check("every block is eligible for amplification (C2 v0 carries no quality)",
            len(eligible) == len(daylog.blocks), f"{len(eligible)} eligible blocks")
    R.end()
    return recipe


def step6(st: Storage, win: Any, recipe: Any, tmp: Path) -> Any:
    R.start("STEP 6", "run continuum's cycle end to end over the HTTP clients "
                      "(TRAINER_BACKEND=mock)")
    from app.clients import recipe_registry, window_ledger
    from app.config import get_settings
    from app.cycle import run_cycle
    from app.publish import ModelDirectory

    settings = get_settings()
    R.check("trainer backend is the mock (no GPU, deterministic)",
            settings.trainer_backend == "mock", settings.trainer_backend)
    R.check("var_dir is the throwaway one, not the service's 66 GB var/",
            Path(settings.var_dir).resolve().is_relative_to(tmp.resolve()),
            settings.var_dir)

    registry = recipe_registry(settings)
    ledger = window_ledger(settings)
    t0 = time.monotonic()
    result = run_cycle(win, registry=registry, recipe=recipe, windows=ledger)
    elapsed = time.monotonic() - t0

    R.check("the cycle reached a TERMINAL status", result.status in
            ("published", "gate_failed", "frozen", "skipped_no_data"),
            f"status={result.status} in {elapsed:.1f}s")
    R.check("and that status is `published`", result.status == "published",
            f"status={result.status}; gate.passed="
            f"{result.gate.passed if result.gate else None}; "
            f"reasons={result.gate.reasons if result.gate else None}")
    R.check("every stage ran (nothing silently skipped on a first night)",
            result.stages_run == ["daylog", "amplify", "replay_mix", "train", "gate",
                                  "publish"],
            f"stages_run={result.stages_run} stages_skipped={result.stages_skipped}")
    R.check("an adapter version was produced", bool(result.adapter_version),
            f"adapter_version={result.adapter_version}")
    R.check("the cycle used storage's window_id unchanged",
            result.window_id == win.window_id, result.window_id)

    # Read through the real ModelDirectory API rather than a hand-built path: the
    # layout is continuum's business and re-encoding it here would let this script
    # fail (or pass) for a reason that has nothing to do with the seam.
    directory = ModelDirectory(settings.var_dir)
    rows = directory.entries(USER_OK)
    mine = [r for r in rows if r.get("training_window") == win.window_id]
    R.check("a C5 entry was appended naming storage's window as training_window",
            len(mine) == 1 and mine[0].get("status") == "active",
            f"{len(mine)} entry: "
            f"{ {k: mine[0].get(k) for k in ('user_id','adapter_version','training_window','recipe_id','status')} if mine else None }")
    alias = directory.active(USER_OK)
    R.check("the serving alias moved to this night's adapter",
            isinstance(alias, dict) and alias.get("training_window") == win.window_id
            and alias.get("adapter_version") == result.adapter_version,
            f"active.json = {alias}")

    code, ledger_body = wire("GET", st.base, f"/reservoir/{USER_OK}")
    admitted = [e["window_id"] for e in ledger_body.get("entries", [])] \
        if isinstance(ledger_body, dict) else []
    R.check("C14: the amplified corpus was admitted to storage's reservoir OVER HTTP",
            code == 200 and admitted == [win.window_id],
            f"HTTP {code}, ledger entries = {admitted}")
    R.check("the C14 body is a C14 v0",
            isinstance(ledger_body, dict) and ledger_body.get("contract") == "C14"
            and str(ledger_body.get("version")) == "0")

    # Re-running the SAME window is the property storage's get-or-create exists to
    # protect. If it did not hold, a retried night would re-train, append a second C5
    # row and double-admit to an append-only reservoir.
    replay = run_cycle(win, registry=registry, recipe=recipe, windows=ledger)
    R.check("re-running the same window replays its outcome with ZERO side effects",
            replay.status == result.status
            and replay.adapter_version == result.adapter_version
            and replay.stages_run == [],
            f"status={replay.status} adapter_version={replay.adapter_version} "
            f"stages_run={replay.stages_run} stages_skipped={replay.stages_skipped}")
    rows2 = directory.entries(USER_OK)
    R.check("no second C5 entry", len(rows2) == len(rows), f"{len(rows2)} rows")
    code, ledger_body2 = wire("GET", st.base, f"/reservoir/{USER_OK}")
    R.check("no double admission into the append-only reservoir",
            len(ledger_body2.get("entries", [])) == 1,
            f"{len(ledger_body2.get('entries', []))} entries")
    R.end()
    return result


def step7(st: Storage, win: Any, result: Any) -> None:
    R.start("STEP 7", "close the window and prove the watermark rule "
                      "(`published` advances; nothing else does)")
    from app.clients import window_ledger
    from app.config import get_settings

    ledger = window_ledger(get_settings())

    closed = ledger.close(win, result.status)
    R.check("close() records the outcome and consolidates the row",
            closed.state == "consolidated" and closed.outcome == "published",
            f"state={closed.state} outcome={closed.outcome}")
    R.check("bounds are unchanged by the close",
            closed.start_utc == win.start_utc and closed.end_utc == win.end_utc,
            f"[{closed.start_utc.strftime(RFC3339)} .. "
            f"{closed.end_utc.strftime(RFC3339)})")
    code, again = wire("POST", st.base, f"/training/windows/{win.window_id}/close",
                       json_body={"user_id": USER_OK, "outcome": "published"})
    R.check("re-closing with the SAME outcome is an idempotent retry", code == 200,
            f"HTTP {code}")
    code, conflict = wire("POST", st.base, f"/training/windows/{win.window_id}/close",
                          json_body={"user_id": USER_OK, "outcome": "gate_failed"})
    R.check("re-closing with a DIFFERENT outcome is 409, never a rewrite of history",
            code == 409, f"HTTP {code}: {json.dumps(conflict)[:200]}")

    w1_end = win.end_utc.strftime(RFC3339)

    # --- the advancing half -------------------------------------------------------
    w2, waited = open_window_when_clock_allows(lambda: ledger.open(USER_OK, tz=HOME_TZ))
    R.note(f"waited {waited:.1f}s for storage's ingest clock to tick past the previous "
           f"window end (409 'not strictly greater than the floor' is the collision "
           f"guard, i.e. storage behaving correctly)")
    R.check("PUBLISHED advanced the watermark: W2.t_start == W1.t_end",
            w2.start_utc.strftime(RFC3339) == w1_end,
            f"W1.t_end={w1_end}  W2.t_start={w2.start_utc.strftime(RFC3339)}")
    R.check("W2 is a NEW window with a strictly greater id",
            w2.window_id != win.window_id and w2.window_id > win.window_id,
            f"{win.window_id} < {w2.window_id}  (string compare == chronological order)")

    # --- the NON-advancing half ---------------------------------------------------
    w2_closed = ledger.close(w2, "gate_failed")
    R.check("W2 closes with a non-publishing outcome", w2_closed.outcome == "gate_failed")

    w3, waited = open_window_when_clock_allows(lambda: ledger.open(USER_OK, tz=HOME_TZ))
    R.check("gate_failed did NOT advance the watermark: W3.t_start is still W1.t_end",
            w3.start_utc.strftime(RFC3339) == w1_end,
            f"W1.t_end={w1_end}  W3.t_start={w3.start_utc.strftime(RFC3339)}  "
            f"(NOT W2.t_end={w2.end_utc.strftime(RFC3339)})")
    R.check("so W3 is a STRICT SUPERSET of W2: same start, later end",
            w3.start_utc == w2.start_utc and w3.end_utc > w2.end_utc,
            f"W2=[{w2.start_utc.strftime(RFC3339)} .. {w2.end_utc.strftime(RFC3339)})  "
            f"W3=[{w3.start_utc.strftime(RFC3339)} .. {w3.end_utc.strftime(RFC3339)})")
    R.check("W3 covers everything W2 covered and strictly more",
            w3.start_utc <= w2.start_utc and w3.end_utc > w2.end_utc
            and (w3.start_utc, w3.end_utc) != (w2.start_utc, w2.end_utc),
            "the failed night's material is ABSORBED, not lost — the design-of-record's "
            "failed-day merge, obtained structurally")

    # --- and the enumeration read agrees with all of it ---------------------------
    consolidated = ledger.enumerate(USER_OK, tz=HOME_TZ, state="consolidated")
    ids = [w.window_id for w in consolidated]
    R.check("enumeration lists exactly the two closed windows, oldest first",
            ids == sorted(ids) and ids == [win.window_id, w2.window_id], f"{ids}")
    outcomes = {w.window_id: w.outcome for w in consolidated}
    R.check("and it carries each one's recorded outcome",
            outcomes == {win.window_id: "published", w2.window_id: "gate_failed"},
            f"{outcomes}")
    prior = ledger.prior_windows(USER_OK, w3.window_id, tz=HOME_TZ)
    # This assertion USED to expect both windows, and in doing so it encoded the H2
    # defect as correct behaviour: it was green while the replay pool silently
    # included nights that never entered the adapter. Replay is ANTI-FORGETTING, so
    # only a PUBLISHED night can be rehearsed — W2 gate-failed, so its material was
    # never trained and has nothing to be forgotten. Worse, because a failed window
    # does not advance the watermark, W2's records are already back in W3 as
    # tonight's FRESH corpus; rehearsing them too spent measured 50% of the budget
    # re-teaching text the night was learning anyway.
    R.check("prior_windows(W3) is the replay input — PUBLISHED nights only",
            [w.window_id for w in prior] == [win.window_id],
            f"{[w.window_id for w in prior]}")
    R.check("...so the gate-failed W2 is EXCLUDED, not merely ordered after",
            all(w.window_id != w2.window_id for w in prior),
            f"{[w.window_id for w in prior]}")

    # Leave nothing open, so the ledger we tear down is not mid-flight.
    ledger.close(w3, "skipped_no_data")
    code, rows_open = wire("GET", st.base, "/training/windows",
                           params={"user_id": USER_OK, "state": "open"})
    R.check("no window is left open for this user", code == 200 and rows_open == [])
    R.end()


def step7b(st: Storage, tmp: Path) -> None:
    """The SECOND night — one step beyond the eight this script was briefed with.

    It is here because without it a green verdict would be misleading. Steps 1-7 prove a
    user's FIRST night over HTTP, where the reservoir is empty and there is provably
    nothing to rehearse. Night two is the first night that owes rehearsal, and rehearsal
    is what rescues sequential consolidation from collapse — so "the seam works" is not a
    safe thing to tell anyone until night two has actually been run.

    The check branches on the pinned recipe's `replay.source`, and BOTH branches assert
    something falsifiable:

      * `amp`   — C14 serves the ledger, never the corpus bodies, so amp replay has no
                  HTTP implementation. The required behaviour is that the night RAISES,
                  naming `rawlog`. The failure this pins is not the raise: it is a
                  silent `return ""`, a night that trains with no rehearsal at all and
                  reports success, which is precisely the collapse replay exists to
                  prevent. That would pass every other check in this file.
      * `rawlog` — the locked architecture. The night must reach a terminal status and
                  its rehearsal must come from the window ENUMERATION read.
    """
    R.start("STEP 7b", "the SECOND night — does a night that OWES rehearsal run over HTTP?")
    from app.clients import recipe_registry, window_ledger
    from app.config import get_settings
    from app.cycle import run_cycle

    user = "u-seamcheck-night2"
    settings = get_settings()
    reg = recipe_registry(settings)
    recipe = reg.fetch_recipe(settings.recipe_id)
    ledger = window_ledger(settings)

    code, _ = wire("PUT", st.base, f"/users/{user}/profile", json_body={"home_tz": HOME_TZ})
    R.check("second user has a profile", code == 200)

    day = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    base = datetime(day.year, day.month, day.day, 14, tzinfo=timezone.utc)

    def post(tag: str, t0: datetime) -> bool:
        okall = True
        for i in range(3):
            rec = c2_record(user_id=user, rid=f"r-{tag}-{i}", chunk=f"c-{tag}-{i}",
                            t_start=t0 + timedelta(seconds=20 * i),
                            t_end=t0 + timedelta(seconds=20 * i + 8),
                            kind="caption", device_tz=HOME_TZ,
                            text=f"night {tag}, scene {i}: a desk by a window, papers "
                                 f"spread out, a cold cup of coffee")
            c, _b = wire("POST", st.base, "/context/records", json_body=rec)
            okall = okall and c == 200
        time.sleep(1.5)
        return okall

    R.check("night 1's records land", post("n1", base))
    w1, _ = open_window_when_clock_allows(lambda: ledger.open(user, tz=HOME_TZ))
    r1 = run_cycle(w1, registry=reg, recipe=recipe, windows=ledger)
    R.check("night 1 publishes (empty reservoir: nothing to rehearse)",
            r1.status == "published", f"status={r1.status} window={w1.window_id}")
    ledger.close(w1, r1.status)

    R.check("night 2's records land", post("n2", base + timedelta(hours=3)))
    w2, _ = open_window_when_clock_allows(lambda: ledger.open(user, tz=HOME_TZ))
    code, led = wire("GET", st.base, f"/reservoir/{user}")
    R.check("the reservoir now HAS history, so night 2 owes rehearsal",
            code == 200 and len(led.get("entries", [])) == 1,
            f"{len(led.get('entries', []))} prior admission")

    outcome: Any = None
    raised: Optional[BaseException] = None
    try:
        outcome = run_cycle(w2, registry=reg, recipe=recipe, windows=ledger)
    except BaseException as exc:  # noqa: BLE001 — the raise IS the observation
        raised = exc

    R.show("pinned recipe", f"{recipe.recipe_id}  replay.source={recipe.replay_source!r} "
                            f"frac={recipe.replay_frac}")
    if recipe.replay_source == "amp":
        refused = R.check("night 2 REFUSES rather than silently rehearsing nothing",
                          isinstance(raised, NotImplementedError),
                          f"raised {type(raised).__name__ if raised else 'nothing'}"
                          + (f"; status={outcome.status}" if outcome is not None else ""))
        named = R.check("...and the refusal names the supported source (`rawlog`)",
                        "rawlog" in str(raised) and "C14" in str(raised),
                        str(raised)[:220])
        if not (refused and named):
            # A night that trains with no rehearsal and REPORTS SUCCESS is a defect, not
            # a blocker. Say so, and do not print the blocker banner over the top of it.
            R.note("the two checks above FAILED, so what follows is a BUG and not the "
                   "known contract limitation: a night that owed rehearsal did not get "
                   "it and did not say so.")
            ledger.close(w2, "crashed")
            R.end()
            return
        print()
        print("  " + "!" * 74)
        print("  !! BLOCKER, and it is a CONTRACT fact, not a bug in either service:")
        print(f"  !! the pinned recipe {recipe.recipe_id} sets replay.source='amp', and amp")
        print("  !! replay pools the amplified CORPUS BODIES. C14 serves the reservoir")
        print("  !! LEDGER only. So over HTTP a user's FIRST night runs and every night")
        print("  !! after it RAISES. Nights 1..n over HTTP need a recipe pinning")
        print("  !! replay.source='rawlog' (prior day-logs re-read via C10) — a recipe")
        print(f"  !! fork, i.e. a founder decision, not a code change. {RECIPE_V11} IS")
        print("  !! that fork and STEP 7c below runs two full nights under it. What is")
        pins = shipped_recipe_defaults()
        unpinned = {k: v for k, v in pins.items() if v != RECIPE_V11}
        if unpinned:
            print("  !! left is a DEPLOYMENT re-pin, on BOTH services — still on the old")
            print(f"  !! recipe: {', '.join(f'{k}={v}' for k, v in unpinned.items())}")
        else:
            print(f"  !! left is NOTHING: both services now ship {RECIPE_V11} as their")
            print(f"  !! default ({', '.join(f'{k}={v}' for k, v in pins.items())}), so a")
            print("  !! fresh deployment runs nights 1..n. This banner documents what the")
            print("  !! v1.0 branch above still proves, not an outstanding action.")
        print("  " + "!" * 74)
        print()
        if unpinned:
            R.blocker(
                f"The SHIPPED default recipe still stops at night one over HTTP. "
                f"{recipe.recipe_id} sets replay.source='amp'; amp replay needs the "
                f"amplified corpus BODIES and C14 serves only the LEDGER, so every night "
                f"after the first raises NotImplementedError (asserted above). "
                f"{RECIPE_V11} fixes it and STEP 7c proves two nights end to end under "
                f"it — but these defaults are still on the old recipe: "
                + ", ".join(f"{k}={v}" for k, v in unpinned.items())
                + ". Nothing is fixed for a real deployment until BOTH are re-pinned: "
                "storage stamps the day-log's recipe_id and continuum records C5 lineage, "
                "so re-pinning one alone trains under a recipe the artifact is not "
                "labelled with, which is unfalsifiable afterwards.")
        ledger.close(w2, "crashed")
    elif recipe.replay_source == "rawlog":
        R.check("night 2 reached a terminal status", raised is None and outcome is not None,
                f"raised {type(raised).__name__ if raised else 'nothing'}")
        if outcome is not None:
            R.check("night 2 published", outcome.status == "published",
                    f"status={outcome.status}")
            journal = (Path(settings.var_dir) / "journal" / user / f"{w2.window_id}.json")
            mix = json.loads(journal.read_text())["stages"].get("replay_mix", {}) \
                if journal.exists() else {}
            R.check("its rehearsal came from a PRIOR day-log, re-read over C10",
                    mix.get("replay_source") == "rawlog" and mix.get("replay_chars", 0) > 0,
                    f"replay_mix journal entry = {mix}")
            ledger.close(w2, outcome.status)
    else:
        R.check(f"replay.source {recipe.replay_source!r} is one this script knows about",
                False, "neither 'amp' nor 'rawlog' — the seam's behaviour is unspecified")
    R.end()


def _post_night(st: Storage, user: str, tag: str, base: datetime, mark: str) -> int:
    """Two clusters of three C2 records, ~10 minutes of event time apart.

    Two clusters and not one because the 60 s scene-boundary gap then puts them in
    DIFFERENT blocks, so the day-log has more than one paragraph for replay to sample
    from — a one-block day-log would make "replay picked prior day-log text" true for a
    reason that is really "there was only one thing it could pick".

    `mark` is planted in every record's text, so whichever paragraphs the sampler
    happens to choose, the marker rides along.
    """
    accepted = 0
    for c, cluster_at in enumerate((base, base + timedelta(minutes=10))):
        specs = [
            ("caption",
             f"{mark}: a long desk under a window, two monitors, a stack of printed "
             f"pages held down by a mug, cluster {c}"),
            ("transcript",
             f"{mark}: we said we would cut the release once the migration owner is "
             f"named, and nobody has named one yet, cluster {c}"),
            ("ocr",
             f"{mark} BOARD: release cut pending / migration owner TBD / retro moved "
             f"to friday afternoon, cluster {c}"),
        ]
        for i, (kind, text) in enumerate(specs):
            t0 = cluster_at + timedelta(seconds=20 * i)
            rec = c2_record(user_id=user, rid=f"r-{tag}-{c}{i}", chunk=f"c-{tag}-{c}{i}",
                            t_start=t0, t_end=t0 + timedelta(seconds=8),
                            kind=kind, device_tz=HOME_TZ, text=text)
            code, _body = wire("POST", st.base, "/context/records", json_body=rec)
            if code == 200:
                accepted += 1
    return accepted


def step7c(st: Storage) -> None:
    """The step 7b's blocker asked for: TWO nights over HTTP under consolidation-v1.1.

    7b proves the default pin (`amp`) cannot get past night one over HTTP. This step
    proves the fork does, and — the part that actually matters — that night two's
    rehearsal was REAL and came from night one's DAY-LOG. A replay that silently
    returned "" would satisfy "night two reached a terminal status" while being exactly
    the collapse replay exists to prevent, so every claim here is pinned to something
    that would break if it did:

      * night ONE's replay is 0 characters (nothing prior exists) and night TWO's is
        not — the two nights differ in the one way the flip is supposed to make them
        differ;
      * night two's TRAINING corpus is strictly larger than its AMPLIFIED corpus, and
        is that corpus plus a tail;
      * night one's marker is in that tail and is NOT anywhere in night two's
        amplified corpus, so it can only have arrived by replay;
      * every paragraph of the tail appears VERBATIM in night one's day-log as fetched
        over C10, and NONE of them appears in night one's amplified corpus — which is
        precisely what C14 holds. That is the difference between "replay happened" and
        "replay happened FROM THE DAY-LOG and not from the reservoir".
    """
    R.start("STEP 7c", f"{RECIPE_V11} (replay.source=rawlog): TWO nights over HTTP, "
                       "night 2 rehearsing from night 1's DAY-LOG")
    from app.clients import HttpReservoirClient, day_log_client, recipe_registry
    from app.clients import window_ledger
    from app.config import get_settings
    from app.cycle import run_cycle
    from app.publish import ModelDirectory
    from app.renderer import blocks_text
    from app.reservoir import ReservoirEntry

    # ---- re-pin BOTH services, and reboot storage on the same DB --------------------
    ok, info = st.restart(daylog_recipe_id=RECIPE_V11)
    if not R.check(f"storage rebooted with STORAGE_DAYLOG_RECIPE_ID={RECIPE_V11}",
                   ok, info):
        R.note("server log tail:\n" + st.log_tail())
        R.end()
        return
    code, body = wire("GET", st.base, f"/users/{USER_OK}/profile")
    R.check("the reboot kept the SAME db (an earlier user's profile is still there)",
            code == 200 and isinstance(body, dict) and body.get("home_tz") == HOME_TZ,
            f"HTTP {code}: {json.dumps(body)[:140]}")
    code, rows = wire("GET", st.base, "/training/windows", params={"user_id": USER_OK})
    R.check("...and the SAME window ledger (STEP 7's rows survived the reboot)",
            code == 200 and isinstance(rows, list) and len(rows) == 3,
            f"{len(rows) if isinstance(rows, list) else '?'} window row(s) for {USER_OK}")

    # ---- HALF-WAY THROUGH THE RE-PIN: storage is on the fork, continuum is not ----
    # This is not a contrived state, it is the middle of the deployment step this very
    # function performs, and it is where F3 lives: storage renders honestly under its
    # own pin and stamps what it rendered, so the day-log that comes back is a
    # perfectly good v1.1 artifact and a night trained on it would be RECORDED in C5
    # as trained under v1.0. Only the consumer holds both facts. Measured here over
    # the real socket rather than asserted in prose.
    from app.clients.daylog_client import DayLogDialectMismatch
    half_pinned = get_settings()   # CONTINUUM_RECIPE_ID is still RECIPE_V10 here
    half_recipe = recipe_registry(half_pinned).fetch_recipe(half_pinned.recipe_id)
    R.check("continuum is still pinned to the OLD recipe at this instant",
            half_recipe.recipe_id == RECIPE_V10, f"{half_recipe.recipe_id}")
    stale_client = day_log_client(half_pinned, half_recipe)
    # Any of this user's windows will do: C10 is random-access, and storage
    # re-materializes a cached day-log whose recorded recipe_id no longer matches the
    # current pin -- so what comes back is stamped v1.1 no matter which we ask for.
    stale_window = window_ledger(half_pinned).enumerate(
        USER_OK, tz=HOME_TZ, state="consolidated")[0]
    refused: Optional[BaseException] = None
    try:
        stale_client.fetch_daylog(stale_window)
    except BaseException as exc:  # noqa: BLE001 - the TYPE is the finding
        refused = exc
    R.check("a HALF-FINISHED re-pin is REFUSED, not trained on (F3)",
            isinstance(refused, DayLogDialectMismatch),
            f"raised {type(refused).__name__ if refused else 'nothing'}: "
            f"{str(refused)[:150]}")
    R.check("...and the refusal NAMES both pins, so the operator knows what to change",
            "STORAGE_DAYLOG_RECIPE_ID" in str(refused or "")
            and "CONTINUUM_RECIPE_ID" in str(refused or ""))

    prev_pin = os.environ.get("CONTINUUM_RECIPE_ID")
    os.environ["CONTINUUM_RECIPE_ID"] = RECIPE_V11
    try:
        settings = get_settings()
        R.check("continuum is now pinned to the fork too",
                settings.recipe_id == RECIPE_V11, f"CONTINUUM_RECIPE_ID={settings.recipe_id}")

        # ---- the fork itself, read over HTTP from C13 -------------------------------
        code, raw10 = wire("GET", st.base, f"/recipes/{RECIPE_V10}")
        code2, raw11 = wire("GET", st.base, f"/recipes/{RECIPE_V11}")
        if not R.check("C13 serves BOTH recipes over HTTP", code == 200 and code2 == 200,
                       f"HTTP {code} / {code2}"):
            R.end()
            return
        R.check(f"{RECIPE_V10} pins replay.source='amp' and {RECIPE_V11} pins 'rawlog'",
                raw10["replay"]["source"] == "amp"
                and raw11["replay"]["source"] == "rawlog",
                f"{raw10['replay']['source']!r} -> {raw11['replay']['source']!r}")
        # "one knob" asserted, not taken on trust from the artifact's own note.
        strip = lambda d: {k: v for k, v in d.items()  # noqa: E731
                           if k not in ("recipe_id", "source", "note")}
        a, b = strip(raw10), strip(raw11)
        a["replay"] = {k: v for k, v in a["replay"].items() if k != "source"}
        b["replay"] = {k: v for k, v in b["replay"].items() if k != "source"}
        R.check("the fork changes EXACTLY that one knob — every other value is identical",
                a == b,
                "amplify dose, neg_frac, replay frac, neg_boost, LoRA shape, lr, epochs, "
                "objective, corpus knobs and the window boundary all byte-identical")
        R.check("so the day-log SHAPE is unchanged by the fork (same corpus knobs)",
                raw10["corpus"] == raw11["corpus"], f"corpus = {json.dumps(raw11['corpus'])}")

        registry = recipe_registry(settings)
        recipe = registry.fetch_recipe(settings.recipe_id)
        R.check("the parsed recipe continuum will train under is the fork",
                recipe.recipe_id == RECIPE_V11 and recipe.replay_source == "rawlog"
                and recipe.replay_frac == 0.3,
                f"recipe_id={recipe.recipe_id} replay_source={recipe.replay_source!r} "
                f"replay_frac={recipe.replay_frac}")

        ledger = window_ledger(settings)
        daylog_http = day_log_client(settings, recipe)
        R.check("the day-log client in play is the HTTP one",
                type(daylog_http).__name__ == "HttpDayLogClient",
                type(daylog_http).__name__)

        code, _ = wire("PUT", st.base, f"/users/{USER_V11}/profile",
                       json_body={"home_tz": HOME_TZ})
        R.check(f"{USER_V11} has a C12 profile", code == 200, f"HTTP {code}")

        day = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        base = datetime(day.year, day.month, day.day, 14, tzinfo=timezone.utc)

        # =========================== NIGHT ONE =====================================
        n1 = _post_night(st, USER_V11, "v11n1", base, MARK_N1)
        R.check("night 1's 6 C2 records land", n1 == 6, f"{n1}/6 accepted")
        time.sleep(1.5)   # every ingest_time strictly below the next window's t_end

        w1, _ = open_window_when_clock_allows(lambda: ledger.open(USER_V11, tz=HOME_TZ))
        R.check("night 1's window opened over HTTP", bool(w1.window_id),
                f"W1={w1.window_id} [{w1.start_utc.strftime(RFC3339)} .. "
                f"{w1.end_utc.strftime(RFC3339)})")

        code, body = wire("GET", st.base, "/training/daylog",
                          params={"user_id": USER_V11, "window_id": w1.window_id})
        R.check("night 1's C10 body is stamped with the FORK, not with v1.0",
                code == 200 and body.get("recipe_id") == RECIPE_V11,
                f"HTTP {code}, body.recipe_id={body.get('recipe_id') if isinstance(body, dict) else body!r}"
                " — the render and the stamp are one decision, so a night trained under "
                "v1.1 must not consume a day-log labelled v1.0")
        d1 = daylog_http.fetch_daylog(w1)
        R.check("night 1's day-log came over HTTP with MORE THAN ONE block",
                len(d1.blocks) >= 2,
                f"{len(d1.segments)} segments, {len(d1.blocks)} blocks")
        d1_text = blocks_text(d1.blocks)
        R.check("night 1's day-log carries the night-1 marker", MARK_N1 in d1_text,
                f"{d1_text.count(MARK_N1)} occurrence(s) of {MARK_N1!r}")

        r1 = run_cycle(w1, registry=registry, windows=ledger)
        R.check("night 1 reached a TERMINAL status of `published`",
                r1.status == "published",
                f"status={r1.status} stages_run={r1.stages_run}")
        R.check("night 1 ran every stage", r1.stages_run == [
                    "daylog", "amplify", "replay_mix", "train", "gate", "publish"],
                f"stages_run={r1.stages_run} stages_skipped={r1.stages_skipped}")

        var_dir = Path(settings.var_dir)
        work1 = var_dir / "cycles" / USER_V11 / w1.window_id

        def replay_mix(window_id: str) -> dict:
            path = var_dir / "journal" / USER_V11 / f"{window_id}.json"
            if not path.exists():
                return {}
            return json.loads(path.read_text())["stages"].get("replay_mix", {})

        mix1 = replay_mix(w1.window_id)
        R.check("night 1 replayed rawlog and got ZERO characters (nothing prior exists)",
                mix1.get("replay_source") == "rawlog" and mix1.get("replay_chars") == 0,
                f"replay_mix = { {k: mix1.get(k) for k in ('replay_source', 'replay_chars')} }"
                " — this is the CONTRAST that makes night 2's non-zero replay mean "
                "something")
        amp1 = (work1 / "amplified.corpus.txt").read_text()
        train1 = (work1 / "train.corpus.txt").read_text()
        R.check("so night 1's training corpus IS its amplified corpus, exactly",
                train1 == amp1, f"amp={len(amp1)} chars, train={len(train1)} chars")

        closed1 = ledger.close(w1, r1.status)
        R.check("night 1's window closes `consolidated`/`published` (watermark advances)",
                closed1.state == "consolidated" and closed1.outcome == "published",
                f"state={closed1.state} outcome={closed1.outcome}")

        # =========================== NIGHT TWO =====================================
        n2 = _post_night(st, USER_V11, "v11n2", base + timedelta(hours=4), MARK_N2)
        R.check("night 2's 6 C2 records land, ingested AFTER night 1's window closed",
                n2 == 6, f"{n2}/6 accepted")
        time.sleep(1.5)

        w2, waited = open_window_when_clock_allows(lambda: ledger.open(USER_V11, tz=HOME_TZ))
        R.check("night 2's window is a DIFFERENT, strictly greater window",
                w2.window_id != w1.window_id and w2.window_id > w1.window_id,
                f"W1={w1.window_id}  W2={w2.window_id}  (waited {waited:.1f}s for the "
                f"ingest clock to tick past W1.t_end)")
        R.check("the watermark handed off: W2.t_start == W1.t_end",
                w2.start_utc == w1.end_utc,
                f"W1.t_end={w1.end_utc.strftime(RFC3339)}  "
                f"W2.t_start={w2.start_utc.strftime(RFC3339)}")

        prior = ledger.prior_windows(USER_V11, w2.window_id, tz=HOME_TZ)
        R.check("the ENUMERATION read is what offers night 1 as night 2's replay input",
                [w.window_id for w in prior] == [w1.window_id],
                f"prior_windows(W2) = {[w.window_id for w in prior]}")

        raised: Optional[BaseException] = None
        r2 = None
        try:
            r2 = run_cycle(w2, registry=registry, windows=ledger)
        except BaseException as exc:  # noqa: BLE001 — a raise here IS the finding
            raised = exc

        # THE CHECK THAT MATTERS: under `amp` this is where night 2 died.
        if not R.check("night 2 did NOT fail for want of amp corpora",
                       raised is None,
                       f"raised {type(raised).__name__}: {str(raised)[:300]}" if raised
                       else "no exception"):
            R.end()
            return
        R.check("night 2 reached a TERMINAL status of `published`",
                r2.status == "published",
                f"status={r2.status}; gate.passed="
                f"{r2.gate.passed if r2.gate else None}")
        R.check("night 2 ran every stage", r2.stages_run == [
                    "daylog", "amplify", "replay_mix", "train", "gate", "publish"],
                f"stages_run={r2.stages_run} stages_skipped={r2.stages_skipped}")

        # ---- did replay ACTUALLY happen, and from WHERE? ----------------------------
        work2 = var_dir / "cycles" / USER_V11 / w2.window_id
        amp2 = (work2 / "amplified.corpus.txt").read_text()
        train2 = (work2 / "train.corpus.txt").read_text()
        mix2 = replay_mix(w2.window_id)
        R.check("night 2's journal records a rawlog replay of NON-ZERO length",
                mix2.get("replay_source") == "rawlog" and mix2.get("replay_chars", 0) > 0,
                f"replay_mix = { {k: mix2.get(k) for k in ('replay_source', 'replay_chars')} }")
        R.check("night 2's TRAINING corpus is strictly larger than its AMPLIFIED corpus",
                len(train2) > len(amp2),
                f"amplified={len(amp2)} chars, training={len(train2)} chars, "
                f"delta=+{len(train2) - len(amp2)}")
        R.check("...and is that amplified corpus plus a tail (not a different corpus)",
                train2.startswith(amp2), "train.corpus.txt == amplified + '\\n\\n' + replay")
        replay_text = train2[len(amp2):].lstrip("\n")
        R.check("the tail is exactly the replay the journal claims",
                len(replay_text) == mix2.get("replay_chars"),
                f"tail={len(replay_text)} chars, journal replay_chars="
                f"{mix2.get('replay_chars')}")

        R.check("night 1's marker is IN night 2's training corpus",
                MARK_N1 in replay_text,
                f"{replay_text.count(MARK_N1)} occurrence(s) of {MARK_N1!r} in the "
                "replayed tail")
        R.check("...and is NOWHERE in night 2's amplified-only corpus",
                MARK_N1 not in amp2,
                "so the only path it could have taken into tonight's training corpus "
                "is replay — this is the check a silent `return \"\"` fails")
        R.check("tonight's own marker is in the amplified corpus and NOT in the replay",
                MARK_N2 in amp2 and MARK_N2 not in replay_text,
                f"amp2 has {amp2.count(MARK_N2)} occurrence(s); replay has "
                f"{replay_text.count(MARK_N2)} — replay is PRIOR material only")

        paras = [p for p in replay_text.split("\n\n") if p]
        R.check("every replayed paragraph appears VERBATIM in night 1's C10 day-log",
                bool(paras) and all(p in d1_text for p in paras),
                f"{len(paras)} paragraph(s), "
                f"{sum(1 for p in paras if p in d1_text)} of them found in night 1's "
                f"day-log block text ({len(d1_text)} chars)")
        R.check("...and NONE of them appears in night 1's AMPLIFIED corpus (what C14 holds)",
                bool(paras) and not any(p in amp1 for p in paras),
                f"{sum(1 for p in paras if p in amp1)} of {len(paras)} found in the "
                f"admitted corpus ({len(amp1)} chars) — replay read the DAY-LOG, not "
                "the reservoir")
        R.check("the replayed text carries no amplifier fingerprint at all",
                "Recounting it plainly:" not in replay_text
                and "Contrary to what one might assume" not in replay_text
                and all("\n" in p for p in paras),
                "no style template, no deny-then-correct negative, and every paragraph "
                "is multi-line — amplified variants are single-line by construction")

        # The control: this SAME user, this SAME ledger, asked for `amp` instead. It
        # must still raise — v1.1 did not make amp work over HTTP, it stopped asking.
        amp_client = HttpReservoirClient(st.base, timeout=30.0, daylog_client=daylog_http)
        amp_raised: Optional[BaseException] = None
        try:
            amp_client.sample_replay(USER_V11, target_chars=len(amp2), frac=0.3, seed=1,
                                     before_window=w2.window_id, source="amp")
        except BaseException as exc:  # noqa: BLE001
            amp_raised = exc
        R.check("CONTROL: the same replay asked for `amp` still raises over HTTP",
                isinstance(amp_raised, NotImplementedError),
                f"raised {type(amp_raised).__name__ if amp_raised else 'nothing'} — "
                "v1.1 did not teach C14 to serve corpus bodies, it stopped asking it to")

        closed2 = ledger.close(w2, r2.status)
        R.check("night 2's window closes `consolidated`/`published` too",
                closed2.state == "consolidated" and closed2.outcome == "published",
                f"state={closed2.state} outcome={closed2.outcome}")

        # ---- C14 is still written for BOTH nights, and is still not the replay source
        code, led = wire("GET", st.base, f"/reservoir/{USER_V11}")
        entries = led.get("entries", []) if isinstance(led, dict) else []
        R.check("C14: BOTH nights admitted their amplified corpus to the reservoir",
                code == 200 and [e["window_id"] for e in entries] == [w1.window_id,
                                                                     w2.window_id],
                f"HTTP {code}, ledger entries = {[e['window_id'] for e in entries]}")
        R.check("both are stamped with the fork's recipe_id",
                [e.get("recipe_id") for e in entries] == [RECIPE_V11, RECIPE_V11],
                f"{[e.get('recipe_id') for e in entries]}")
        shas = [e.get("sha") for e in entries]
        R.check("each ledger sha is the sha of that night's AMPLIFIED corpus",
                shas == [hashlib.sha256(amp1.encode()).hexdigest(),
                         hashlib.sha256(amp2.encode()).hexdigest()],
                "the ledger's provenance is over the amplified bodies — text the replay "
                "above demonstrably never read")
        R.check("the C14 body is still a C14 v0 (the flip changed nothing about it)",
                isinstance(led, dict) and led.get("contract") == "C14"
                and str(led.get("version")) == "0",
                f"contract={led.get('contract') if isinstance(led, dict) else '?'} "
                f"version={led.get('version') if isinstance(led, dict) else '?'}")

        # ---- the enumeration read after two nights ---------------------------------
        code, rows = wire("GET", st.base, "/training/windows",
                          params={"user_id": USER_V11})
        R.check("storage lists BOTH windows for this user, oldest first",
                code == 200 and [r["window_id"] for r in rows] == [w1.window_id,
                                                                   w2.window_id],
                f"HTTP {code}: {[r['window_id'] for r in rows]}")
        R.check("both are `consolidated` with outcome `published`",
                [(r["state"], r.get("outcome")) for r in rows]
                == [("consolidated", "published"), ("consolidated", "published")],
                f"{[(r['state'], r.get('outcome')) for r in rows]}")
        enum = ledger.enumerate(USER_V11, tz=HOME_TZ, state="consolidated")
        R.check("continuum's enumerate() agrees with the raw wire",
                [w.window_id for w in enum] == [w1.window_id, w2.window_id],
                f"{[w.window_id for w in enum]}")
        code, rows_open = wire("GET", st.base, "/training/windows",
                               params={"user_id": USER_V11, "state": "open"})
        R.check("no window is left open for this user", code == 200 and rows_open == [],
                f"HTTP {code}: {rows_open}")
        R.check("prior_windows(W1) is EMPTY — night 1 legitimately had nothing to rehearse",
                ledger.prior_windows(USER_V11, w1.window_id, tz=HOME_TZ) == [],
                "which is why night 1's replay was 0 chars and night 2's was not")
        R.check("a window id still cannot be parsed back into a date",
                not hasattr(ReservoirEntry, "local_window_date"),
                "ReservoirEntry.local_window_date is gone (D18), so ENUMERATION is the "
                "only way prior windows can be discovered — the read exercised above")

        directory = ModelDirectory(settings.var_dir)
        mine = [r for r in directory.entries(USER_V11)]
        R.check("C5 lineage records both nights under the fork's recipe_id",
                [r.get("training_window") for r in mine] == [w1.window_id, w2.window_id]
                and {r.get("recipe_id") for r in mine} == {RECIPE_V11},
                f"{[(r.get('training_window'), r.get('recipe_id'), r.get('status')) for r in mine]}")

        print()
        print("  ---- THE REPLAYED TAIL OF NIGHT 2's TRAINING CORPUS ---------------------")
        for line in replay_text.splitlines()[:14]:
            print(f"      | {line}")
        if len(replay_text.splitlines()) > 14:
            print(f"      | ... ({len(replay_text.splitlines()) - 14} more lines)")
        print("  -------------------------------------------------------------------------")
        print()
    finally:
        if prev_pin is None:
            os.environ.pop("CONTINUUM_RECIPE_ID", None)
        else:
            os.environ["CONTINUUM_RECIPE_ID"] = prev_pin
    R.end()


def step8(st: Storage, tmp: Path, before: dict) -> None:
    R.start("STEP 8", "teardown — no processes, no files, nothing of the dev tree touched")
    stopped, how = st.stop()
    R.check("the storage process is gone", stopped, f"terminated via {how}, "
            f"exit code {st.proc.returncode if st.proc else '?'}")
    time.sleep(0.3)
    R.check(f"port {st.port} is free again", port_is_free(st.port))

    log_bytes = st.log_path.stat().st_size if st.log_path.exists() else 0
    shutil.rmtree(tmp, ignore_errors=True)
    R.check("the temp dir (db + blobs + reservoir + continuum var) is removed",
            not tmp.exists(), f"{tmp}  (server log was {log_bytes} bytes)")

    after_devdb = sha256_file(STORAGE_ROOT / "app" / "dev.db")
    R.check("storage/app/dev.db is byte-identical to before the run",
            after_devdb == before["devdb"],
            f"before {before['devdb'][:16]}…  after {after_devdb[:16]}…")
    R.check("storage/app/ has no new or changed entries",
            listing(STORAGE_ROOT / "app") == before["storage_app"],
            f"{len(listing(STORAGE_ROOT / 'app'))} entries")
    R.check("continuum/var/ is untouched",
            listing(CONTINUUM_ROOT / "var") == before["continuum_var"],
            f"{listing(CONTINUUM_ROOT / 'var')}")
    R.check("storage service root has no new entries",
            listing(STORAGE_ROOT) == before["storage_root"])
    R.end()


# ---------------------------------------------------------------------------------



def shipped_recipe_defaults() -> dict[str, str]:
    """The recipe id each service ships as its DEFAULT, read from source.

    Deliberately read from the files rather than from a live process or an env var:
    the question this answers is "what would a FRESH deployment do", and an env
    override in this script's own environment would mask exactly the misconfiguration
    the blocker exists to catch. Both sides matter and for different reasons — storage
    stamps `recipe_id` onto the C10 day-log body, continuum records it in C5 lineage —
    so a one-sided re-pin trains under a recipe the artifact is not labelled with.
    """
    import re
    here = Path(__file__).resolve().parents[1]
    out: dict[str, str] = {}
    probes = {
        "CONTINUUM_RECIPE_ID": (here / "app" / "config.py",
                                r'CONTINUUM_RECIPE_ID"\s*,\s*"([^"]+)"'),
        "STORAGE_DAYLOG_RECIPE_ID": (here.parent / "storage" / "app" / "daylog.py",
                                     r'_DEFAULT_RECIPE_ID\s*=\s*"([^"]+)"'),
    }
    for name, (path, pattern) in probes.items():
        try:
            m = re.search(pattern, path.read_text())
            out[name] = m.group(1) if m else "<unreadable>"
        except OSError:
            out[name] = "<missing>"
    return out

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--storage-python", default=str(STORAGE_ROOT / ".venv" / "bin" / "python"),
                    help="interpreter that runs the storage service (its own venv)")
    args = ap.parse_args(argv)

    storage_python = Path(args.storage_python)
    if not storage_python.exists():
        print(f"FATAL: storage interpreter not found: {storage_python}", file=sys.stderr)
        return 2

    print("=" * 78)
    print("LIVE SEAM CHECK — storage <-> continuum, two processes, real HTTP")
    print("=" * 78)
    print(f"  started        {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print(f"  this script    {sys.executable}")
    print(f"  storage server {storage_python}")
    print(f"  storage tree   {STORAGE_ROOT}")
    print(f"  continuum tree {CONTINUUM_ROOT}")

    before = {
        "devdb": sha256_file(STORAGE_ROOT / "app" / "dev.db"),
        "devdb_size": (STORAGE_ROOT / "app" / "dev.db").stat().st_size
        if (STORAGE_ROOT / "app" / "dev.db").exists() else 0,
        "storage_app": listing(STORAGE_ROOT / "app"),
        "storage_root": listing(STORAGE_ROOT),
        "continuum_var": listing(CONTINUUM_ROOT / "var"),
    }

    tmp = Path(tempfile.mkdtemp(prefix="seamcheck-"))
    port = free_port()
    if port == FLEET_PORT:  # astronomically unlikely; still not left to chance
        port = free_port()
    st = Storage(tmp, port, storage_python)

    # Continuum's own settings, for everything imported below and for the nightly
    # subprocess. Set BEFORE any continuum module is used — config reads env per call,
    # so this is the whole configuration surface.
    os.environ.update({
        "CONTINUUM_STORAGE_CLIENTS": "http",
        "STORAGE_URL": st.base,
        "CONTINUUM_VAR_DIR": str(tmp / "continuum_var"),
        "TRAINER_BACKEND": "mock",
        "MOCK_GATE": "auto",
        "CONTINUUM_RECIPE_ID": "consolidation-v1.0",
        "CONTINUUM_POLICY_ID": "gate-policy-v1.1",
        "CONTINUUM_HTTP_TIMEOUT": "60",
    })

    torn_down = False
    try:
        step1(st, tmp, before)
        step2(st, tmp, Path(sys.executable))
        instants = step3(st)
        opened = step4(st)
        recipe = step5(st, opened["win"], instants)
        result = step6(st, opened["win"], recipe, tmp)
        step7(st, opened["win"], result)
        step7b(st, tmp)
        step7c(st)
        step8(st, tmp, before)
        torn_down = True
    except StepAborted as exc:
        print(f"\n!! ABORTED: {exc}")
        for step_id in ("STEP 2", "STEP 3", "STEP 4", "STEP 5", "STEP 6", "STEP 7",
                        "STEP 7b", "STEP 7c", "STEP 8"):
            if not any(s[0] == step_id for s in R.steps):
                R.abort(step_id, "an earlier step aborted the run")
    except BaseException:  # noqa: BLE001 — report, never swallow
        print("\n!! UNHANDLED EXCEPTION — the run is a FAIL, not an error to ignore:")
        traceback.print_exc()
        print("\n---- storage server log tail ----")
        print(st.log_tail())
        print("---------------------------------")
        for step_id in ("STEP 1", "STEP 2", "STEP 3", "STEP 4", "STEP 5", "STEP 6",
                        "STEP 7", "STEP 7b", "STEP 7c", "STEP 8"):
            if not any(s[0] == step_id for s in R.steps):
                R.abort(step_id, "unhandled exception during the run")
    finally:
        if not torn_down:
            # Teardown is a GUARANTEE, not a step that only runs on the happy path.
            print("\n---- storage server log tail (run did not complete) ----")
            print(st.log_tail())
            print("--------------------------------------------------------")
            stopped, how = st.stop()
            shutil.rmtree(tmp, ignore_errors=True)
            print(f"  emergency teardown: process stopped={stopped} ({how}), "
                  f"temp dir removed={not tmp.exists()}")

    return R.summary()


if __name__ == "__main__":
    sys.exit(main())
