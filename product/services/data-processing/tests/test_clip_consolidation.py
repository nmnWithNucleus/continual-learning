"""WS-D consolidation (tab D3) — the clip captioner integration surface, end to end.

D1 (pack/config/version) and D2 (primary/parse/emit) each proved their half in
``tests/test_prompt_pack.py`` / ``tests/test_budget.py`` and ``tests/test_clipcap.py`` /
``tests/test_parse.py``. This file is the D3 gate that ties the layers together across the
D1↔D2 seam and asserts the two §11 WS-D exit criteria that are inherently cross-layer:

  * **The mock ``clipcap`` seam emits exactly ONE ``kind='caption'`` record with the frozen
    shape and a byte-identical span** — driven through the real composed clip dialect
    (``backend.PIPELINE_VERSION + backend.prompt_tag(vs) + cfg_tag(vs)``), ``emit.caption_unit``
    and ``pipeline.build_c2``, validated against the frozen C2 schema (task 2).
  * **One byte of a ``.prompt.md`` edit forks ``pipeline_version`` → ``record_id`` end to end**,
    a semantic edit forks and a whitespace-only edit does not, and the ``mock`` corpus is NOT
    re-keyed by a prompt edit (task 4).

Headless + offline (house rule 5): the ``mock`` backend needs no GPU / no network. The clip
stage itself (``app/stages/video/clipcap.py``) stays HELD — its ``needs=("clipprep",
"screentext")`` cannot resolve until WS-B/WS-C register those stages (stagegraph ``_discover``
raises on an unknown need), so the record-contract is proven through ``emit.caption_unit`` +
``build_c2`` exactly as it will run inside ``ClipcapStage.assemble``.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from app import schemas
from app.pipeline import build_c2, compute_record_id
from app.vision import clipcap, emit, prompts
from app.vision.budget import caption_cap
from app.vision.clip_types import ClipDesc, ClipFrames, Frame
from app.vision.config import get_vision_settings
from app.vision.prompts import compute_digest, load_registry
from app.vision.version import cfg_tag


# ---- fixtures (self-contained; mirror the C1 envelope a video chunk carries) ----

def _clip(n=4, span=10.0, idle=False):
    frames = tuple(
        Frame(index=i, t_offset_s=round(i * (span / max(1, n)), 1),
              jpeg_lo=f"lo{i}".encode(), jpeg_hi=f"hi{i}".encode())
        for i in range(n)
    )
    return ClipFrames(frames=frames, ocr_times=tuple(f.t_offset_s for f in frames),
                      idle=idle, span_s=span)


def _video_c1(chunk_id="chunk-CLIP-CONSOL-01", t_start="2026-07-24T10:00:00Z",
              t_end="2026-07-24T10:00:10Z"):
    return {
        "contract": "C1", "version": "0", "user_id": "pilot-user",
        "device_id": "mac-cli-1", "stream_id": "stream-AAAA", "sequence": 0,
        "chunk_id": chunk_id, "modality": "video", "codec": "video/mp4",
        "t_start": t_start, "t_end": t_end,
        "blob_ref": f"raw/pilot-user/{chunk_id}.mp4",
        "blob_sha256": "0" * 64, "blob_bytes": 10,
    }


_EMPTY_ENRICHMENTS = {"speakers": [], "faces": [], "places": [], "objects": []}


# ======================================================================================
# Task 2 — the mock clipcap seam: one chunk -> exactly one frozen caption record
# ======================================================================================

def test_mock_clipcap_seam_emits_one_frozen_caption_with_byte_identical_span(monkeypatch):
    """End to end through the REAL composed clip dialect: fixture ClipFrames -> mock
    backend describe -> emit.caption_unit (the clip primary's record SET) -> build_c2.
    Asserts the record SET is exactly one ``kind='caption'`` with the frozen shape (D-05),
    a byte-identical span (the Z-vs-+00:00 hazard structurally unreachable), and C2-valid."""
    monkeypatch.setenv("VIDEO_BACKEND", "mock")
    monkeypatch.setenv("VIDEO_PIPELINE", "clip")   # the production clip regime
    vs = get_vision_settings()

    backend = clipcap.select(vs)
    assert backend.PIPELINE_VERSION == "vidclip-mock-v1"

    # the REAL clip dialect the ClipcapStage.version_fragment composes (D-13). Mock ->
    # prompt_tag "" (no '@...'); cfg_tag present ('#...').
    pv = backend.PIPELINE_VERSION + backend.prompt_tag(vs) + cfg_tag(vs)
    assert pv.startswith("vidclip-mock-v1#") and "@" not in pv

    span = 10.0
    clip = _clip(n=4, span=span)
    c1 = _video_c1(t_start="2026-07-24T10:00:00Z", t_end="2026-07-24T10:00:10Z")

    desc = asyncio.run(backend.describe(vs, clip, "region:[main] a compose window", c1))

    # the clip primary's record SET (ClipcapStage.assemble returns exactly this list)
    units = [emit.caption_unit(desc, span, vs)]
    assert len(units) == 1                                   # EXACTLY one unit
    unit = units[0]

    c2s = [build_c2(c1, u, pv, "2026-07-24T10:00:12Z") for u in units]
    captions = [c for c in c2s if c["content"]["kind"] == "caption"]
    assert len(captions) == 1                                # EXACTLY one caption record
    c2 = captions[0]

    # --- frozen shape (D-05) ---
    assert unit.discriminator == ""                          # canonical v0 1:1 id
    assert unit.t_start is None and unit.t_end is None       # -> build_c2 carries C1's span
    assert c2["content"]["kind"] == "caption"
    assert c2["enrichments"] == _EMPTY_ENRICHMENTS
    assert c2["record_id"] == compute_record_id(c1["chunk_id"], pv, "")
    assert c2["pipeline_version"] == pv

    # --- byte-identical span (D-05 timestamp-form invariant) ---
    assert c2["t_start"] == c1["t_start"] == "2026-07-24T10:00:00Z"   # the trailing 'Z' survives
    assert c2["t_end"] == c1["t_end"] == "2026-07-24T10:00:10Z"

    # --- single line, within the char budget (D-11 / D-12) ---
    text = c2["content"]["text"]
    assert "\n" not in text
    assert len(text) <= caption_cap(span, vs)

    # --- validates against the frozen C2 contract ---
    assert schemas.validate_c2(c2) == []


def test_mock_clipcap_seam_is_deterministic_across_workers(monkeypatch):
    """Determinism contract (house rule 6): identical bytes + settings -> byte-identical
    C2 on any worker in the fleet — same record_id, same span, same text."""
    monkeypatch.setenv("VIDEO_BACKEND", "mock")
    monkeypatch.setenv("VIDEO_PIPELINE", "clip")
    vs = get_vision_settings()
    backend = clipcap.select(vs)
    pv = backend.PIPELINE_VERSION + backend.prompt_tag(vs) + cfg_tag(vs)
    c1 = _video_c1()

    def one():
        desc = asyncio.run(backend.describe(vs, _clip(), "ocr", c1))
        return build_c2(c1, emit.caption_unit(desc, 10.0, vs), pv, "2026-07-24T10:00:12Z")

    assert one() == one()


# ======================================================================================
# Task 4 — one byte of a .prompt.md edit forks pipeline_version -> record_id, end to end
# ======================================================================================

_PKG_PROMPTS = Path(prompts.__file__).parent


def _copy_packs(dst: Path) -> Path:
    """Copy the packaged pack tree (``*.prompt.md`` + routes/schemas/LOCK) into ``dst`` so a
    test can edit one byte and reload it through the REAL loader without touching the tree."""
    dst.mkdir(parents=True, exist_ok=True)
    for f in _PKG_PROMPTS.iterdir():
        if f.is_file() and f.suffix in (".md", ".json"):
            shutil.copy(f, dst / f.name)
    return dst


def _digest_of(source_dir: Path) -> str:
    packs, routes, _ = load_registry(source_dir)
    return compute_digest(packs, routes)


def test_prompt_byte_edit_forks_record_id_end_to_end(tmp_path, monkeypatch):
    """A SEMANTIC one-byte edit to a ``.prompt.md`` forks PACK_DIGEST -> version_tag ->
    the composed clip ``pipeline_version`` -> ``record_id``; a WHITESPACE-only edit forks
    nothing (D-13 normalisation). Uses the real ``load_registry`` / ``compute_digest`` on
    copied trees, cross-checked against the live ``version_tag`` so the reconstruction is
    provably the real dialect format."""
    monkeypatch.setenv("VIDEO_BACKEND", "vlm")     # vlm backend carries the prompt tag
    monkeypatch.setenv("VIDEO_PIPELINE", "clip")
    monkeypatch.setenv("VIDEO_SCENARIO", "screen-mac")
    vs = get_vision_settings()
    from app.vision.clipcap import vlm as vlm_backend

    pack_id = prompts.select(vs).id                # the pack the scenario resolves to
    pack_ver = prompts.PACK_VERSION

    # baseline: a faithful copy reproduces the packaged digest AND the real version_tag.
    base = _copy_packs(tmp_path / "base")
    d_base = _digest_of(base)
    assert d_base == prompts.PACK_DIGEST
    assert f"@{pack_id}.p{pack_ver}.{d_base}" == prompts.version_tag(vs)

    # one SEMANTIC byte: change an actual word in the model-facing [system] text.
    sem = _copy_packs(tmp_path / "sem")
    sp = sem / "screen-clip-v1.prompt.md"
    edited = sp.read_text("utf-8").replace("personal memory system",
                                           "personal memory systemX", 1)
    assert edited != sp.read_text("utf-8")
    sp.write_text(edited, "utf-8")
    d_sem = _digest_of(sem)
    assert d_sem != d_base                          # semantic edit forks PACK_DIGEST

    # WHITESPACE-only: trailing spaces on a line + trailing blank lines (both normalised away).
    ws = _copy_packs(tmp_path / "ws")
    wp = ws / "screen-clip-v1.prompt.md"
    wtext = wp.read_text("utf-8").replace("RULES, applied strictly:",
                                          "RULES, applied strictly:    ", 1) + "\n\n\n"
    assert wtext != wp.read_text("utf-8")
    wp.write_text(wtext, "utf-8")
    d_ws = _digest_of(ws)
    assert d_ws == d_base                            # whitespace-only does NOT fork

    # ---- end to end: bytes -> digest -> pipeline_version -> record_id ----
    def pv_for(digest: str) -> str:
        tag = f"@{pack_id}.p{pack_ver}.{digest}"
        return vlm_backend.PIPELINE_VERSION + tag + cfg_tag(vs)

    c1 = _video_c1()

    def rid_for(digest: str) -> str:
        return compute_record_id(c1["chunk_id"], pv_for(digest), "")

    # the whole point: a one-byte semantic edit reaches record identity.
    assert pv_for(d_sem) != pv_for(d_base)
    assert rid_for(d_sem) != rid_for(d_base)
    # and a whitespace-only edit is inert all the way down.
    assert pv_for(d_ws) == pv_for(d_base)
    assert rid_for(d_ws) == rid_for(d_base)

    # the fork also builds a genuinely different C2 record (end to end through build_c2).
    unit = emit.caption_unit(
        ClipDesc(app="Gmail", activity="composing", description="Writing a reply.",
                 sensitive=False, raw="", parsed=True), 10.0, vs)
    c2_base = build_c2(c1, unit, pv_for(d_base), "2026-07-24T10:00:12Z")
    c2_sem = build_c2(c1, unit, pv_for(d_sem), "2026-07-24T10:00:12Z")
    assert c2_base["record_id"] != c2_sem["record_id"]
    assert schemas.validate_c2(c2_base) == [] and schemas.validate_c2(c2_sem) == []


def test_prompt_edit_does_not_rekey_the_mock_corpus(monkeypatch):
    """D-13 headless-corpus invariant, at record_id level: the mock backend's ``prompt_tag``
    is ``""`` and its dialect never references PACK_DIGEST, so editing ANY ``.prompt.md``
    (which only moves PACK_DIGEST) cannot change a mock record's ``pipeline_version`` or
    ``record_id`` — the headless fixtures are not re-keyed by a prompt edit."""
    monkeypatch.setenv("VIDEO_BACKEND", "mock")
    monkeypatch.setenv("VIDEO_PIPELINE", "clip")
    vs = get_vision_settings()
    from app.vision.clipcap import mock
    assert mock.prompt_tag(vs) == ""
    pv = mock.PIPELINE_VERSION + mock.prompt_tag(vs) + cfg_tag(vs)
    # PACK_DIGEST is the ONLY thing a prompt edit moves; it appears nowhere in the mock pv.
    assert prompts.PACK_DIGEST not in pv
    assert "@" not in pv and pv.startswith("vidclip-mock-v1#")
