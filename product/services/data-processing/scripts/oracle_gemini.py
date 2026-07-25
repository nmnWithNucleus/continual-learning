#!/usr/bin/env python3
"""The Gemini oracle + the blind judge — O-4's evidence, and O-8's qualitative half (WS-H).

Two things the mechanical scorers in ``prompt_ab.py`` structurally cannot do:

  * **judge** — a BLIND pairwise comparison of two arms' captions on the POC's
    frame-grounded rubric (cross-frame reasoning present · invented detail · usable as a
    day-log line). Blind means the judge never learns which arm is which: the two captions
    are presented as ``A``/``B`` in an order derived from a hash of the chunk id, and the
    mapping is kept only on this side. That removes the position bias that makes an
    un-blinded LLM judge worthless, and it is deterministic (no RNG), so a re-run reproduces
    the same presentation.
  * **oracle** — an upper bound. A frontier model is given the SAME frames the captioner
    saw and asked for the ideal day-log line; every arm's named-entity recall is then scored
    against the oracle's named strings instead of against a hand label. This is what turns
    "arm A beat arm B" into "arm A recovered 61 % of what was recoverable".

**This costs real money and reaches a real network.** Both are gated: nothing runs without
``--yes``, the projected spend is printed first, and a missing credential is a clean,
honest exit — *not* a silent fallback to a cheaper model or to nothing.

    export VERTEX_API_KEY=...                      # or VERTEX_ACCESS_TOKEN + VERTEX_PROJECT
    python scripts/oracle_gemini.py judge  --rows-dir rows/ --arms injected,blind --chunkset cs/ --yes
    python scripts/oracle_gemini.py oracle --rows-dir rows/ --chunkset cs/ --yes

Inputs come from ``prompt_ab.py --rows-dir DIR`` (one ``<arm>.jsonl`` per arm) plus the
chunkset the arms ran over (for the frames). Output is a JSON report + a printed table.

IF THERE IS NO KEY: this exits 4 and says so. The mechanical scorers in ``prompt_ab.py``
stand alone — they are the gate; this is the corroboration.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from prompt_ab import named_strings, norm  # noqa: E402  (one scorer definition, not two)

DEFAULT_MODEL = "gemini-2.5-pro"
DEFAULT_LOCATION = "us-central1"

# Published Vertex list price for the 2.5-pro class, per 1M tokens (USD). Used ONLY to
# print a projection before spending; the report records the model actually called so the
# figure can be re-derived if the rate moves.
USD_PER_M_INPUT = 1.25
USD_PER_M_OUTPUT = 10.00
IMAGE_TOKENS_EACH = 258          # Gemini bills a <=768px tile at a flat 258 tokens


# ======================================================================== rubric

JUDGE_SYSTEM = """\
You are grading two candidate day-log lines written from the SAME short screen recording.
You are shown the frames the writers saw, then caption A and caption B. You do not know
which system wrote which, and there is no correct answer keyed to either letter.

Grade on exactly three axes, each independently for A and for B:

1. cross_frame — does the line describe what CHANGED across the frames (an action over
   time), rather than describing a single still picture? 0 = a still description,
   1 = one weak temporal cue, 2 = a clear action across frames.
2. invented — does the line state a name, number, quoted string, price or address that is
   NOT visible in the frames? 0 = nothing invented, 1 = one questionable detail,
   2 = a confidently stated detail that is not there. LOWER IS BETTER.
3. usable — read alone, six months later, would this line let the person recall this
   moment of their day? 0 = useless, 1 = partly, 2 = yes.

Then pick the better line overall. Ties are allowed and are not a failure.

Reply with ONE JSON object and nothing else:
{"a": {"cross_frame": 0-2, "invented": 0-2, "usable": 0-2},
 "b": {"cross_frame": 0-2, "invented": 0-2, "usable": 0-2},
 "winner": "a" | "b" | "tie",
 "why": "<at most 25 words>"}
"""

ORACLE_SYSTEM = """\
You are shown still frames sampled in time order from ONE continuous clip of a person's
computer display. Write the single best day-log line for this clip: what application or
site is in focus, what the person is doing, and the specific thing they are working on.

Name only what you can actually read in the frames. Never invent a name, number or quoted
string. One paragraph, no line breaks, at most 60 words.

Reply with ONE JSON object and nothing else:
{"app": "<application or site in focus, or 'unknown'>",
 "line": "<the day-log line>"}
"""


# ======================================================================== credentials

@dataclass(frozen=True)
class Creds:
    mode: str          # "apikey" | "vertex"
    key: str
    project: str = ""
    location: str = DEFAULT_LOCATION

    def url(self, model: str) -> str:
        if self.mode == "apikey":
            return (f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model}:generateContent")
        return (f"https://{self.location}-aiplatform.googleapis.com/v1/projects/"
                f"{self.project}/locations/{self.location}/publishers/google/models/"
                f"{model}:generateContent")

    def headers(self) -> dict[str, str]:
        if self.mode == "apikey":
            return {"x-goog-api-key": self.key, "content-type": "application/json"}
        return {"Authorization": f"Bearer {self.key}", "content-type": "application/json"}


def resolve_creds() -> Creds | None:
    """``VERTEX_API_KEY`` (Generative Language API) or ``VERTEX_ACCESS_TOKEN`` +
    ``VERTEX_PROJECT`` (Vertex AI). No new dependency: both are plain REST over httpx,
    which is already a base dep — an eval harness must not drag a cloud SDK into the
    service's requirements."""
    api_key = (os.getenv("VERTEX_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
    if api_key:
        return Creds("apikey", api_key)
    token = (os.getenv("VERTEX_ACCESS_TOKEN") or "").strip()
    project = (os.getenv("VERTEX_PROJECT") or "").strip()
    if token and project:
        return Creds("vertex", token, project,
                     (os.getenv("VERTEX_LOCATION") or DEFAULT_LOCATION).strip())
    return None


# ======================================================================== frames

def frames_for(chunkset_root: Path, entry: dict, *, max_frames: int) -> list[bytes]:
    """The SAME frames the captioner saw — ``app.vision.clip.prepare_clip``, not a fresh
    ffmpeg invocation with different settings. A judge shown a different sample of the clip
    is grading a different clip, and an oracle built that way is an upper bound on the wrong
    thing. Returns ``[]`` when the chunk has no blob (a headless chunkset), which the caller
    reports rather than silently judging text-only."""
    if not entry.get("blob"):
        return []
    from app.vision import clip as clip_mod

    blob = (chunkset_root / entry["blob"]).read_bytes()
    cvs = clip_mod.build_vision_settings()
    try:
        clip_frames, _delta = clip_mod.prepare_clip(
            blob, float(entry.get("span_seconds") or 10.0), 0.0, cvs)
    except Exception as exc:
        print(f"  ! {entry['chunk_id']}: frame prep failed ({exc}) — judging text-only",
              file=sys.stderr)
        return []
    jpegs = [f.jpeg_lo for f in clip_frames.frames if f.jpeg_lo]
    return jpegs[:max_frames]


def _part_image(jpeg: bytes) -> dict[str, Any]:
    return {"inline_data": {"mime_type": "image/jpeg",
                            "data": base64.b64encode(jpeg).decode("ascii")}}


# ======================================================================== the wire

def call_gemini(creds: Creds, model: str, system: str, parts: list[dict],
                *, timeout: float = 120.0) -> tuple[dict[str, Any], dict[str, int]]:
    """One ``generateContent`` call → ``(parsed_json, usage)``. Tolerant parse: a frontier
    model still fences its JSON often enough that a strict loads() would drop good rows."""
    import httpx

    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    resp = httpx.post(creds.url(model), json=payload, headers=creds.headers(), timeout=timeout)
    resp.raise_for_status()
    body = resp.json()
    usage_raw = body.get("usageMetadata") or {}
    usage = {"input": int(usage_raw.get("promptTokenCount", 0)),
             "output": int(usage_raw.get("candidatesTokenCount", 0))}
    candidates = body.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"no candidates in response: {str(body)[:300]}")
    text = "".join(p.get("text", "")
                   for p in (candidates[0].get("content") or {}).get("parts") or [])
    return _loads_tolerant(text), usage


def _loads_tolerant(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


# ======================================================================== blinding

def presentation_order(chunk_id: str, arm_a: str, arm_b: str) -> tuple[str, str]:
    """Which arm is shown as "A". Derived from a hash of ``(chunk_id, arms)``, never from a
    RNG and never from arm order — so the blinding is reproducible across runs and machines
    while still being uncorrelated with the arms themselves."""
    h = hashlib.sha256(f"{chunk_id}\x00{arm_a}\x00{arm_b}".encode()).digest()[0]
    return (arm_a, arm_b) if h % 2 == 0 else (arm_b, arm_a)


# ======================================================================== loading

def load_rows(rows_dir: Path, arm: str) -> dict[str, dict]:
    path = rows_dir / f"{arm}.jsonl"
    if not path.is_file():
        raise SystemExit(f"no rows for arm {arm!r} at {path} — run prompt_ab.py --rows-dir")
    out: dict[str, dict] = {}
    for line in path.read_text("utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["chunk_id"]] = row
    return out


def load_manifest(chunkset: str) -> tuple[Path, dict[str, dict]]:
    from capture_chunkset import load_chunkset

    manifest = load_chunkset(chunkset)
    return Path(manifest["root"]), {c["chunk_id"]: c for c in manifest["chunks"]}


# ======================================================================== cost

def project_cost(n_calls: int, n_images: int, model: str) -> float:
    """A pre-flight projection, printed BEFORE anything is spent. ~700 text tokens per call
    plus a flat image charge; output is short and JSON-shaped."""
    inp = n_calls * 700 + n_images * IMAGE_TOKENS_EACH
    out = n_calls * 200
    return inp / 1e6 * USD_PER_M_INPUT + out / 1e6 * USD_PER_M_OUTPUT


def _confirm(n_calls: int, n_images: int, model: str, yes: bool) -> None:
    usd = project_cost(n_calls, n_images, model)
    print(f"  model={model}  calls={n_calls}  images={n_images}  "
          f"projected ${usd:.2f} (list price; the report records the model called)")
    if not yes:
        raise SystemExit("refusing to spend without --yes")


# ======================================================================== commands

def cmd_judge(args) -> int:
    creds = _require_creds()
    rows_dir = Path(args.rows_dir)
    arm_a, arm_b = [a.strip() for a in args.arms.split(",")][:2]
    rows_a, rows_b = load_rows(rows_dir, arm_a), load_rows(rows_dir, arm_b)
    root, entries = load_manifest(args.chunkset)
    chunk_ids = [c for c in rows_a if c in rows_b][: args.limit or None]
    if not chunk_ids:
        raise SystemExit(f"no chunks present in both {arm_a!r} and {arm_b!r}")

    frames = {c: frames_for(root, entries[c], max_frames=args.max_frames)
              for c in chunk_ids if c in entries}
    _confirm(len(chunk_ids), sum(len(f) for f in frames.values()), args.model, args.yes)

    tally = {arm_a: 0, arm_b: 0, "tie": 0}
    axes = {arm_a: {"cross_frame": [], "invented": [], "usable": []},
            arm_b: {"cross_frame": [], "invented": [], "usable": []}}
    results, usage_total = [], {"input": 0, "output": 0}
    for chunk_id in chunk_ids:
        first, second = presentation_order(chunk_id, arm_a, arm_b)
        caption = {arm_a: rows_a[chunk_id].get("caption", ""),
                   arm_b: rows_b[chunk_id].get("caption", "")}
        parts: list[dict[str, Any]] = []
        for jpeg in frames.get(chunk_id, []):
            parts.append(_part_image(jpeg))
        if not frames.get(chunk_id):
            parts.append({"text": "(no frames available for this clip — grade the text only, "
                                  "and score `invented` 0 for both)"})
        parts.append({"text": f"Caption A:\n{caption[first]}\n\nCaption B:\n{caption[second]}"})
        try:
            verdict, usage = call_gemini(creds, args.model, JUDGE_SYSTEM, parts)
        except Exception as exc:
            print(f"  ! {chunk_id}: judge call failed ({type(exc).__name__}: {exc})",
                  file=sys.stderr)
            continue
        usage_total["input"] += usage["input"]
        usage_total["output"] += usage["output"]
        # UNBLIND here, and only here.
        letter_to_arm = {"a": first, "b": second}
        winner = letter_to_arm.get(str(verdict.get("winner", "")).lower(), "tie")
        tally[winner if winner in tally else "tie"] += 1
        for letter, arm in letter_to_arm.items():
            scores = verdict.get(letter) or {}
            for axis in axes[arm]:
                if isinstance(scores.get(axis), (int, float)):
                    axes[arm][axis].append(float(scores[axis]))
        results.append({"chunk_id": chunk_id, "shown_as_a": first, "shown_as_b": second,
                        "winner": winner, "why": verdict.get("why", ""), "raw": verdict})

    report = {
        "mode": "judge", "model": args.model, "arms": [arm_a, arm_b],
        "chunks": len(results), "tally": tally,
        "axes": {arm: {k: (round(sum(v) / len(v), 3) if v else None) for k, v in d.items()}
                 for arm, d in axes.items()},
        "usage": usage_total,
        "usd": (usage_total["input"] / 1e6 * USD_PER_M_INPUT
                + usage_total["output"] / 1e6 * USD_PER_M_OUTPUT),
        "results": results,
        "blinding": "presentation order = sha256(chunk_id, arms)[0] % 2 — deterministic, "
                    "uncorrelated with the arms, unblinded only after the verdict is read",
    }
    print()
    print(f"blind judge · {arm_a} vs {arm_b} · {len(results)} chunks · {args.model}")
    print(f"  wins: {arm_a}={tally[arm_a]}  {arm_b}={tally[arm_b]}  tie={tally['tie']}")
    for arm in (arm_a, arm_b):
        a = report["axes"][arm]
        print(f"  {arm:<18} cross_frame={a['cross_frame']}  invented={a['invented']} (lower "
              f"is better)  usable={a['usable']}")
    print(f"  spent ~${report['usd']:.2f} ({usage_total['input']} in / "
          f"{usage_total['output']} out tokens)")
    _write(args.json, report)
    return 0


def cmd_oracle(args) -> int:
    creds = _require_creds()
    rows_dir = Path(args.rows_dir)
    arms = [a.strip() for a in args.arms.split(",")] if args.arms else \
        sorted(p.stem for p in rows_dir.glob("*.jsonl"))
    per_arm = {a: load_rows(rows_dir, a) for a in arms}
    root, entries = load_manifest(args.chunkset)
    any_arm = per_arm[arms[0]]
    chunk_ids = [c for c in any_arm if c in entries][: args.limit or None]

    frames = {c: frames_for(root, entries[c], max_frames=args.max_frames) for c in chunk_ids}
    usable = [c for c in chunk_ids if frames.get(c)]
    if not usable:
        raise SystemExit("no chunk in this chunkset has decodable frames — an oracle needs "
                         "pixels; use a `synth`/`slice`/`wrap` chunkset, not `headless`")
    _confirm(len(usable), sum(len(frames[c]) for c in usable), args.model, args.yes)

    oracle: dict[str, dict] = {}
    usage_total = {"input": 0, "output": 0}
    for chunk_id in usable:
        parts: list[dict[str, Any]] = [_part_image(j) for j in frames[chunk_id]]
        parts.append({"text": f"{len(frames[chunk_id])} frames in time order. Write the line."})
        try:
            got, usage = call_gemini(creds, args.model, ORACLE_SYSTEM, parts)
        except Exception as exc:
            print(f"  ! {chunk_id}: oracle call failed ({type(exc).__name__}: {exc})",
                  file=sys.stderr)
            continue
        usage_total["input"] += usage["input"]
        usage_total["output"] += usage["output"]
        oracle[chunk_id] = {"app": got.get("app", ""), "line": got.get("line", ""),
                            "entities": named_strings(got.get("line", ""))}

    scored: dict[str, dict] = {}
    for arm, rows in per_arm.items():
        hits = tot = 0
        app_hits = app_n = 0
        for chunk_id, ref in oracle.items():
            caption = norm(rows.get(chunk_id, {}).get("caption", ""))
            for e in ref["entities"]:
                tot += 1
                hits += int(norm(e) in caption)
            if ref["app"] and ref["app"].lower() != "unknown":
                app_n += 1
                app_hits += int(norm(ref["app"]) in caption)
        scored[arm] = {
            "oracle_entity_recall": round(hits / tot, 4) if tot else None,
            "oracle_app_agreement": round(app_hits / app_n, 4) if app_n else None,
            "oracle_entities": tot,
        }

    report = {"mode": "oracle", "model": args.model, "chunks": len(oracle),
              "arms": scored, "oracle": oracle, "usage": usage_total,
              "usd": (usage_total["input"] / 1e6 * USD_PER_M_INPUT
                      + usage_total["output"] / 1e6 * USD_PER_M_OUTPUT)}
    print()
    print(f"gemini oracle · {len(oracle)} chunks · {args.model}")
    for arm, s in scored.items():
        print(f"  {arm:<18} entity recall vs oracle={s['oracle_entity_recall']}  "
              f"app agreement={s['oracle_app_agreement']}  (n={s['oracle_entities']})")
    print(f"  spent ~${report['usd']:.2f}")
    _write(args.json, report)
    return 0


def _require_creds() -> Creds:
    creds = resolve_creds()
    if creds is None:
        print(
            "no Vertex/Gemini credential found — the oracle and the blind judge cannot run.\n"
            "  Set VERTEX_API_KEY (Generative Language API) or VERTEX_ACCESS_TOKEN +\n"
            "  VERTEX_PROJECT (Vertex AI).\n"
            "  This is not a failure of the eval: the mechanical scorers in prompt_ab.py\n"
            "  stand alone and are the gate. The oracle is corroboration, not the ruling.",
            file=sys.stderr,
        )
        raise SystemExit(4)
    return creds


def _write(path: str | None, report: dict) -> None:
    if path:
        Path(path).write_text(json.dumps(report, indent=2) + "\n", "utf-8")
        print(f"  wrote {path}")


# ======================================================================== cli

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="oracle_gemini.py",
        description="Blind LLM judge + frontier-model oracle over prompt_ab arm outputs.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--rows-dir", required=True,
                        help="directory of <arm>.jsonl from `prompt_ab.py --rows-dir`")
        sp.add_argument("--chunkset", required=True, help="the chunkset the arms ran over")
        sp.add_argument("--model", default=DEFAULT_MODEL)
        sp.add_argument("--limit", type=int, default=0)
        sp.add_argument("--max-frames", type=int, default=6,
                        help="frames sent per chunk (the judge/oracle image bill)")
        sp.add_argument("--json", help="write the machine-readable report here")
        sp.add_argument("--yes", action="store_true", help="confirm the projected spend")

    j = sub.add_parser("judge", help="blind pairwise A/B judging of two arms")
    common(j)
    j.add_argument("--arms", default="injected,blind")
    j.set_defaults(func=cmd_judge)

    o = sub.add_parser("oracle", help="frontier-model upper bound; score every arm against it")
    common(o)
    o.add_argument("--arms", default="", help="default: every arm in --rows-dir")
    o.set_defaults(func=cmd_oracle)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
