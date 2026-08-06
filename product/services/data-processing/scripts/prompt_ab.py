#!/usr/bin/env python3
"""PARKED — BROKEN AGAINST THE DP-REBUILD STAGE GRAPH (Stage C, WP-C5). DO NOT RUN.

This harness is built on machinery the rebuild deleted (deliberately, per L4 — no
output-affecting env knobs): per-arm ``VIDEO_PROMPT_DIR`` temp registries +
``VIDEO_CLIP_PROMPT``/``VIDEO_*`` env forks, ``prompt_dir_fingerprint``/``cfg_tag``
dialects, the v0 ``resolve(modality, stages, settings)`` / per-unit ``run_graph`` /
4-arg ``build_c2``/``compute_record_id`` signatures, and the ``keyframe`` legacy
pipeline. ``main()`` refuses to run below.

WHAT A REBUILD NEEDS (report to the eval owner; a minimal adaptation was judged NOT
minimal and a half-fix worse than a clear break):
  1. Arms become IN-CODE stage constructions: ``ClipcapStage(backend=Backend("vlm", n),
     experiment="<arm>")`` (the ``.exp-<code>`` dialect) — experimental packs load via
     ``prompts.load_registry(<arm dir>)`` handed to an arm-specific describe(), or the
     arm pack is temporarily added to the packaged registry on an experiment branch
     (the digest pin + vB bump make it visible by construction).
  2. Drive ``resolve("video", [clipprep, screentext_fake_or_real, clipcap_arm])`` +
     ``run_graph(resolved, c1=..., blob=..., span_seconds=..., clients=...)`` and score
     ``GraphResult.slots`` (one record; the old per-unit loop is gone).
  3. The OCR truth/corrupt30 arms become client-level fakes handed via ``clients``
     (no VIDEO_OCR_BACKEND).
  4. The storage-poison guard (``_forbid_storage``) and the scorers are still sound
     and can be lifted as-is; ``DP_OFFLINE_EVAL`` keeps its serving-guard role in
     app/main.py for whatever replaces this driver.

The original design notes are preserved below for that rebuild.
--------------------------------------------------------------------------------------

The offline prompt A/B + the quality gates (WS-H · §11 → WS-H, O-4, O-8).

Runs a chunkset through the REAL video stage graph — ``resolve()`` + ``run_graph()`` +
``pipeline.build_c2``, imported DIRECTLY — once per arm, and scores the arms side by side
with mechanical scorers only. Cheap enough to be a pre-push hook, which is the actual
requirement: an eval expensive enough to skip will be skipped.

WHY IT CANNOT WRITE TO /context
-------------------------------
Never FastAPI, never ``StorageClient``. ``ingest_core.py``'s per-unit loop is the only
writer in the system and it lives *above* the processor; this harness enters below it. That
is a structural property, and :func:`_forbid_storage` makes it an ENFORCED one — the arm
worker poisons ``StorageClient.__init__`` and ``ingest_core.process_chunk`` before importing
a single stage, so a future refactor that quietly reaches for storage fails loudly here
instead of silently minting experimental records in a real corpus. The mirror guard lives in
``app/main.py``: ``DP_OFFLINE_EVAL=1`` (which this harness requires) makes the serving app
refuse to boot.

WHY ARMS CANNOT COLLIDE
-----------------------
Each arm is assembled into its own COMPLETE prompt registry in a temp dir (the six packaged
packs + ``schemas.json`` + the arm's experimental pack + a rewritten ``routes.json`` whose
``family_defaults.clip`` is that arm's pack + an ``arm.json`` recording the arm's identity),
and runs in its own SUBPROCESS with ``VIDEO_PROMPT_DIR`` pointed at it — packs load once per
process at import (D-13's TOCTOU discipline), so one process cannot hold two arms. The fork
is then automatic and two-fold:

  * ``prompt_dir_fingerprint`` is OUTPUT_AFFECTING, so the arm dir's CONTENTS fold into
    ``cfg_tag`` — arms fork under EVERY backend, mock included (where ``prompt_tag`` is
    ``""`` by design and would not fork on its own). ``arm.json`` is what makes two arms
    with identical pack text (clean-OCR vs corrupted-OCR) fork too.
  * ``PACK_DIGEST`` + the pack id fork ``prompt_tag`` under ``vlm``/``vertex``.

The report prints the arms' ``pipeline_version`` strings and the resulting ``record_id``s
side by side, which is the proof that the fork is real rather than asserted.

SCORERS (all mechanical)
------------------------
records/chunk · chars/record · projected chars per day-log block through continuum's OWN
``build_daylog`` against ``EXCERPT_CHARS`` · parse-fallback + truncation rates ·
``app != "unknown"`` rate · change-verb rate · the WIDENED grounding scorer (all named
≥4-char strings, not only double-quoted spans) · measured prompt/completion tokens ·
and the **O-8 gate**: ``app_correct``, ``named_entity_recall(A) − named_entity_recall(B)``
and ``propagation_rate`` on a 30 %-corrupted-OCR arm, with the pre-registered decision rule.

USAGE
-----
    DP_OFFLINE_EVAL=1 python scripts/prompt_ab.py --chunkset tests/fixtures/chunksets/smoke-v1
    DP_OFFLINE_EVAL=1 python scripts/prompt_ab.py --chunkset <cs> --gate o8
    DP_OFFLINE_EVAL=1 python scripts/prompt_ab.py --chunkset <cs> --arms keyframe,injected   # O-4
    DP_OFFLINE_EVAL=1 VIDEO_BACKEND=vlm VIDEO_VLM_URL=http://127.0.0.1:8000 \
        python scripts/prompt_ab.py --chunkset <cs> --gate o8 --json report.json

Under the default ``VIDEO_BACKEND=mock`` every scorer runs and every plumbing property is
proven, but the mock caption is a canned string derived from ``(n_frames, span, chunk_id)``
— it never reads the injected OCR — so the QUALITY numbers (recall, grounding, app_correct)
measure the harness, not the model. The report says so, in those words, at the top.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

# The continuum constant the day-log budget is scored against (speed.py:79). Read live
# below when continuum is importable; this is the pinned fallback so the scorer still has
# a denominator in a DP-only checkout.
EXCERPT_CHARS_DEFAULT = 6000

# §7 serving assumptions — the ONLY inputs to the cost projection. Ratios are robust;
# absolutes are an input-times-assumption (O-6), and the report says so.
PREFILL_TOK_PER_S = 12_000.0
DECODE_TOK_PER_S = 2_000.0
NODE_DOLLARS_PER_HOUR = 16.0


# ======================================================================== arms

@dataclass(frozen=True)
class Arm:
    """One experimental arm. ``pack_file`` is an experimental pack added to the assembled
    registry; ``pack`` is the id that becomes ``family_defaults.clip`` for the arm."""
    name: str
    pack: str = "screen-clip-v1"
    pack_file: str | None = None          # relative to app/vision/prompts/
    pipeline: str = "clip"                # clip | keyframe (the O-4 per-frame arm)
    ocr: str = "truth"                    # truth | corrupt30 | backend | off
    corrupt_fraction: float = 0.0
    env: dict[str, str] = field(default_factory=dict)
    note: str = ""


_EXPERIMENTAL = "experimental"

ARMS: dict[str, Arm] = {
    # --- O-8: the ratified blind-vs-injected gate ------------------------------------
    "injected": Arm(
        "injected", pack="screen-clip-v1", ocr="truth",
        note="Architecture A (D-09, the shipped design): OCR text injected into the caption prompt",
    ),
    "blind": Arm(
        "blind", pack="screen-clip-blind-v1",
        pack_file=f"{_EXPERIMENTAL}/screen-clip-blind-v1.prompt.md", ocr="truth",
        note="Architecture B: captioner sees frames only; the ocr record is still emitted",
    ),
    "hint": Arm(
        "hint", pack="screen-clip-hint-v1",
        pack_file=f"{_EXPERIMENTAL}/screen-clip-hint-v1.prompt.md", ocr="truth",
        note="Architecture D (the ratified fallback): OCR usable for the app NAME only",
    ),
    "injected-corrupt": Arm(
        "injected-corrupt", pack="screen-clip-v1", ocr="corrupt30", corrupt_fraction=0.30,
        note="Architecture A with 30% of the ground-truth OCR strings falsified — the "
             "propagation_rate arm of O-8",
    ),
    # --- O-4: per-frame vs per-clip, same model --------------------------------------
    "keyframe": Arm(
        "keyframe", pipeline="keyframe", ocr="backend",
        note="the retained legacy keyframe graph (per-frame captions) — O-4's control arm",
    ),
    # --- runtime pack variants --------------------------------------------------------
    "idle": Arm("idle", pack="screen-clip-idle-v1", ocr="truth",
                note="the idle-screen pack as the family default"),
    "single": Arm("single", pack="screen-clip-single-v1", ocr="truth",
                  note="the K=1 fallback pack (no --limit-mm-per-prompt)"),
}

DEFAULT_ARMS = ("injected", "blind")
O8_ARMS = ("injected", "blind", "hint", "injected-corrupt")


# ======================================================================== scorers (pure)

_WS = re.compile(r"\s+")
_QUOTED = re.compile(r'"([^"\n]{1,120})"')
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/@'\-]*")

# Change verbs the design's §5.1 rule 2 asks the caption to use — the mechanical proxy for
# "did this caption reason ACROSS frames rather than describe one picture" (O-4's headline).
CHANGE_VERBS = (
    "typing", "typed", "types", "writing", "wrote", "editing", "edited",
    "scrolling", "scrolled", "reading", "read", "switching", "switched",
    "selecting", "selected", "dragging", "dragged", "filling", "filled",
    "opening", "opened", "closing", "closed", "navigating", "navigated",
    "running", "ran", "searching", "searched", "clicking", "clicked",
    "playing", "reviewing", "replying", "composing", "renaming", "moving",
)

# Capitalised words that are NOT names. A sentence-initial "The"/"Two" must never be
# scored as an ungrounded named string — that is the false-positive class that would make
# the widened grounding counter unreadable.
_STOP = frozenset("""
a an and are as at be been but by can cannot could did does doing done down during each
either else few for from further had has have having here how however if in inside into is
it its itself just least less like made make many may might more most much must near
neither never next no nor not now of off on once one only onto or other our out over own
same shall should since so some still such than that the their them then there these they
this those three through thus to together too two under until up upon use used using very
was way we were what when where whether which while who whom whose why will with within
without would yet you your above after again against all almost along already also although
always among another any anything appears around because before being below beside besides
both continues cannot
person people user users screen window windows display displays computer text content
sentence sentences paragraph line lines page pages file files document documents
unknown unclear none visible focused focus stable growing across between during moment
moments frame frames clip clips application applications site website
""".split()) | frozenset(CHANGE_VERBS)   # a change verb is an ACTION, never a name
# NOTE: app-shaped common nouns (Terminal, Safari, Numbers, Mail, Notes, Preview) are
# deliberately ABSENT — they are real macOS application names and the scorer's whole job is
# to notice when a caption states one the OCR pass never read.

# Namelike token shapes a plain English word never has.
_HAS_DIGIT = re.compile(r"(?=.*[A-Za-z])(?=.*\d)")
_INNER_CAP = re.compile(r"[a-z][A-Z]")
_PATHY = re.compile(r"[._/@]")


def norm(text: str) -> str:
    """Casefolded, whitespace-collapsed form used for every substring comparison. Lenient
    matching is deliberate: O-2 measured 0.000 *strict* recall on a model that was reading
    correctly, so an exact-equality grounding check would score noise."""
    return _WS.sub(" ", (text or "")).strip().casefold()


def _clean(token: str) -> str:
    return token.strip(" \t\"'`.,;:!?()[]{}")


def _is_namelike(token: str) -> bool:
    """Does this token look like a NAME rather than an English word? Digits+letters
    (``node-7``, ``Q3``), an internal capital (``StageContext``), a path/dot/@ shape
    (``executor.py``, ``arxiv.org``), or a capitalised non-stopword.

    The stopword list is the whole defence against sentence-initial capitalisation
    ("The", "Two", "A person") being scored as an invented name — which is the
    false-positive class that would make the widened counter unreadable. It is a list, not
    a dictionary, so it is deliberately generous about common English and about the words
    this specific prompt family produces."""
    t = _clean(token)
    if len(t) < 4 or t.casefold() in _STOP:
        return False
    if _HAS_DIGIT.match(t) or _INNER_CAP.search(t) or _PATHY.search(t):
        return True
    return t[0].isupper()


def named_strings(text: str) -> list[str]:
    """Every NAMED ≥4-char string in ``text`` — the WIDENED grounding candidate set (the
    ratified addendum edit #2).

    The shipped counter ``dp_caption_ungrounded_quote_total`` was specified over
    double-quoted spans only, and 32.6 % of OCR-derived strings enter a caption UNQUOTED,
    escaping that check entirely. Widening is what makes injection's headline safety
    property actually hold, so this returns:

      * every double-quoted span, and
      * every maximal run of adjacent namelike tokens (so ``Visual Studio Code`` is ONE
        candidate, not three).

    Deterministic and order-preserving; duplicates are collapsed case-insensitively.
    """
    out: list[str] = []
    seen: set[str] = set()

    def push(s: str) -> None:
        s = _clean(s)
        if len(s) < 4:
            return
        key = norm(s)
        if key and key not in seen:
            seen.add(key)
            out.append(s)

    for m in _QUOTED.finditer(text or ""):
        push(m.group(1))

    # A run continues only across a PLAIN SPACE, and only when the previous token did not
    # itself end a clause. "...in Gmail. Sarah replied" must not become the phrase
    # "Gmail Sarah" — that would be an invented two-word name, ungrounded by construction.
    # (``_TOKEN`` absorbs a trailing ``.`` so ``executor.py`` stays one token, which is why
    # the clause-end test looks at the RAW match rather than the gap alone.)
    run: list[str] = []
    prev_end, prev_raw = 0, ""
    for m in _TOKEN.finditer(text or ""):
        raw = m.group(0)
        gap = (text or "")[prev_end:m.start()]
        prev_end = m.end()
        joins = (bool(gap) and gap.strip() == "" and "\n" not in gap
                 and not prev_raw.endswith((".", "!", "?", ",", ";", ":")))
        prev_raw = raw
        if not _is_namelike(raw):
            if run:
                push(" ".join(run))
                run = []
            continue
        if run and not joins:
            push(" ".join(run))
            run = []
        run.append(_clean(raw))
    if run:
        push(" ".join(run))
    return out


def is_grounded(candidate: str, ocr_text: str) -> bool:
    """Is ``candidate`` present in the chunk's OCR text? Whole-phrase substring first;
    failing that, EVERY namelike token of the phrase must ground individually (so a caption
    that writes two separately-read names side by side is not scored as an invention)."""
    hay = norm(ocr_text)
    if not hay:
        return False
    if norm(candidate) in hay:
        return True
    tokens = [_clean(t) for t in _TOKEN.findall(candidate)]
    tokens = [t for t in tokens if len(t) >= 4]
    return bool(tokens) and all(norm(t) in hay for t in tokens)


def grounding(caption: str, ocr_text: str) -> dict[str, Any]:
    """Both grounding measures for one caption: the NARROW (quoted-span) rate the shipped
    counter implements, and the WIDE rate over all named ≥4-char strings. The gap between
    them is the number the addendum's measured 32.6 % refers to."""
    quoted = [_clean(m.group(1)) for m in _QUOTED.finditer(caption or "")]
    quoted = [q for q in quoted if len(q) >= 4]
    wide = named_strings(caption)
    q_bad = [q for q in quoted if not is_grounded(q, ocr_text)]
    w_bad = [w for w in wide if not is_grounded(w, ocr_text)]
    return {
        "quoted": len(quoted), "quoted_ungrounded": len(q_bad),
        "named": len(wide), "named_ungrounded": len(w_bad),
        "ungrounded_examples": w_bad[:5],
    }


def entity_recall(caption: str, entities: Iterable[str]) -> tuple[int, int]:
    """(recovered, total) ground-truth named entities present in the caption (lenient
    substring, casefolded). ``total == 0`` ⇒ the chunk contributes no denominator.

    The 2-char floor is the only filter: unlike the grounding scorer (which INFERS which
    strings are names and so needs a length heuristic), these entities were chosen by
    whoever labelled the corpus, and silently dropping their short ones — ``Q3``, ``v2`` —
    would quietly change the denominator of a gate."""
    hay = norm(caption)
    ents = [e for e in entities if e and len(e) >= 2]
    if not hay:
        return 0, len(ents)
    return sum(1 for e in ents if norm(e) in hay), len(ents)


def app_correct(app: str, truth_app: str) -> bool:
    """Lenient app match: either string containing the other, casefolded. ``Gmail`` vs
    ``Gmail — Inbox`` is correct; ``unknown`` never is."""
    a, b = norm(app), norm(truth_app)
    if not a or not b or a == "unknown":
        return False
    return a in b or b in a


def has_change_verb(caption: str) -> bool:
    low = norm(caption)
    return any(f" {v}" in f" {low}" for v in CHANGE_VERBS)


def mean(values: Iterable[float]) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0


# ======================================================================== OCR injection

def _pseudo_word(word: str, salt: str) -> str:
    """A deterministic false word of the same shape as ``word`` (length, capitalisation,
    digit positions). Used to falsify a ground-truth OCR string so ``propagation_rate`` can
    ask a precise question: did THIS false string reach the caption?"""
    digest = hashlib.sha256(f"{salt}\x00{word}".encode("utf-8")).digest()
    cons, vows, digits = "bdfgklmnprstvz", "aeiou", "3456789"
    out: list[str] = []
    for i, ch in enumerate(word):
        b = digest[i % len(digest)]
        if ch.isdigit():
            out.append(digits[b % len(digits)])
        elif ch.isalpha():
            pool = vows if i % 3 == 1 else cons
            c = pool[b % len(pool)]
            out.append(c.upper() if ch.isupper() else c)
        else:
            out.append(ch)
    return "".join(out)


def corrupt_text(text: str, fraction: float, salt: str) -> tuple[str, list[dict[str, str]]]:
    """Falsify ``fraction`` of the ≥4-char namelike tokens in ``text``, deterministically.

    Selection is by a stable hash of the token, NOT by position or a RNG, so the corrupted
    arm is reproducible on any machine and the same token is corrupted identically wherever
    it appears in the chunk. Returns the corrupted text plus the ``[{true, false}]`` map the
    propagation scorer looks for in the caption."""
    if fraction <= 0:
        return text, []
    swaps: dict[str, str] = {}
    for m in _TOKEN.finditer(text or ""):
        tok = _clean(m.group(0))
        if len(tok) < 4 or tok.casefold() in _STOP or tok in swaps:
            continue
        h = int.from_bytes(hashlib.sha256(f"{salt}\x00{tok}".encode()).digest()[:4], "big")
        if (h % 1000) / 1000.0 < fraction:
            false = _pseudo_word(tok, salt)
            if norm(false) != norm(tok):
                swaps[tok] = false
    out = text
    for true, false in swaps.items():
        out = re.sub(rf"(?<![A-Za-z0-9]){re.escape(true)}(?![A-Za-z0-9])", false, out)
    return out, [{"true": t, "false": f} for t, f in sorted(swaps.items())]


# ======================================================================== arm worker

def _forbid_storage() -> None:
    """Make "this harness cannot write to /context" an ENFORCED property, not a comment.

    ``app.stagegraph.executor`` imports ``ingest_core`` (for ``ProcessingError``), which
    imports ``StorageClient`` — so the class object is reachable even though no instance is
    ever built. Poisoning the constructor and the writer entry point means a future
    refactor that reaches for storage from below the seam fails loudly HERE, in an eval,
    instead of quietly minting experimental records in a real corpus."""
    from app import ingest_core, storage_client

    def _blocked(*_a, **_kw):
        raise RuntimeError(
            "offline eval (DP_OFFLINE_EVAL=1) must never touch storage — prompt_ab.py "
            "drives resolve()/run_graph()/build_c2 only; /context is written by "
            "ingest_core, which lives ABOVE the processor seam"
        )

    storage_client.StorageClient.__init__ = _blocked          # type: ignore[assignment]
    ingest_core.process_chunk = _blocked                      # type: ignore[assignment]


def _install_ocr_injector(arm: Arm, truth_by_chunk: dict[str, dict]) -> dict[str, list]:
    """Replace the OCR backend with the chunkset's GROUND TRUTH (optionally falsified).

    O-8 asks whether the caption is better because the OCR text is *true*, and whether it
    is worse when the OCR text is *false*. Both halves need the OCR content to be a known
    quantity — the mock backend's canned lines are unrelated to the chunk, and a real
    engine's output is exactly the confound under test. So the arm reads its regions from
    the chunkset's ``truth.ocr_regions`` through the SAME WS-C post-processing (confidence
    gate → role assignment → min-chars → redaction → dedup → budget → single line), i.e.
    only the *reader* is replaced, never the assembly.

    Honest caveat, stated in the report: ``version_fragment`` still reports the configured
    backend (``+ocr-mock-v1``), so a truth-injected arm's dialect names a backend it did not
    use. That is harmless offline (arms fork on ``arm.json`` via ``prompt_dir_fingerprint``,
    and nothing is written), and it is why the eval dialect must never reach production —
    which is what the ``DP_OFFLINE_EVAL`` boot guard in ``app/main.py`` enforces."""
    from app.vision import ocr as ocr_pkg
    from app.vision.clip_types import OcrRead, OcrRegion

    corrupted: dict[str, list] = {}

    class _TruthBackend:
        @staticmethod
        def make_client(cfg):
            return None

        @staticmethod
        def read(cfg, frame, client, chunk_id: str):
            truth = truth_by_chunk.get(chunk_id) or {}
            regions = []
            for r in truth.get("ocr_regions") or []:
                text = r.get("text", "")
                if arm.corrupt_fraction > 0:
                    text, swaps = corrupt_text(text, arm.corrupt_fraction, salt=chunk_id)
                    if swaps:
                        corrupted.setdefault(chunk_id, [])
                        known = {s["true"] for s in corrupted[chunk_id]}
                        corrupted[chunk_id].extend(s for s in swaps if s["true"] not in known)
                regions.append(OcrRegion(
                    text=text,
                    role=r.get("role", "") or "",
                    bbox=tuple(r.get("bbox") or (0.0, 0.0, 0.0, 0.0)),  # type: ignore[arg-type]
                    conf=float(r.get("conf", 0.95)),
                ))
            return OcrRead(t_offset_s=float(frame.t_offset_s), regions=tuple(regions))

    ocr_pkg.select = lambda cfg: _TruthBackend       # type: ignore[assignment]
    ocr_pkg.assert_health = lambda cfg: None         # type: ignore[assignment]
    return corrupted


def _install_usage_probe(current_chunk) -> dict[str, list[dict]]:
    """Capture ``usage`` off every captioner response, keyed by chunk.

    The vlm backend does not surface ``usage`` (it reads only ``choices``), and WS-D owns
    that file — so the probe patches the ONE documented client factory
    (``vlm.make_async_client``, whose docstring names it the patch point) rather than
    editing a stage. The chunk id travels on a ``ContextVar``: chunks run CONCURRENTLY, so
    a positional "everything since index N" attribution would silently mis-assign tokens
    between two in-flight chunks."""
    usage: dict[str, list[dict]] = {}
    try:
        from app.vision.clipcap import vlm
    except Exception:  # pragma: no cover - httpx-less checkout
        return usage
    original = vlm.make_async_client

    def factory(vs):
        client = original(vs)
        post = client.post

        async def wrapped(*a, **kw):
            resp = await post(*a, **kw)
            try:
                usage.setdefault(current_chunk.get(""), []).append(
                    (resp.json() or {}).get("usage") or {})
            except Exception:
                pass
            return resp

        client.post = wrapped  # type: ignore[method-assign]
        return client

    vlm.make_async_client = factory  # type: ignore[assignment]
    return usage


def run_arm_worker(arm: Arm, chunkset_path: str, out_path: str, limit: int,
                   concurrency: int = 8) -> int:
    """Run ONE arm over the chunkset in THIS process (env already set by the driver) and
    write one JSON object per chunk to ``out_path``.

    Chunks run CONCURRENTLY behind a semaphore, because the ingest path they emulate is
    concurrent (``INGEST_MODALITY_LIMITS=video=3``) and because a serial harness cannot meet
    the exit criterion: a 200-chunk arm at the design's ~1.6 s single-stream decode is ~5
    minutes serial and ~40 s at this default. Concurrency changes throughput only —
    every chunk is an independent graph run over its own ``StageContext``, and the output
    is re-sorted into manifest order, so the report is byte-identical at any width.

    Imports live inside the function so the DRIVER never pulls the app into its own
    interpreter."""
    import contextvars

    _forbid_storage()

    from app.pipeline import build_c2, compute_record_id
    from app.stagegraph.executor import resolve, run_graph
    from app.stagegraph.stage import StageContext, stages_for

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from capture_chunkset import load_chunkset  # noqa: E402

    manifest = load_chunkset(chunkset_path)
    chunks = manifest["chunks"][:limit] if limit else manifest["chunks"]
    truth_by_chunk = {c["chunk_id"]: (c.get("truth") or {}) for c in chunks}

    corrupted: dict[str, list] = {}
    if arm.ocr in ("truth", "corrupt30") and arm.pipeline == "clip":
        corrupted = _install_ocr_injector(arm, truth_by_chunk)
    current_chunk: contextvars.ContextVar[str] = contextvars.ContextVar("chunk", default="")
    usage_by_chunk = _install_usage_probe(current_chunk)

    resolved = resolve("video", stages_for("video"), None)

    async def one(entry: dict, sem: asyncio.Semaphore) -> dict[str, Any]:
        c1 = entry["c1_obj"]
        current_chunk.set(c1["chunk_id"])
        blob = (Path(entry["blob_path"]).read_bytes() if entry.get("blob_path")
                else b"notavideo")   # undecodable -> clipprep's documented mock fallback
        span = float(entry.get("span_seconds") or 10.0)
        ctx = StageContext(c1=c1, blob=blob, settings=None, span_seconds=span,
                           slots={}, resources=SimpleNamespace(metrics=None))
        row: dict[str, Any] = {"chunk_id": c1["chunk_id"], "arm": arm.name,
                               "pipeline_version": resolved.pipeline_version}
        t0 = time.perf_counter()
        async with sem:
            try:
                units = await run_graph(resolved, ctx)
            except Exception as exc:  # an arm that fails a chunk is DATA, not a crash
                row.update(error=f"{type(exc).__name__}: {exc}",
                           wall_s=time.perf_counter() - t0)
                return row
        wall = time.perf_counter() - t0

        records = [build_c2(c1, u, resolved.pipeline_version, "2026-07-25T00:00:00Z")
                   for u in units]
        caption = " ".join(r["content"]["text"] for r in records
                           if r["content"]["kind"] == "caption")
        desc = ctx.slots.get("clip")
        row.update(
            wall_s=wall,
            # zip, never a text lookup: build_c2 is applied unit-by-unit in order, and two
            # units of one chunk can legitimately carry identical text (two idle keyframes).
            records=[{
                "record_id": r["record_id"], "kind": r["content"]["kind"],
                "text": r["content"]["text"], "t_start": r["t_start"], "t_end": r["t_end"],
                "discriminator": u.discriminator,
            } for u, r in zip(units, records)],
            caption=caption,
            ocr_text=ctx.slots.get("ocr_text", ""),
            span_seconds=span,
            clip=({"app": desc.app, "activity": desc.activity,
                   "description": desc.description, "sensitive": desc.sensitive,
                   "parsed": desc.parsed, "raw_len": len(desc.raw)} if desc is not None else None),
            corrupted=corrupted.get(c1["chunk_id"], []),
            usage=usage_by_chunk.get(c1["chunk_id"], []),
            truth=entry.get("truth") or {},
        )
        # The identity proof: the record_id is recomputable from (chunk_id, pv, disc).
        row["record_id_check"] = all(
            r["record_id"] == compute_record_id(c1["chunk_id"], resolved.pipeline_version,
                                                r["discriminator"])
            for r in row["records"]
        )
        return row

    async def all_chunks() -> list[dict[str, Any]]:
        sem = asyncio.Semaphore(max(1, concurrency))
        return list(await asyncio.gather(*(one(e, sem) for e in chunks)))

    rows = asyncio.run(all_chunks())
    Path(out_path).write_text("\n".join(json.dumps(r) for r in rows) + "\n", "utf-8")
    return 0


# ======================================================================== arm assembly

def _packaged_prompt_dir() -> Path:
    return _SERVICE_ROOT / "app" / "vision" / "prompts"


def assemble_arm_dir(arm: Arm, dest: Path) -> Path:
    """Build a COMPLETE prompt registry for one arm.

    Copies the six packaged packs + ``schemas.json``, adds the arm's experimental pack,
    rewrites ``routes.json`` so ``family_defaults.clip`` and every screen scenario resolve
    to the arm's pack, and drops an ``arm.json`` recording the arm's identity. ``arm.json``
    is not read by anything — its whole job is to be HASHED: ``prompt_dir_fingerprint``
    covers ``*.json`` under the dir, so two arms whose pack text is identical (clean-OCR vs
    corrupted-OCR) still fork ``cfg_tag`` and therefore ``record_id``.

    ``LOCK.json`` is deliberately NOT copied: ``PACK_VERSION`` falls back to ``"1"``, and an
    eval arm must not claim the production pack's locked human version."""
    src = _packaged_prompt_dir()
    dest.mkdir(parents=True, exist_ok=True)
    for f in sorted(src.glob("*.prompt.md")):
        shutil.copy(f, dest / f.name)
    shutil.copy(src / "schemas.json", dest / "schemas.json")
    if arm.pack_file:
        extra = src / arm.pack_file
        if not extra.is_file():
            raise SystemExit(f"arm {arm.name!r}: experimental pack {extra} not found")
        shutil.copy(extra, dest / Path(arm.pack_file).name)

    routes = json.loads((src / "routes.json").read_text("utf-8"))
    routes["family_defaults"]["clip"] = arm.pack
    for scenario in list(routes.get("scenarios", {})):
        if scenario.startswith("screen"):
            routes["scenarios"][scenario] = arm.pack
    (dest / "routes.json").write_text(json.dumps(routes, indent=2, sort_keys=True) + "\n", "utf-8")
    (dest / "arm.json").write_text(json.dumps({
        "arm": arm.name, "pack": arm.pack, "pipeline": arm.pipeline, "ocr": arm.ocr,
        "corrupt_fraction": arm.corrupt_fraction, "note": arm.note,
        "warning": "offline eval arm — this directory is NEVER a production prompt source; "
                   "it exists to fork prompt_dir_fingerprint so arms cannot collide",
    }, indent=2, sort_keys=True) + "\n", "utf-8")
    return dest


def arm_env(arm: Arm, arm_dir: Path, base: dict[str, str]) -> dict[str, str]:
    env = dict(base)
    env["DP_OFFLINE_EVAL"] = "1"
    env["VIDEO_PIPELINE"] = arm.pipeline
    if arm.pipeline == "clip":
        env["VIDEO_PROMPT_DIR"] = str(arm_dir)
        env["VIDEO_CLIP_PROMPT"] = arm.pack
        if arm.ocr == "off":
            env["VIDEO_OCR_BACKEND"] = "off"
    else:
        # The legacy dialect is a frozen "" exemption (D-14) — it carries no pack and MUST
        # NOT be handed a prompt-dir override, which would be a silent claim it cannot make.
        env.pop("VIDEO_PROMPT_DIR", None)
        env.pop("VIDEO_CLIP_PROMPT", None)
    env.update(arm.env)
    return env


# ======================================================================== day-log scorer

def _load_continuum():
    """Import continuum's OWN ``build_daylog`` (never a reimplementation): the projected
    chars/block must be measured by the function that actually renders the training block.
    Returns ``(build_daylog, window_for, EXCERPT_CHARS)`` or ``None`` when the continuum
    service is not in this checkout."""
    import importlib.util
    import types

    root = _SERVICE_ROOT.parent / "continuum"
    if not (root / "app" / "daylog.py").is_file():
        return None
    pkg_name = "_wsh_continuum_app"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(root / "app")]  # type: ignore[attr-defined]
        sys.modules[pkg_name] = pkg
    daylog = importlib.import_module(f"{pkg_name}.daylog")
    window = importlib.import_module(f"{pkg_name}.window")
    excerpt = EXCERPT_CHARS_DEFAULT
    try:
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}._speed", root / "app" / "morpheus" / "profiles" / "speed.py")
        if spec and spec.loader:  # optional: pulls the real constant, not a copy
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            excerpt = int(getattr(mod, "EXCERPT_CHARS", EXCERPT_CHARS_DEFAULT))
    except Exception:
        pass
    return daylog.build_daylog, window.Window, excerpt


def daylog_projection(rows: list[dict], *, segment_seconds: int, block_segments: int,
                      user_id: str = "eval-user") -> dict[str, Any]:
    """Render every arm record through continuum's ``build_daylog`` and report the block
    character budget against ``EXCERPT_CHARS``.

    This is the D-11 correctness claim, measured rather than argued: the caption budget is a
    chars-per-second-of-life dial and the thing it must not blow is the amplifier's excerpt
    window. A block over ``EXCERPT_CHARS`` is silently TRUNCATED by
    ``speed.amplify_prompt`` — ordinally, so the OCR line (rendered last) dies first."""
    loaded = _load_continuum()
    if loaded is None:
        return {"available": False,
                "why": "continuum service not in this checkout — chars/block unmeasured"}
    build_daylog, Window, excerpt_chars = loaded
    from datetime import date, datetime, time, timedelta, timezone

    records = [{
        "content": {"kind": r["kind"], "text": r["text"]},
        "t_start": r["t_start"], "t_end": r["t_end"],
    } for row in rows for r in row.get("records", [])]
    if not records:
        return {"available": False, "why": "no records"}

    # The eval window, constructed INLINE (2026-07-27). This used to call continuum's
    # `window_for(user_id, day, "UTC")`, which the storage/C10 cutover DELETED along with
    # the rest of the local-date window arithmetic — the cycle window is now storage's
    # ingest-time watermark and windows are minted by `POST /training/windows`.
    #
    # This harness is an OFFLINE PROJECTION, not a consolidation: it needs bounds only so
    # `build_daylog` can bucket records, and it must never reach a storage service. So it
    # builds the value object directly, reproducing `window_for`'s old semantics EXACTLY —
    # a 04:00Z boundary, 24 h wide — because these numbers are compared across runs and a
    # changed window would silently move every chars-per-block figure this harness reports.
    day = date.fromisoformat(records[0]["t_start"][:10])
    start_utc = datetime.combine(day, time(4, 0), tzinfo=timezone.utc)
    end_utc = start_utc + timedelta(days=1)
    win = Window(
        window_id="w" + end_utc.strftime("%Y%m%dT%H%M%SZ"),  # opaque, storage's minted format
        user_id=user_id, tz="UTC", start_utc=start_utc, end_utc=end_utc)
    log = build_daylog(records, win, segment_seconds=segment_seconds,
                       block_segments=block_segments)
    lens = [len(b.text) for b in log.blocks]
    return {
        "available": True,
        "segment_seconds": segment_seconds,
        "block_segments": block_segments,
        "excerpt_chars": excerpt_chars,
        "blocks": len(lens),
        "segments": len(log.segments),
        "chars_per_block_mean": round(mean(lens), 1),
        "chars_per_block_max": max(lens) if lens else 0,
        "headroom_pct": round(100.0 * (1 - (max(lens) / excerpt_chars)), 1) if lens else None,
        "blocks_over_excerpt": sum(1 for n in lens if n > excerpt_chars),
    }


# ======================================================================== scoring an arm

def score_arm(arm: Arm, rows: list[dict], *, segment_seconds: int, block_segments: int,
              backend: str) -> dict[str, Any]:
    ok = [r for r in rows if not r.get("error")]
    errors = [r for r in rows if r.get("error")]
    caps = []
    truncated = 0
    for r in ok:
        clip = r.get("clip")
        if not clip:
            continue
        cap = _caption_cap_for(r["span_seconds"])
        caps.append(cap)
        rendered = len(r.get("caption", ""))
        if rendered >= cap or clip["raw_len"] > cap:
            truncated += 1

    recalls, recall_n, recall_d = [], 0, 0
    app_hits, app_n = 0, 0
    prop_hits, prop_n = 0, 0
    g_named = g_named_bad = g_quoted = g_quoted_bad = 0
    examples: list[str] = []
    for r in ok:
        caption = r.get("caption", "")
        truth = r.get("truth") or {}
        got, tot = entity_recall(caption, truth.get("entities") or [])
        if tot:
            recalls.append(got / tot)
            recall_n += got
            recall_d += tot
        if truth.get("app"):
            app_n += 1
            app_hits += int(app_correct((r.get("clip") or {}).get("app", ""), truth["app"]))
        corrupted = r.get("corrupted") or []
        if corrupted:
            prop_n += 1
            prop_hits += int(any(norm(s["false"]) in norm(caption) for s in corrupted))
        g = grounding(caption, r.get("ocr_text", ""))
        g_named += g["named"]
        g_named_bad += g["named_ungrounded"]
        g_quoted += g["quoted"]
        g_quoted_bad += g["quoted_ungrounded"]
        examples.extend(g["ungrounded_examples"])

    prompt_tok = sum(u.get("prompt_tokens", 0) for r in ok for u in (r.get("usage") or []))
    completion_tok = sum(u.get("completion_tokens", 0) for r in ok for u in (r.get("usage") or []))
    node_s = prompt_tok / PREFILL_TOK_PER_S + completion_tok / DECODE_TOK_PER_S

    return {
        "arm": arm.name,
        "note": arm.note,
        "pack": arm.pack,
        "pipeline": arm.pipeline,
        "ocr": arm.ocr,
        "chunks": len(rows),
        "errors": len(errors),
        "error_examples": [e["error"] for e in errors[:3]],
        "pipeline_version": rows[0]["pipeline_version"] if rows else "",
        "record_ids": [r["record_id"] for r in (ok[0]["records"] if ok else [])],
        "record_id_recomputes": all(r.get("record_id_check", False) for r in ok),
        "records_per_chunk": round(mean(len(r["records"]) for r in ok), 3),
        "chars_per_record": round(mean(len(rec["text"]) for r in ok for rec in r["records"]), 1),
        "chars_per_caption": round(mean(len(r.get("caption", "")) for r in ok), 1),
        "caption_cap": round(mean(caps), 1) if caps else None,
        "truncation_rate": round(truncated / len(ok), 3) if ok else None,
        "parse_fallback_rate": round(
            sum(1 for r in ok if r.get("clip") and not r["clip"]["parsed"]) / len(ok), 3
        ) if ok else None,
        "app_known_rate": round(
            sum(1 for r in ok if (r.get("clip") or {}).get("app", "").strip().casefold()
                not in ("", "unknown")) / len(ok), 3
        ) if ok else None,
        "change_verb_rate": round(
            sum(1 for r in ok if has_change_verb(r.get("caption", ""))) / len(ok), 3
        ) if ok else None,
        "ungrounded_quote_rate": round(g_quoted_bad / g_quoted, 3) if g_quoted else None,
        "ungrounded_named_rate": round(g_named_bad / g_named, 3) if g_named else None,
        "named_strings_total": g_named,
        "quoted_spans_total": g_quoted,
        "ungrounded_examples": examples[:8],
        "named_entity_recall": round(mean(recalls), 4) if recalls else None,
        "named_entity_recall_micro": round(recall_n / recall_d, 4) if recall_d else None,
        "app_correct": round(app_hits / app_n, 4) if app_n else None,
        "propagation_rate": round(prop_hits / prop_n, 4) if prop_n else None,
        "propagation_chunks": prop_n,
        "wall_s": round(sum(r.get("wall_s", 0.0) for r in rows), 2),
        "prompt_tokens": prompt_tok,
        "completion_tokens": completion_tok,
        "node_seconds": round(node_s, 3),
        "usd": round(node_s / 3600.0 * NODE_DOLLARS_PER_HOUR, 5),
        "daylog": daylog_projection(ok, segment_seconds=segment_seconds,
                                    block_segments=block_segments),
    }


def _caption_cap_for(span_seconds: float) -> int:
    """``budget.caption_cap`` recomputed from the env, so the DRIVER stays app-free (it
    must never be able to resolve a graph or reach a backend by accident — only the arm
    subprocesses import the app). Same formula and same defaults as
    ``app/vision/budget.py``: ``round(min(share, total) × span)``."""
    rate = int(os.getenv("VIDEO_CAPTION_CHARS_SHARE", "16"))
    total = int(os.getenv("VIDEO_CHARS_PER_SECOND", "22"))
    return max(0, round(max(0, min(rate, total)) * max(0.0, span_seconds)))


# ======================================================================== the O-8 gate

O8_RULE = ("ship A (injection) iff recall_lift > 0.25 AND propagation_rate < 0.10; "
           "else ship D (screen-clip-hint-v1)")


def o8_verdict(scores: dict[str, dict], *, backend: str = "mock") -> dict[str, Any]:
    """The PRE-REGISTERED O-8 decision. Written before any number was seen, evaluated
    without a free parameter: no post-hoc threshold, no arm dropped, and an UNDECIDED
    verdict when the corpus cannot answer — which is itself a result. An unlabelled or
    mock-captioned corpus must never be allowed to ratify a cutover, and "the numbers came
    out equal" is exactly what a captioner that reads no prompt produces."""
    a, b, d = scores.get("injected"), scores.get("blind"), scores.get("hint")
    out: dict[str, Any] = {"rule": O8_RULE, "thresholds": {"recall_lift": 0.25,
                                                           "propagation_rate": 0.10}}
    if backend == "mock":
        return {**out, "verdict": "UNDECIDED",
                "why": "VIDEO_BACKEND=mock — the mock captioner never reads the prompt or "
                       "the injected OCR, so every arm is identical by construction. The "
                       "gate requires a real captioner (VIDEO_BACKEND=vlm|vertex)."}
    if not a or not b:
        return {**out, "verdict": "UNDECIDED", "why": "both the injected and blind arms are required"}
    ra, rb = a.get("named_entity_recall"), b.get("named_entity_recall")
    corrupt = scores.get("injected-corrupt") or {}
    prop = corrupt.get("propagation_rate")
    out.update(recall_injected=ra, recall_blind=rb,
               recall_lift=(round(ra - rb, 4) if ra is not None and rb is not None else None),
               propagation_rate=prop,
               recall_hint=(d or {}).get("named_entity_recall"))
    if ra is None or rb is None:
        return {**out, "verdict": "UNDECIDED",
                "why": "the corpus carries no ground-truth entities — label `truth.entities` "
                       "(capture_chunkset.py synth does this automatically)"}
    if prop is None:
        return {**out, "verdict": "UNDECIDED",
                "why": "no corrupted-OCR arm ran — the propagation half of the rule is "
                       "unmeasured (add the `injected-corrupt` arm)"}
    lift = ra - rb
    ship_a = lift > 0.25 and prop < 0.10
    return {**out,
            "verdict": "SHIP A (injection)" if ship_a else "SHIP D (screen-clip-hint-v1)",
            "why": (f"recall_lift={lift:.4f} {'>' if lift > 0.25 else '<='} 0.25 and "
                    f"propagation_rate={prop:.4f} {'<' if prop < 0.10 else '>='} 0.10")}


# ======================================================================== reporting

def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def print_report(report: dict[str, Any]) -> None:
    scores = report["arms"]
    names = list(scores)
    w = max(24, *(len(n) for n in names)) if names else 24

    print()
    print("=" * 100)
    print(f"prompt_ab · chunkset={report['chunkset']} · chunks={report['chunks']} · "
          f"backend={report['backend']} · ocr_backend={report['ocr_backend']}")
    print("=" * 100)
    for line in report["advisories"]:
        print(f"  ! {line}")
    if report["advisories"]:
        print()

    print("-- the fork is real (pipeline_version + the resulting record_ids) " + "-" * 34)
    for n in names:
        s = scores[n]
        print(f"  {n:<{w}}  {s['pipeline_version']}")
        for rid in s["record_ids"]:
            print(f"  {'':<{w}}    record_id {rid}")
        print(f"  {'':<{w}}    record_id recomputes from (chunk_id, pv, discriminator): "
              f"{s['record_id_recomputes']}")
    distinct = {scores[n]["pipeline_version"] for n in names}
    print(f"  => {len(distinct)} distinct dialect(s) across {len(names)} arm(s)"
          f"{'  ** COLLISION **' if len(distinct) != len(names) else ''}")

    rows = [
        ("records/chunk", "records_per_chunk"),
        ("chars/record", "chars_per_record"),
        ("chars/caption", "chars_per_caption"),
        ("caption cap (D-11)", "caption_cap"),
        ("truncation rate", "truncation_rate"),
        ("parse-fallback rate", "parse_fallback_rate"),
        ("app != unknown", "app_known_rate"),
        ("change-verb rate", "change_verb_rate"),
        ("ungrounded QUOTE rate", "ungrounded_quote_rate"),
        ("ungrounded NAMED rate", "ungrounded_named_rate"),
        ("named strings seen", "named_strings_total"),
        ("quoted spans seen", "quoted_spans_total"),
        ("named-entity recall", "named_entity_recall"),
        ("app_correct", "app_correct"),
        ("propagation rate", "propagation_rate"),
        ("errors", "errors"),
        ("wall seconds", "wall_s"),
        ("prompt tokens", "prompt_tokens"),
        ("completion tokens", "completion_tokens"),
        ("projected USD", "usd"),
    ]
    print()
    print("-- mechanical scorers " + "-" * 77)
    head = f"  {'metric':<24}" + "".join(f"{n:>18}" for n in names)
    print(head)
    print("  " + "-" * (len(head) - 2))
    for label, key in rows:
        print(f"  {label:<24}" + "".join(f"{_fmt(scores[n].get(key)):>18}" for n in names))

    print()
    print("-- day-log projection (continuum build_daylog, not a reimplementation) " + "-" * 28)
    for n in names:
        d = scores[n]["daylog"]
        if not d.get("available"):
            print(f"  {n:<{w}}  unavailable: {d.get('why')}")
            continue
        print(f"  {n:<{w}}  blocks={d['blocks']:<4} chars/block mean={d['chars_per_block_mean']:<8} "
              f"max={d['chars_per_block_max']:<6} EXCERPT_CHARS={d['excerpt_chars']} "
              f"headroom={d['headroom_pct']}%  over-budget blocks={d['blocks_over_excerpt']}")

    ung = {n: scores[n]["ungrounded_examples"] for n in names if scores[n]["ungrounded_examples"]}
    if ung:
        print()
        print("-- ungrounded named strings (the widened scorer's actual finds) " + "-" * 35)
        for n, ex in ung.items():
            print(f"  {n:<{w}}  {ex}")

    if report.get("o8"):
        v = report["o8"]
        print()
        print("-- O-8 gate (pre-registered) " + "-" * 70)
        print(f"  rule            : {v['rule']}")
        print(f"  recall injected : {_fmt(v.get('recall_injected'))}")
        print(f"  recall blind    : {_fmt(v.get('recall_blind'))}")
        print(f"  recall hint (D) : {_fmt(v.get('recall_hint'))}")
        print(f"  recall lift     : {_fmt(v.get('recall_lift'))}   (threshold > 0.25)")
        print(f"  propagation     : {_fmt(v.get('propagation_rate'))}   (threshold < 0.10)")
        print(f"  VERDICT         : {v['verdict']}")
        print(f"  why             : {v['why']}")

    if report.get("checks"):
        print()
        print("-- gates " + "-" * 89)
        for check in report["checks"]:
            print(f"  [{'PASS' if check['ok'] else 'FAIL'}] {check['name']}: {check['detail']}")

    c = report["cost"]
    print()
    print("-- exit criterion: cheap enough to be a pre-push hook " + "-" * 46)
    print(f"  measured: {c['chunks']} chunks × {c['arms']} arm(s) in {c['wall_s']:.1f}s wall, "
          f"projected ${c['usd']:.4f}")
    print(f"  per 200 chunks (1 arm): ~{c['wall_s_per_200']:.1f}s wall, ~${c['usd_per_200']:.4f}")
    print("  [§11's exit criterion is ~40s / ~$0.02. The wall target holds. The DOLLAR "
          "target does not\n"
          "   reconcile with §7.3's own arithmetic: 200 chunks × 1,517 prefill + 60 output "
          "tok at\n"
          "   12k/2k tok/s and $16/node-h is ~$0.139 — i.e. $0.250/screen-hour ÷ 360 "
          "chunks × 200.\n"
          "   14 cents is still pre-push cheap; the stated $0.02 is ~7x low. See "
          "handoff/ws-video-clip-eval.md.]")
    print("=" * 100)


# ======================================================================== driver

def _base_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("VIDEO_BACKEND", "mock")
    env.setdefault("VIDEO_OCR_BACKEND", "mock")
    env.setdefault("ASR_BACKEND", "mock")
    env.setdefault("PYTHONPATH", str(_SERVICE_ROOT))
    return env


def run_checks(scores: dict[str, dict], *, max_ungrounded: float | None) -> list[dict[str, Any]]:
    """The pre-push GATE. Four properties that must hold on any corpus, under any backend —
    they are about the pipeline, not about the model, so they are safe to fail a push on:

      1. no arm errored on any chunk (a chunk that raises is a chunk that dead-letters);
      2. no two arms share a ``pipeline_version`` (a collision means an experiment would
         overwrite another experiment's — or production's — records under one id);
      3. every ``record_id`` recomputes from ``(chunk_id, pipeline_version, discriminator)``
         (the C2 idempotency contract, §4 R4);
      4. no day-log block exceeds ``EXCERPT_CHARS`` (D-11: the budget is a correctness knob,
         and truncation is ordinal, so the OCR line dies first and silently).

    ``--max-ungrounded-named`` adds a fifth, opt-in, model-dependent one."""
    checks: list[dict[str, Any]] = []
    errored = {n: s["errors"] for n, s in scores.items() if s["errors"]}
    checks.append({"name": "no chunk errors", "ok": not errored,
                   "detail": "clean" if not errored else f"errors per arm: {errored}"})

    pvs = {n: s["pipeline_version"] for n, s in scores.items()}
    distinct = len(set(pvs.values()))
    checks.append({"name": "arms fork (no dialect collision)", "ok": distinct == len(pvs),
                   "detail": f"{distinct} distinct pipeline_version over {len(pvs)} arm(s)"})

    bad_ids = [n for n, s in scores.items() if not s["record_id_recomputes"]]
    checks.append({"name": "record_id recomputes", "ok": not bad_ids,
                   "detail": "all arms" if not bad_ids else f"broken in {bad_ids}"})

    over = {n: s["daylog"]["blocks_over_excerpt"] for n, s in scores.items()
            if s["daylog"].get("available") and s["daylog"]["blocks_over_excerpt"]}
    measured = [n for n, s in scores.items() if s["daylog"].get("available")]
    checks.append({
        "name": "day-log blocks within EXCERPT_CHARS", "ok": not over,
        "detail": ("unmeasured (continuum not in this checkout)" if not measured
                   else "all blocks within budget" if not over else f"over budget: {over}"),
    })

    if max_ungrounded is not None:
        bad = {n: s["ungrounded_named_rate"] for n, s in scores.items()
               if s["ungrounded_named_rate"] is not None
               and s["ungrounded_named_rate"] > max_ungrounded}
        checks.append({"name": f"ungrounded named rate <= {max_ungrounded}", "ok": not bad,
                       "detail": "within bound" if not bad else f"exceeded: {bad}"})
    return checks


def _advisories(base: dict[str, str], manifest: dict) -> list[str]:
    out: list[str] = []
    if base.get("VIDEO_BACKEND", "mock") == "mock":
        out.append(
            "VIDEO_BACKEND=mock — the mock captioner returns a canned string derived from "
            "(n_frames, span, chunk_id) and NEVER reads the injected OCR. Every plumbing "
            "property below is real; every QUALITY number (recall, grounding, app_correct, "
            "propagation) measures the harness, not a model. Set VIDEO_BACKEND=vlm to score."
        )
    if not manifest.get("labelled"):
        out.append(
            "the chunkset carries no `truth.entities` — named_entity_recall / app_correct "
            "have a zero denominator and the O-8 gate will return UNDECIDED. Build a "
            "labelled corpus with `capture_chunkset.py synth`, or label a sliced one."
        )
    if not manifest.get("has_blobs"):
        out.append(
            "the chunkset has no blobs — clipprep takes its synthetic-frames fallback, so "
            "frame selection / delta classification are NOT exercised (by design: this "
            "shape is the committable JSON-only smoke corpus)."
        )
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="prompt_ab.py",
        description="Offline prompt A/B + quality gates over a chunkset (never writes /context).",
    )
    p.add_argument("--chunkset", required=True)
    p.add_argument("--arms", default=",".join(DEFAULT_ARMS),
                   help=f"comma-separated arm names from: {', '.join(sorted(ARMS))}")
    p.add_argument("--gate", choices=("o8",), help="run the pre-registered O-8 arm set + verdict")
    p.add_argument("--limit", type=int, default=0, help="only the first N chunks")
    p.add_argument("--concurrency", type=int, default=8,
                   help="chunks in flight per arm (throughput only — results are identical "
                        "at any width; keep <= the served endpoint's comfortable batch)")
    p.add_argument("--segment-seconds", type=int, default=60,
                   help="day-log segment length (the recommended production recipe is 60)")
    p.add_argument("--block-segments", type=int, default=2,
                   help="day-log block length in segments (recommended 2 — dose 15.1x)")
    p.add_argument("--json", help="write the full machine-readable report here")
    p.add_argument("--rows-dir",
                   help="write each arm's per-chunk rows to <dir>/<arm>.jsonl "
                        "(what scripts/oracle_gemini.py judges)")
    p.add_argument("--check", action="store_true",
                   help="run the pre-push gates and exit non-zero if any fails")
    p.add_argument("--max-ungrounded-named", type=float,
                   help="with --check: fail if an arm's widened ungrounded-named rate "
                        "exceeds this (model-dependent — only meaningful under a real backend)")
    p.add_argument("--keep-arm-dirs", action="store_true",
                   help="keep the assembled per-arm prompt registries for inspection")
    p.add_argument("--_arm-worker", help=argparse.SUPPRESS)
    p.add_argument("--_arm-out", help=argparse.SUPPRESS)
    args = p.parse_args(sys.argv[1:] if argv is None else argv)

    # PARKED (DP rebuild Stage C): the arm machinery below targets the deleted v0
    # graph/config surface — see the module docstring for exactly what a rebuild
    # needs. Refuse loudly rather than half-run against the new API.
    print(
        "prompt_ab.py is PARKED: it drives the retired v0 stage graph (env-forked "
        "arms, VIDEO_PROMPT_DIR registries, per-unit records). The DP rebuild made "
        "experiments in-code (.exp-<code> dialects); see this file's docstring for "
        "the rebuild checklist.",
        file=sys.stderr,
    )
    return 3

    # ---- worker mode (one arm, this process, env already set) ------------------------
    if args._arm_worker:
        arm = Arm(**json.loads(args._arm_worker))
        return run_arm_worker(arm, args.chunkset, args._arm_out, args.limit,
                              concurrency=args.concurrency)

    # ---- driver ----------------------------------------------------------------------
    if os.getenv("DP_OFFLINE_EVAL", "").strip().lower() in ("", "0", "false", "no", "off"):
        print("refusing to run: set DP_OFFLINE_EVAL=1.\n"
              "  It unlocks the prompt-pack overrides this harness needs, AND it makes "
              "app/main.py refuse to boot — the flag that enables experiments is the flag "
              "that prevents serving.", file=sys.stderr)
        return 2

    names = list(O8_ARMS) if args.gate == "o8" else [n.strip() for n in args.arms.split(",") if n.strip()]
    unknown = [n for n in names if n not in ARMS]
    if unknown:
        print(f"unknown arm(s) {unknown}; known: {sorted(ARMS)}", file=sys.stderr)
        return 2

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from capture_chunkset import load_chunkset  # noqa: E402
    manifest = load_chunkset(args.chunkset)
    n_chunks = min(len(manifest["chunks"]), args.limit) if args.limit else len(manifest["chunks"])

    base = _base_env()
    workdir = Path(tempfile.mkdtemp(prefix="prompt-ab-"))
    scores: dict[str, dict] = {}
    t_all = time.perf_counter()
    try:
        for name in names:
            arm = ARMS[name]
            arm_dir = assemble_arm_dir(arm, workdir / name) if arm.pipeline == "clip" else workdir / name
            arm_dir.mkdir(parents=True, exist_ok=True)
            out = workdir / f"{name}.jsonl"
            cmd = [sys.executable, str(Path(__file__).resolve()),
                   "--chunkset", str(args.chunkset),
                   "--limit", str(args.limit),
                   "--concurrency", str(args.concurrency),
                   "--_arm-worker", json.dumps(arm.__dict__, default=str),
                   "--_arm-out", str(out)]
            t0 = time.perf_counter()
            proc = subprocess.run(cmd, env=arm_env(arm, arm_dir, base),
                                  capture_output=True, text=True)
            if proc.returncode != 0 or not out.is_file():
                print(f"arm {name!r} failed (rc={proc.returncode}):\n{proc.stderr[-3000:]}",
                      file=sys.stderr)
                return 1
            rows = [json.loads(line) for line in out.read_text("utf-8").splitlines() if line.strip()]
            if args.rows_dir:
                Path(args.rows_dir).mkdir(parents=True, exist_ok=True)
                shutil.copy(out, Path(args.rows_dir) / f"{name}.jsonl")
            print(f"  arm {name:<18} {len(rows):>4} chunks  {time.perf_counter() - t0:6.2f}s")
            scores[name] = score_arm(arm, rows, segment_seconds=args.segment_seconds,
                                     block_segments=args.block_segments,
                                     backend=base.get("VIDEO_BACKEND", "mock"))
    finally:
        if not args.keep_arm_dirs:
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            print(f"  (arm dirs kept at {workdir})")

    wall = time.perf_counter() - t_all
    usd = sum(s["usd"] for s in scores.values())
    per_arm_wall = wall / max(1, len(names))
    report = {
        "chunkset": manifest["name"],
        "chunkset_mode": manifest.get("mode"),
        "chunks": n_chunks,
        "backend": base.get("VIDEO_BACKEND", "mock"),
        "ocr_backend": base.get("VIDEO_OCR_BACKEND", "mock"),
        "advisories": _advisories(base, manifest),
        "arms": scores,
        "o8": (o8_verdict(scores, backend=base.get("VIDEO_BACKEND", "mock"))
               if args.gate == "o8" else None),
        "checks": (run_checks(scores, max_ungrounded=args.max_ungrounded_named)
                   if args.check else None),
        "cost": {
            "chunks": n_chunks, "arms": len(names), "wall_s": wall, "usd": usd,
            "wall_s_per_200": (per_arm_wall / max(1, n_chunks)) * 200,
            "usd_per_200": (usd / max(1, len(names)) / max(1, n_chunks)) * 200,
        },
    }
    print_report(report)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2) + "\n", "utf-8")
        print(f"wrote {args.json}")
    if report["checks"] and any(not c["ok"] for c in report["checks"]):
        return 1
    if report["o8"] and report["o8"]["verdict"] == "UNDECIDED":
        return 3
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
