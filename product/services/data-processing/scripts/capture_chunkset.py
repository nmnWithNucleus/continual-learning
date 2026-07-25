#!/usr/bin/env python3
"""Build a **chunkset** — the corpus `scripts/prompt_ab.py` scores an arm over (WS-H).

A chunkset is a directory of C1 envelopes (+ optional blobs, + optional ground truth) with
one `manifest.json`. It is deliberately *not* a database dump and *not* a `/context` read:
the eval path never touches storage, so a corpus can be captured, committed, diffed and
thrown away without any of it being a fact about a user.

    <chunkset>/
      manifest.json           # the whole index; truth is INLINE per chunk
      c1/<chunk_id>.json      # one C1 envelope per chunk (contract-valid)
      blobs/<chunk_id>.mp4    # optional — absent in a `headless` chunkset

Four modes:

  * ``slice``    — cut ONE real screen recording into span-length chunks (`ffmpeg -f
                   segment`, stream copy) and mint a C1 per segment. Ground truth is left
                   as empty stubs for a human labelling pass. This is how O-4's 30 real
                   clips and the 200-chunk pre-push corpus are made.
  * ``wrap``     — a directory of already-cut clips becomes a chunkset, one chunk per file.
  * ``synth``    — generate labelled synthetic "screens" with ffmpeg `lavfi` + `drawtext`.
                   Every string drawn on the frame is recorded as ground truth, so
                   `named_entity_recall` / `app_correct` / `propagation_rate` have an EXACT
                   denominator without a human labelling pass. No GPU, no network, and no
                   binaries in git (the blobs land in a scratch dir you point `--out` at).
  * ``headless`` — C1 + ground truth ONLY, no blobs. JSON-only, so it is committable:
                   `tests/fixtures/chunksets/**` is one of these. Under
                   `VIDEO_BACKEND=mock` the harness feeds an undecodable blob and
                   `clipprep` takes its documented synthetic-frames fallback, so the whole
                   mechanical scorer set runs headless and offline (house rule 5).

Ground truth per chunk (the `truth` object, all optional):

    app          the application/site that SHOULD be named          -> app_correct
    activity     the change verb that SHOULD appear                 -> change_verb_rate
    entities     named strings a correct caption may recover        -> named_entity_recall
    ocr_regions  what a perfect OCR pass would read (text/bbox/     -> the `truth` OCR
                 conf/role, the WS-C OcrRegion shape)                  injector + the
                                                                       corrupted-OCR arm

Usage:

    python scripts/capture_chunkset.py synth    --out /tmp/cs-synth --count 24 --span 10
    python scripts/capture_chunkset.py headless --out tests/fixtures/chunksets/smoke-v1
    python scripts/capture_chunkset.py slice    --out /tmp/cs-real --from-video screen.mp4 --span 60
    python scripts/capture_chunkset.py wrap     --out /tmp/cs-real --from-dir /path/to/clips
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CHUNKSET_VERSION = "1"

_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}

# The synthetic screen canvas. 1440x900 matches WS-B's calibration fixtures, so a synth
# chunkset exercises the same delta-gate operating point the thresholds were read at.
SYNTH_W, SYNTH_H, SYNTH_FPS = 1440, 900, 30


# ======================================================================== C1 envelopes

def _iso(dt: datetime) -> str:
    """RFC3339 with a literal ``Z`` — the form recording emits and the form D-05 requires
    ``build_c2`` to carry through verbatim (never re-rendered as ``+00:00``)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_c1(
    *,
    chunk_id: str,
    sequence: int,
    t_start: datetime,
    span_seconds: float,
    blob: bytes | None,
    user_id: str,
    device_id: str,
    stream_id: str,
) -> dict[str, Any]:
    """One contract-valid C1 envelope. ``blob_sha256``/``blob_bytes`` describe the real
    bytes when there are any; a headless chunkset declares the undecodable placeholder the
    harness feeds, so the envelope never claims a blob that does not exist."""
    payload = blob if blob is not None else b""
    return {
        "contract": "C1",
        "version": "0",
        "user_id": user_id,
        "device_id": device_id,
        "stream_id": stream_id,
        "sequence": sequence,
        "chunk_id": chunk_id,
        "modality": "video",
        "codec": "video/mp4",
        "t_start": _iso(t_start),
        "t_end": _iso(t_start + timedelta(seconds=span_seconds)),
        "blob_ref": f"raw/{user_id}/{chunk_id}.mp4",
        "blob_sha256": hashlib.sha256(payload).hexdigest(),
        "blob_bytes": len(payload),
    }


# ======================================================================== synthetic screens

@dataclass(frozen=True)
class Screen:
    """One labelled synthetic screen: what is drawn IS the ground truth."""
    key: str
    app: str                      # the string `app_correct` scores against
    activity: str                 # the change verb `change_verb_rate` looks for
    bg: str                       # lavfi background colour
    title: str                    # titlebar line (drawn at the top strip)
    subject: str                  # main-area subject line
    typed: str                    # the line that GROWS across the clip (the motion)
    entities: tuple[str, ...]     # named strings a correct caption may recover
    secret: str = ""              # drawn only when --with-secrets; exercises redaction


SCREENS: tuple[Screen, ...] = (
    Screen(
        key="ide",
        app="Visual Studio Code",
        activity="typing",
        bg="0x1e1e1e",
        title="executor.py - dataprocessing - Visual Studio Code",
        subject="def run_graph(resolved, ctx):",
        typed="futures = {s.name: loop.create_future() for s in resolved.enabled}",
        entities=("executor.py", "run_graph", "Visual Studio Code", "dataprocessing"),
    ),
    Screen(
        key="mail",
        app="Gmail",
        activity="typing",
        bg="white",
        title="Inbox (12) - Gmail",
        subject="Re: Q3 deck review",
        typed="Hi Sarah, the revised Q3 deck is attached, slide 14 has the new numbers",
        entities=("Gmail", "Q3 deck review", "Sarah", "Inbox"),
    ),
    Screen(
        key="chat",
        app="Slack",
        activity="reading",
        bg="0x3f0e40",
        title="platform-oncall - Nucleus - Slack",
        subject="marcus: the serve loop is down again on node-7",
        typed="acking, will drain and replace before the nightly window",
        entities=("Slack", "platform-oncall", "node-7", "marcus"),
    ),
    Screen(
        key="term",
        app="Terminal",
        activity="scrolling",
        bg="black",
        title="ubuntu@node-7: ~/nucleus/data-processing",
        subject="pytest -q tests/test_clip_pipeline_e2e.py",
        typed="465 passed in 60.94s",
        entities=("Terminal", "node-7", "pytest", "data-processing"),
    ),
    Screen(
        key="web",
        app="Safari",
        activity="scrolling",
        bg="0xf6f6f6",
        title="Attention Is All You Need - arxiv.org - Safari",
        subject="3.2.1 Scaled Dot-Product Attention",
        typed="We call our particular attention Scaled Dot-Product Attention",
        entities=("Safari", "arxiv.org", "Scaled Dot-Product Attention"),
    ),
    Screen(
        key="sheet",
        app="Numbers",
        activity="editing",
        bg="0xfafafa",
        title="pilot-costs-2026.numbers - Numbers",
        subject="node-hour rate  screen-hour cost  pilot users",
        typed="=B4*C4*250",
        entities=("Numbers", "pilot-costs-2026.numbers", "node-hour"),
    ),
)

# Drawn positions, in normalized (0..1) coordinates — these become the ground-truth OCR
# bboxes, so `assemble.assign_role` derives the SAME roles a real read would.
_TITLE_BOX = (0.02, 0.005, 0.62, 0.030)     # top strip     -> titlebar
_SUBJECT_BOX = (0.10, 0.330, 0.88, 0.375)   # mid content   -> main
_TYPED_BOX = (0.10, 0.470, 0.92, 0.510)     # mid content   -> main
_SECRET_BOX = (0.10, 0.760, 0.60, 0.790)    # lower content -> compose


def _esc(text: str) -> str:
    """Escape a string for an ffmpeg ``drawtext=text='...'`` argument."""
    return (text.replace("\\", "").replace("'", "").replace(":", " -")
                .replace("%", " pct").replace(",", " "))


def _drawtext(text: str, box: tuple[float, float, float, float], size: int,
              colour: str, enable: str = "") -> str:
    x = int(box[0] * SYNTH_W)
    y = int(box[1] * SYNTH_H)
    part = f"drawtext=text='{_esc(text)}':x={x}:y={y}:fontsize={size}:fontcolor={colour}"
    return part + (f":enable='{enable}'" if enable else "")


def _fg_for(bg: str) -> str:
    return "white" if bg in ("black", "0x1e1e1e", "0x3f0e40") else "black"


def synth_filters(screen: Screen, span: float, *, with_secret: bool) -> str:
    """The ``-vf`` chain for one screen: static title + subject, then the typed line grown
    one character per window so the clip carries REAL between-frame change (the delta gate
    classifies it as text/layout rather than idle — a synth chunk that never changes would
    silently exercise only the idle pack)."""
    fg = _fg_for(screen.bg)
    layers = [
        _drawtext(screen.title, _TITLE_BOX, 20, fg),
        _drawtext(screen.subject, _SUBJECT_BOX, 26, fg),
    ]
    steps = min(32, max(4, len(screen.typed)))
    for i in range(1, steps + 1):
        a = round((i - 1) * span / steps, 3)
        b = round(i * span / steps, 3)
        prefix = screen.typed[: max(1, round(len(screen.typed) * i / steps))]
        layers.append(_drawtext(prefix, _TYPED_BOX, 24, fg,
                                enable=f"between(t\\,{a}\\,{b})"))
    if with_secret and screen.secret:
        layers.append(_drawtext(screen.secret, _SECRET_BOX, 22, fg))
    return ",".join(layers)


def truth_for(screen: Screen, *, with_secret: bool) -> dict[str, Any]:
    """The ground truth for a synth chunk — derived from the SAME strings the filter chain
    draws, so the label can never drift from the pixels."""
    regions = [
        {"t_offset_s": 0.0, "text": screen.title, "bbox": list(_TITLE_BOX),
         "conf": 0.97, "role": ""},
        {"t_offset_s": 0.0, "text": screen.subject, "bbox": list(_SUBJECT_BOX),
         "conf": 0.95, "role": ""},
        {"t_offset_s": 0.0, "text": screen.typed, "bbox": list(_TYPED_BOX),
         "conf": 0.93, "role": ""},
    ]
    if with_secret and screen.secret:
        regions.append({"t_offset_s": 0.0, "text": screen.secret, "bbox": list(_SECRET_BOX),
                        "conf": 0.94, "role": ""})
    return {
        "app": screen.app,
        "activity": screen.activity,
        "entities": list(screen.entities),
        "ocr_regions": regions,
    }


# ======================================================================== ffmpeg helpers

def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise SystemExit(
            "ffmpeg is not on PATH — required for `synth`/`slice`. `headless` needs none."
        )
    return exe


def _encode_synth(screen: Screen, span: float, out: Path, *, with_secret: bool) -> bytes:
    src = f"color=c={screen.bg}:s={SYNTH_W}x{SYNTH_H}:r={SYNTH_FPS}:d={span}"
    cmd = [_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", src,
           "-vf", synth_filters(screen, span, with_secret=with_secret),
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(span), str(out)]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(
            "ffmpeg failed to render a synthetic screen (drawtext needs libfreetype + a "
            f"fontconfig default):\n{proc.stderr.decode('utf-8', 'replace')[:800]}"
        )
    return out.read_bytes()


def _slice_video(src: Path, span: float, work: Path) -> list[Path]:
    """Cut ``src`` into ``span``-second segments by STREAM COPY (no re-encode): the eval
    corpus must carry the capture's own encoder artefacts — CRF-28 ringing is exactly what
    O-2 says the OCR pass is out-of-distribution for, so re-encoding would score a corpus
    the pipeline will never see."""
    work.mkdir(parents=True, exist_ok=True)
    pattern = str(work / "seg-%05d.mp4")
    cmd = [_ffmpeg(), "-y", "-v", "error", "-i", str(src), "-c", "copy",
           "-f", "segment", "-segment_time", str(span), "-reset_timestamps", "1", pattern]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(f"ffmpeg segment failed:\n{proc.stderr.decode('utf-8', 'replace')[:800]}")
    return sorted(work.glob("seg-*.mp4"))


# ======================================================================== the writer

@dataclass
class ChunksetWriter:
    out: Path
    name: str
    user_id: str = "eval-user"
    device_id: str = "mac-eval-1"
    stream_id: str = "stream-EVAL"
    span_seconds: float = 10.0
    start: datetime = field(default_factory=lambda: datetime(2026, 7, 25, 9, 0, tzinfo=timezone.utc))
    chunks: list[dict[str, Any]] = field(default_factory=list)

    def add(self, *, blob: bytes | None, truth: dict[str, Any] | None,
            span: float | None = None, source: str = "") -> dict[str, Any]:
        i = len(self.chunks)
        span = self.span_seconds if span is None else span
        chunk_id = f"chunk-{self.name}-{i:04d}"
        c1 = make_c1(
            chunk_id=chunk_id, sequence=i,
            t_start=self.start + timedelta(seconds=i * self.span_seconds),
            span_seconds=span, blob=blob,
            user_id=self.user_id, device_id=self.device_id, stream_id=self.stream_id,
        )
        (self.out / "c1").mkdir(parents=True, exist_ok=True)
        (self.out / "c1" / f"{chunk_id}.json").write_text(
            json.dumps(c1, indent=2) + "\n", "utf-8")
        blob_rel = None
        if blob is not None:
            (self.out / "blobs").mkdir(parents=True, exist_ok=True)
            (self.out / "blobs" / f"{chunk_id}.mp4").write_bytes(blob)
            blob_rel = f"blobs/{chunk_id}.mp4"
        entry = {
            "chunk_id": chunk_id,
            "c1": f"c1/{chunk_id}.json",
            "blob": blob_rel,
            "span_seconds": span,
            "truth": truth or {},
        }
        if source:
            entry["source"] = source
        self.chunks.append(entry)
        return entry

    def write_manifest(self, mode: str, note: str = "") -> Path:
        manifest = {
            "chunkset_version": CHUNKSET_VERSION,
            "name": self.name,
            "mode": mode,
            "note": note,
            "user_id": self.user_id,
            "span_seconds": self.span_seconds,
            "count": len(self.chunks),
            "has_blobs": any(c["blob"] for c in self.chunks),
            "labelled": sum(1 for c in self.chunks if c["truth"].get("entities")),
            "chunks": self.chunks,
        }
        path = self.out / "manifest.json"
        path.write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
        return path


# ======================================================================== modes

def cmd_synth(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    w = ChunksetWriter(out=out, name=args.name, span_seconds=args.span,
                       user_id=args.user_id)
    tmp = out / ".render"
    tmp.mkdir(exist_ok=True)
    for i in range(args.count):
        screen = SCREENS[i % len(SCREENS)]
        blob = _encode_synth(screen, args.span, tmp / "frame.mp4",
                             with_secret=args.with_secrets)
        w.add(blob=blob, truth=truth_for(screen, with_secret=args.with_secrets),
              source=f"synth:{screen.key}")
        print(f"  [{i + 1}/{args.count}] {screen.key:6s} {len(blob):>8,d} B  {screen.app}")
    shutil.rmtree(tmp, ignore_errors=True)
    path = w.write_manifest("synth", note="ffmpeg lavfi + drawtext; labels ARE the drawn strings")
    print(f"wrote {path} ({len(w.chunks)} chunks, decodable, exactly labelled)")
    return 0


def cmd_headless(args) -> int:
    """C1 + truth only — no blobs, JSON-only, committable. The harness feeds an
    undecodable placeholder, so `clipprep` takes its synthetic-frames fallback and the
    mechanical scorers run with no ffmpeg, no GPU and no network."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    w = ChunksetWriter(out=out, name=args.name, span_seconds=args.span, user_id=args.user_id)
    for i in range(args.count):
        screen = SCREENS[i % len(SCREENS)]
        w.add(blob=None, truth=truth_for(screen, with_secret=args.with_secrets),
              source=f"headless:{screen.key}")
    path = w.write_manifest(
        "headless",
        note="no blobs (house rule 5: commit no binaries); clipprep takes its synthetic "
             "frames fallback under VIDEO_BACKEND=mock",
    )
    print(f"wrote {path} ({len(w.chunks)} chunks, JSON-only)")
    return 0


def cmd_slice(args) -> int:
    src = Path(args.from_video)
    if not src.is_file():
        raise SystemExit(f"--from-video {src} is not a file")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    work = out / ".segments"
    segments = _slice_video(src, args.span, work)
    if not segments:
        raise SystemExit(f"ffmpeg produced no segments from {src}")
    w = ChunksetWriter(out=out, name=args.name, span_seconds=args.span, user_id=args.user_id)
    for seg in segments[: args.count] if args.count else segments:
        w.add(blob=seg.read_bytes(), truth={}, source=f"slice:{src.name}:{seg.name}")
    shutil.rmtree(work, ignore_errors=True)
    path = w.write_manifest(
        "slice",
        note=f"stream-copied from {src.name}; `truth` is EMPTY — label it before running "
             f"the O-8 gate (app / activity / entities / ocr_regions per chunk)",
    )
    print(f"wrote {path} ({len(w.chunks)} chunks from {src.name})")
    print("NOTE: truth is empty. app_correct / named_entity_recall / propagation_rate "
          "score 0 denominators until you label it.")
    return 0


def cmd_wrap(args) -> int:
    src = Path(args.from_dir)
    if not src.is_dir():
        raise SystemExit(f"--from-dir {src} is not a directory")
    files = sorted(p for p in src.iterdir() if p.suffix.lower() in _VIDEO_EXTS)
    if not files:
        raise SystemExit(f"no video files ({sorted(_VIDEO_EXTS)}) in {src}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    w = ChunksetWriter(out=out, name=args.name, span_seconds=args.span, user_id=args.user_id)
    for f in files[: args.count] if args.count else files:
        w.add(blob=f.read_bytes(), truth={}, source=f"wrap:{f.name}")
    path = w.write_manifest("wrap", note=f"one chunk per file from {src}")
    print(f"wrote {path} ({len(w.chunks)} chunks from {src})")
    return 0


# ======================================================================== O-2 bridge

# WS-C's bake-off runner hard-codes its focus-box coordinate space
# (``sidecars/ocr/bakeoff/run_bakeoff.py: SRC_2X_W = 3456``) and scales boxes by
# ``width / SRC_2X_W``, so an exported truth row must express its boxes in a 3456-wide
# canvas or the CER half of the O-2 gate is scored against the wrong region.
BAKEOFF_SRC_2X_W = 3456


def jpeg_size(data: bytes) -> tuple[int, int]:
    """(width, height) from a JPEG's SOF marker. A 20-line parser instead of a Pillow
    dependency: this script must run in the DP venv, which has no imaging stack."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return w, h
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    raise ValueError("not a JPEG (no SOF marker)")


def cmd_ocr_truth(args) -> int:
    """Export a labelled chunkset into WS-C's O-2 bake-off ground-truth format.

    O-2's gate is defined over ~200 hand-labelled REAL macOS frames, and WS-C shipped a
    harness that "accepts real frames with zero code change". This is the other half of that
    sentence: **one labelling pass, two gates.** The same ``truth.ocr_regions`` that lets
    ``prompt_ab.py`` run the O-8 injection gate is written out here as ``ground_truth.json``
    + the extracted high-resolution frames, so the sidecar bake-off can be re-run over the
    real corpus without labelling it twice (and without the two gates drifting onto two
    different notions of what the screen said).

    The frames exported are the OCR renditions (``jpeg_hi`` at ``VIDEO_OCR_FRAME_WIDTH``)
    that the ``screentext`` stage would actually have read — not a fresh, differently-scaled
    extraction.

    NOTE for the operator: ``run_bakeoff.py`` re-encodes each input at CRF 28. Frames pulled
    from a real capture are ALREADY CRF-28 degraded, so a straight re-run measures a double
    encode — a conservative lower bound. Use the raw arm (``ablate_crf.py``) for the honest
    single-encode number.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.vision import clip as clip_mod  # noqa: E402

    manifest = load_chunkset(args.chunkset)
    out = Path(args.out)
    (out / "frames_src").mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    skipped = 0
    cvs = clip_mod.build_vision_settings()
    for entry in manifest["chunks"]:
        truth = entry.get("truth") or {}
        regions = truth.get("ocr_regions") or []
        if not entry.get("blob_path") or not regions:
            skipped += 1
            continue
        blob = Path(entry["blob_path"]).read_bytes()
        try:
            clip_frames, _ = clip_mod.prepare_clip(
                blob, float(entry.get("span_seconds") or 10.0), 0.0, cvs)
        except Exception as exc:
            print(f"  ! {entry['chunk_id']}: frame prep failed ({exc})", file=sys.stderr)
            skipped += 1
            continue
        hi = [f for f in clip_frames.frames if f.jpeg_hi]
        if not hi:
            skipped += 1
            continue
        frame = hi[0]
        name = f"{entry['chunk_id']}-{frame.index:02d}.jpg"
        (out / "frames_src" / name).write_bytes(frame.jpeg_hi)
        w, h = jpeg_size(frame.jpeg_hi)
        canvas_h = BAKEOFF_SRC_2X_W * h / max(1, w)
        xs = [c for r in regions for c in (r["bbox"][0], r["bbox"][2])]
        ys = [c for r in regions for c in (r["bbox"][1], r["bbox"][3])]
        rows.append({
            "id": entry["chunk_id"],
            "archetype": (entry.get("source", "") or "chunk").split(":")[-1],
            "png": f"frames_src/{name}",
            "key_strings": sorted({t for t in
                                   [r["text"] for r in regions] + (truth.get("entities") or [])
                                   if len(t.strip()) >= 5}),
            "focus_bbox_2x": [round(min(xs) * BAKEOFF_SRC_2X_W, 1), round(min(ys) * canvas_h, 1),
                              round(max(xs) * BAKEOFF_SRC_2X_W, 1), round(max(ys) * canvas_h, 1)],
            "focus_text": "\n".join(r["text"] for r in regions),
        })
    (out / "ground_truth.json").write_text(json.dumps(rows, indent=2) + "\n", "utf-8")
    print(f"wrote {out / 'ground_truth.json'} ({len(rows)} frames, {skipped} chunk(s) skipped)")
    print("run it with:  cd sidecars/ocr/bakeoff && "
          f"cp -r {out}/* . && ./.venv/bin/python run_bakeoff.py")
    return 0


# ======================================================================== reader (shared)

def load_chunkset(path: str | Path) -> dict[str, Any]:
    """Load a chunkset manifest and resolve every chunk's C1 + blob path. Shared with
    ``prompt_ab.py`` / ``oracle_gemini.py`` — one reader, one on-disk contract."""
    root = Path(path)
    if root.is_file():
        root = root.parent
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"{root} is not a chunkset (no manifest.json)")
    manifest = json.loads(manifest_path.read_text("utf-8"))
    for entry in manifest["chunks"]:
        entry["c1_obj"] = json.loads((root / entry["c1"]).read_text("utf-8"))
        entry["blob_path"] = str(root / entry["blob"]) if entry.get("blob") else None
    manifest["root"] = str(root)
    return manifest


# ======================================================================== cli

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="capture_chunkset.py",
        description="Build a chunkset (the corpus prompt_ab.py scores an arm over).",
    )
    sub = p.add_subparsers(dest="mode", required=True)

    def common(sp, *, default_name: str, default_count: int):
        sp.add_argument("--out", required=True, help="chunkset directory to write")
        sp.add_argument("--name", default=default_name, help="chunkset name (used in chunk_ids)")
        sp.add_argument("--count", type=int, default=default_count,
                        help="number of chunks (0 = all, for slice/wrap)")
        sp.add_argument("--span", type=float, default=10.0, help="chunk span in seconds")
        sp.add_argument("--user-id", default="eval-user")
        sp.add_argument("--with-secrets", action="store_true",
                        help="draw a synthetic secret so the redaction path is exercised")

    s = sub.add_parser("synth", help="generate labelled synthetic screens with ffmpeg")
    common(s, default_name="synth", default_count=24)
    s.set_defaults(func=cmd_synth)

    h = sub.add_parser("headless", help="C1 + truth only, no blobs (committable)")
    common(h, default_name="smoke", default_count=12)
    h.set_defaults(func=cmd_headless)

    sl = sub.add_parser("slice", help="cut one real recording into chunks")
    common(sl, default_name="real", default_count=0)
    sl.add_argument("--from-video", required=True)
    sl.set_defaults(func=cmd_slice)

    wr = sub.add_parser("wrap", help="wrap a directory of pre-cut clips")
    common(wr, default_name="real", default_count=0)
    wr.add_argument("--from-dir", required=True)
    wr.set_defaults(func=cmd_wrap)

    ot = sub.add_parser("ocr-truth",
                        help="export a labelled chunkset as WS-C's O-2 bake-off ground truth")
    ot.add_argument("--chunkset", required=True)
    ot.add_argument("--out", required=True)
    ot.set_defaults(func=cmd_ocr_truth)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
