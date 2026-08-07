#!/usr/bin/env python3
"""cutover_wipe.py — the OD-2 fresh-forward wipe (DP rebuild Stage F, D19 license).

Wipes the DERIVED, processed corpus so the v1 world starts fresh; never touches
the sacred sources. The classification is the contract:

  WIPE (derived / processed / trainer state over the wiped corpus):
    storage  context_records   — C2 processed corpus (the thing OD-2 names)
    storage  day_logs          — materialized C10 cache over context_records
    storage  training_windows  — the D18 ledger over wiped windows; the watermark
                                 is derived from it, so fresh-forward resets both
    storage  reservoir/ files  — amplified corpora rendered FROM wiped day-logs
    DP       processed+pending — the journal (OD-2 names it); a done-row kept
                                 here would make a redelivered chunk SKIP forever
                                 against records that no longer exist
    continuum var learn-state  — journal/ state/ cycles/ adapters/
                                 model_directory/ reservoir/ (per-window trainer
                                 state hashed over wiped inputs)

  KEEP (sacred / declared / not derived):
    storage  turns             — /sessions turns (C4), bytes are sacred
    storage  raw_blobs + the raw_store/ tree — the /raw blob leg, bytes are sacred
    storage  user_profiles     — C12 profiles are DECLARED facts, not derived data
    storage  model_directory   — the C6 base-entry seed (config, not corpus)
    recording ledger           — capture history is recording's own truth; only
                                 its dp_state CLAIMS are at issue (see below)
    continuum var research dirs — diag/ parity/ phase3/ slurm/ (July experiment
                                 artifacts, nothing to do with the learn loop)

  RECORDING dp_state DISPOSITION (founder rules at the gate; --recording-claims):
    keep  (default) — 'processed' claims that name wiped C2s stay as documented
                      reconciliation noise: recording never re-emits them, the
                      gap-report may name them, /raw still holds their bytes.
    reset           — 'processed' -> NULL. Corrected at the GATE 1 verification
                      round: NULLed rows are selected by NO redrive path (the
                      chunk redrive queries dp_state='accepted' only; startup
                      re-enqueue drives 'received' SEGMENTS), so reset causes
                      no backfill — it is INERT METRIC INCOHERENCE: a chunk
                      that was processed reads as never-emitted forever, and
                      the dp_acked ⇔ C2-durable reading stops holding for those
                      rows. It buys nothing and breaks a ledger meaning; not
                      the default. 'accepted' rows are preserved under BOTH
                      dispositions — they are the buffered-chunk redrive the
                      cutover relies on.

Structure of the safety story, not just prose:
  * full-classification-or-refusal — every table in every DB must be named KEEP
    or WIPE above, and every continuum var/ subdir likewise; anything unknown
    aborts BOTH modes before a single row is touched;
  * the only SQL the wet path can run is DELETE FROM <an allowlisted table> (and
    the one dp_state UPDATE under --recording-claims reset); DDL, DROP and every
    KEEP table are structurally unreachable;
  * DRY-RUN IS MANDATORY and is the default: databases are opened read-only
    (sqlite URI mode=ro), and --execute additionally refuses to run while the
    freeze ports (:8083 v0 storage, :8085 v0 DP) still answer — the freeze comes
    first, per the Stage F order. The freeze covers every DB wet mode can touch:
    under --recording-claims reset, ledger.db becomes a wet target, so the
    recording port (:8084, --recording-port) is APPENDED IN CODE to the freeze
    list; under keep it is not a target and recording keeps capturing, which is
    the F1 design;
  * a MISSING wipe-target path (a mistyped --*-db / dir) ABORTS both modes — a
    silent no-op at F1 would be a corpse in the new world; --allow-missing is
    the explicit escape for the genuinely-absent case;
  * the wet manifest is computed by the same code path as the dry manifest, so
    "wet output must match the dry-run" is checkable line by line.

Usage:
  python3 cutover_wipe.py                 # dry-run (default): print the manifest
  python3 cutover_wipe.py --execute       # the wipe itself (post-freeze, gated)
  python3 cutover_wipe.py --execute --recording-claims reset   # founder's call
"""
from __future__ import annotations

import argparse
import socket
import sqlite3
import sys
from pathlib import Path

TREE = Path("/home/ubuntu/nmn/continual_learning/product/services")
V0_WORKTREE = Path("/home/ubuntu/nmn/dp-v0-live/product/services")

# --- the classification (the contract; see module docstring) -----------------
STORAGE_WIPE = ("context_records", "day_logs", "training_windows")
STORAGE_KEEP = ("turns", "raw_blobs", "user_profiles", "model_directory")
DP_WIPE = ("pending", "processed")
DP_KEEP: tuple[str, ...] = ()
RECORDING_KEEP = ("captures", "segments", "streams", "chunks")
RECORDING_WIPE: tuple[str, ...] = ()
CONTINUUM_WIPE_SUBDIRS = ("journal", "state", "cycles", "adapters",
                          "model_directory", "reservoir")
CONTINUUM_KEEP_SUBDIRS = ("diag", "parity", "phase3", "slurm")


def _tables(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'").fetchall()]


def _count(con: sqlite3.Connection, table: str) -> int:
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _open_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _classify_or_die(name: str, found: list[str], keep: tuple[str, ...],
                     wipe: tuple[str, ...]) -> None:
    unknown = sorted(set(found) - set(keep) - set(wipe))
    if unknown:
        sys.exit(f"ABORT: {name} holds table(s) this script has not classified "
                 f"KEEP or WIPE: {unknown} — a wipe may not run against a schema "
                 "it does not fully understand. Classify them (a code edit) first.")


def _missing(kind: str, name: str, path: Path, allow: bool) -> None:
    if allow:
        return
    sys.exit(f"ABORT: {name} {kind} is MISSING at {path} — a mistyped path would "
             "silently no-op the wipe and leave a corpse in the new world. Fix "
             "the path, or pass --allow-missing if it is genuinely absent.")


def db_manifest(name: str, path: Path, keep: tuple[str, ...],
                wipe: tuple[str, ...], allow_missing: bool = False,
                ) -> list[tuple[str, str, str, int]]:
    """(db, table, verdict, rows) rows for the manifest; aborts on the unknown
    and on a missing db (unless --allow-missing)."""
    if not path.exists():
        _missing("db", name, path, allow_missing)
        return [(name, "(missing db — allowed by flag)", "-", 0)]
    con = _open_ro(path)
    try:
        found = _tables(con)
        _classify_or_die(f"{name} ({path})", found, keep, wipe)
        return [(name, t, "WIPE" if t in wipe else "KEEP", _count(con, t))
                for t in sorted(found, key=lambda t: (t not in wipe, t))]
    finally:
        con.close()


def tree_files(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if p.is_file()) if root.exists() else 0


def continuum_manifest(var: Path, allow_missing: bool = False,
                       ) -> list[tuple[str, str, str, int]]:
    rows: list[tuple[str, str, str, int]] = []
    if not var.exists():
        _missing("dir", "continuum var", var, allow_missing)
        return [("continuum var", "(missing dir — allowed by flag)", "-", 0)]
    unknown = sorted(
        p.name for p in var.iterdir() if p.is_dir()
        and p.name not in CONTINUUM_WIPE_SUBDIRS + CONTINUUM_KEEP_SUBDIRS)
    if unknown:
        sys.exit(f"ABORT: continuum var/ holds unclassified subdir(s): {unknown} "
                 "— classify them KEEP or WIPE (a code edit) first.")
    for sub in CONTINUUM_WIPE_SUBDIRS:
        rows.append(("continuum var", f"{sub}/", "WIPE",
                     tree_files(var / sub)))
    for sub in CONTINUUM_KEEP_SUBDIRS:
        if (var / sub).exists():
            rows.append(("continuum var", f"{sub}/", "KEEP", tree_files(var / sub)))
    return rows


def recording_states(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    con = _open_ro(path)
    try:
        if "chunks" not in _tables(con):
            return {}
        return dict(con.execute(
            "SELECT COALESCE(dp_state,'NULL') s, COUNT(*) FROM chunks GROUP BY s"
        ).fetchall())
    finally:
        con.close()


def print_manifest(args) -> None:
    rows: list[tuple[str, str, str, int]] = []
    rows += db_manifest("storage dev.db", args.storage_db, STORAGE_KEEP,
                        STORAGE_WIPE, args.allow_missing)
    rows += db_manifest("DP journal (v0)", args.dp_db, DP_KEEP, DP_WIPE,
                        args.allow_missing)
    rows += db_manifest("recording ledger", args.recording_db,
                        RECORDING_KEEP, RECORDING_WIPE, args.allow_missing)
    if not args.reservoir_dir.exists():
        _missing("dir", "storage reservoir", args.reservoir_dir,
                 args.allow_missing)
        rows.append(("storage reservoir", "(missing dir — allowed by flag)", "-", 0))
    else:
        rows.append(("storage reservoir", "files under the dir", "WIPE",
                     tree_files(args.reservoir_dir)))
    rows += continuum_manifest(args.continuum_var, args.allow_missing)

    print(f"{'LEDGER':<18} {'TABLE / TREE':<28} {'VERDICT':<8} {'ROWS/FILES':>10}")
    print("-" * 68)
    for db, table, verdict, n in rows:
        print(f"{db:<18} {table:<28} {verdict:<8} {n:>10}")
    print("-" * 68)
    print("KEEP also covers, by never being opened for write at all: the raw_store/")
    print("blob tree (bytes are sacred), /sessions turns, C12 profiles, recording's")
    print("spool files. The DB file itself is kept in every case — rows are cleared,")
    print("the D27-migrated shape stays (the ladder no-ops on an already-migrated")
    print("table, so the cutover order cannot strand a DB shape).")

    states = recording_states(args.recording_db)
    print()
    print(f"recording chunks.dp_state breakdown: {states or '(no chunks table / no rows)'}")
    n_proc = states.get("processed", 0)
    print(f"DISPOSITION QUESTION for the founder — {n_proc} 'processed' claim(s) "
          f"would name wiped C2s at execution time:")
    print("  keep  (default): stays as documented reconciliation noise; recording")
    print("                   never re-emits them; /raw still holds their bytes.")
    print("  reset          : 'processed' -> NULL. No redrive path selects a NULL row")
    print("                   (chunk redrive queries dp_state='accepted' only; startup")
    print("                   re-enqueue drives 'received' segments), so this causes NO")
    print("                   backfill — it is inert metric incoherence: a processed")
    print("                   chunk reads as never-emitted forever, and the dp_acked <=>")
    print("                   C2-durable reading stops holding for those rows.")
    print("  'accepted' rows are preserved either way (the buffered redrive the")
    print(f"  cutover relies on). This run: --recording-claims {args.recording_claims}")


def freeze_check(ports: list[int]) -> None:
    still_up = []
    for port in ports:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                still_up.append(port)
    if still_up:
        sys.exit(f"ABORT: freeze port(s) {still_up} still listening — the wipe "
                 "runs only AFTER v0 storage/DP are stopped (the Stage F order). "
                 "Nothing was touched.")


def execute(args) -> None:
    ports = [int(p) for p in args.freeze_ports.split(",") if p]
    if args.recording_claims == "reset":
        # Reset writes ledger.db, so the freeze must cover recording too.
        # Appended in code, not via the flag: the operator cannot forget it.
        ports.append(args.recording_port)
    freeze_check(sorted(set(ports)))

    def wipe_db(name: str, path: Path, keep: tuple[str, ...],
                wipe: tuple[str, ...]) -> None:
        if not path.exists():
            _missing("db", name, path, args.allow_missing)
            print(f"[wet] {name}: missing — allowed by flag, skipped")
            return
        con = sqlite3.connect(path)
        try:
            _classify_or_die(f"{name} ({path})", _tables(con), keep, wipe)
            with con:  # one transaction per DB
                for table in wipe:
                    if table in _tables(con):
                        assert table not in keep  # structural: WIPE∩KEEP is empty
                        n = con.execute(f"DELETE FROM {table}").rowcount
                        print(f"[wet] {name}: DELETE FROM {table} -> {n} row(s)")
        finally:
            con.close()

    wipe_db("storage dev.db", args.storage_db, STORAGE_KEEP, STORAGE_WIPE)
    wipe_db("DP journal (v0)", args.dp_db, DP_KEEP, DP_WIPE)

    if args.recording_claims == "reset" and args.recording_db.exists():
        con = sqlite3.connect(args.recording_db)
        try:
            with con:
                n = con.execute(
                    "UPDATE chunks SET dp_state=NULL WHERE dp_state='processed'"
                ).rowcount
            print(f"[wet] recording ledger: dp_state 'processed' -> NULL on {n} row(s) "
                  "('accepted' preserved)")
        finally:
            con.close()
    else:
        print("[wet] recording ledger: claims kept (documented reconciliation noise)")

    if args.reservoir_dir.exists():
        n = 0
        for p in sorted(args.reservoir_dir.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink(); n += 1
            elif p.is_dir() and not any(p.iterdir()):
                p.rmdir()
        print(f"[wet] storage reservoir: {n} file(s) removed")

    if args.continuum_var.exists():
        import shutil
        for sub in CONTINUUM_WIPE_SUBDIRS:
            d = args.continuum_var / sub
            if d.exists():
                n = tree_files(d)
                shutil.rmtree(d)
                print(f"[wet] continuum var/{sub}: removed ({n} file(s))")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--storage-db", type=Path,
                    default=TREE / "storage" / "app" / "dev.db")
    ap.add_argument("--dp-db", type=Path,
                    default=V0_WORKTREE / "data-processing" / "var" / "dp.db")
    ap.add_argument("--recording-db", type=Path,
                    default=TREE / "recording" / "var" / "ledger.db")
    ap.add_argument("--reservoir-dir", type=Path,
                    default=TREE / "storage" / "app" / "reservoir")
    ap.add_argument("--continuum-var", type=Path,
                    default=TREE / "continuum" / "var")
    ap.add_argument("--freeze-ports", default="8083,8085",
                    help="wet mode refuses while any of these still listen")
    ap.add_argument("--recording-port", type=int, default=8084,
                    help="appended to the freeze list in code when "
                         "--recording-claims reset makes ledger.db a wet target")
    ap.add_argument("--recording-claims", choices=("keep", "reset"), default="keep")
    ap.add_argument("--allow-missing", action="store_true",
                    help="explicit escape: a missing wipe-target path is "
                         "otherwise an abort in BOTH modes")
    ap.add_argument("--execute", action="store_true",
                    help="run the wipe (default is the mandatory dry-run)")
    args = ap.parse_args(argv)

    mode = "WET RUN" if args.execute else "DRY-RUN (nothing will be touched)"
    print(f"=== OD-2 fresh-forward cutover wipe — {mode} ===\n")
    print_manifest(args)                    # aborts on any unclassified target
    if args.execute:
        print()
        execute(args)
        print()
        print("=== post-wipe verification (same manifest code path) ===\n")
        print_manifest(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
