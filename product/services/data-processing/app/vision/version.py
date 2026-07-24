"""The clip config tag (``cfg_tag``) and the OUTPUT_AFFECTING classification (D-13).

The clip primary's ``version_fragment`` is ``backend.PIPELINE_VERSION +
backend.prompt_tag(vs) + cfg_tag(vs)``. ``cfg_tag`` is the half every source proposal got
wrong: ``#`` + sha8 over an EXPLICIT allowlist of the config knobs that can change a clip
record's bytes. This closes the verified hole where ``VIDEO_VLM_MODEL``, ``VIDEO_OCR_STAMP``,
the token budgets and the region flag all rewrote record bytes under an unchanged
``record_id`` (a silent ``/context`` overwrite).

Two allowlists, and their union MUST be every field of ``VisionSettings`` — a new knob
cannot be added without being classified (:func:`assert_fields_classified`, asserted in the
tests). The classification is w.r.t. the CLIP dialect:

  * ``OUTPUT_AFFECTING`` — changing this changes a clip record's bytes → folded into
    ``cfg_tag`` → forks the corpus (correct, and a cost: A-15 — every one of these forks the
    day's captions when nudged).
  * ``OPERATIONAL_ONLY`` — changing this CANNOT change a clip record's bytes → excluded.
    Two sub-groups: (a) wire/host/thread knobs (``vlm_url``/``ocr_url``/timeouts/threads/…)
    — moving an endpoint with the same served model is a no-op, and forking on a DNS change
    would be a self-inflicted double-count (D-13); (b) the LEGACY keyframe-path knobs
    (``scene_threshold`` … ``ocr_records``) — the clip path never reads them, and the legacy
    dialect is a frozen ``""`` exemption (D-14) that encodes no config at all, so they fork
    no corpus anywhere.

Deliberately EXCLUDED from ``cfg_tag``: ``vlm_url`` / ``ocr_url`` (endpoint moves), and note
that a prompt-text edit forks via ``prompt_tag`` (``PACK_DIGEST``), not here — so the mock
backend, whose ``prompt_tag`` is ``""``, does not re-key on a packaged prompt edit.
"""
from __future__ import annotations

import hashlib
import json

from .config import VisionSettings

# --- knobs that CHANGE a clip record's bytes (folded into cfg_tag) --------------------
OUTPUT_AFFECTING: tuple[str, ...] = (
    "pipeline", "backend", "vlm_model",
    "clip_frame_width", "clip_seconds_per_frame", "clip_max_frames", "clip_min_frames",
    "clip_max_tokens", "chars_per_second", "caption_chars_share",
    "analysis_period_s", "pixel_threshold", "grid", "idle_peak", "layout_peak", "layout_spread",
    "ocr_backend", "ocr_ep", "ocr_model_sha_det", "ocr_model_sha_rec", "ocr_frame_width",
    "ocr_min_conf", "ocr_min_chars", "ocr_dedup_ratio", "ocr_floor_s", "ocr_max_events",
    "ocr_max_tokens", "ocr_model", "ocr_stamp", "privacy_filter", "scenario", "prompt_dir_fingerprint",
    # ocr_model — the vlm-OCR-arm served-model name. Folded in at D3 consolidation when WS-C's
    # OCR seam shim (app/vision/ocr/config.py, marked "TEMP -> VisionSettings (WS-D)") migrated
    # its VIDEO_OCR_MODEL knob home: a served-model swap changes OCR record bytes (and, injected,
    # the caption), exactly the class vlm_model is OUTPUT_AFFECTING for. '' under mock/ppocr, so it
    # forks nothing until the vlm OCR arm is used.
)

# --- knobs that CANNOT change a clip record's bytes (excluded from cfg_tag) ------------
OPERATIONAL_ONLY: tuple[str, ...] = (
    # (a) wire / host — endpoint moves; forking on a DNS/host change is a double-count (D-13).
    #     ocr_api_key folded in at D3 consolidation (WS-C shim migration): a bearer credential,
    #     exactly the class vlm_api_key is OPERATIONAL_ONLY for — it cannot change a record's bytes.
    "vlm_url", "vlm_api_key", "vlm_timeout",
    "ocr_url", "ocr_api_key", "ocr_timeout", "ocr_threads",
    # (b) client-side decode/guard knobs. Design D-13 (line 450) ratifies both as
    #     OPERATIONAL_ONLY: `structured_mode` toggles guided decoding whose SCHEMA already
    #     forks via PACK_DIGEST, and the parse ladder is built so guided/unguided replies
    #     converge to the SAME ClipDesc; `max_prompt_images_check` is only an assertion bound
    #     on K. (Tension flagged for the D3 consolidation/lead: §5.3 calls guided decoding
    #     "the primary discipline lever" that can move a WEAK model's output at the tail — an
    #     O-7-class question. Kept per the ratified allowlist; do not reclassify unilaterally.)
    "structured_mode", "max_prompt_images_check",
    # (c) legacy keyframe-path knobs — the clip path never reads them, and the legacy
    #     dialect is a frozen "" exemption (D-14), so they fork no corpus anywhere.
    "scene_threshold", "keyframe_interval_s", "max_keyframes", "min_keyframes",
    "sample_fps", "frame_max_width", "vlm_max_tokens", "ocr_records",
)


def cfg_tag(vs: VisionSettings) -> str:
    """``#`` + sha8 over the OUTPUT_AFFECTING allowlist. Deterministic (sorted keys), so
    identical output-affecting config yields an identical tag on every worker; a nudge to
    any listed knob forks the clip dialect, a nudge to an operational knob does not."""
    payload = {name: getattr(vs, name) for name in OUTPUT_AFFECTING}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "#" + hashlib.sha256(blob).hexdigest()[:8]


def unclassified_fields() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """(missing, duplicated, unknown): fields on ``VisionSettings`` classified in NEITHER
    list; fields in BOTH; names in a list that are not fields. All empty ⇔ a clean
    partition. Exposed for a precise test-failure message."""
    fields = set(VisionSettings.__dataclass_fields__)
    oa, oo = set(OUTPUT_AFFECTING), set(OPERATIONAL_ONLY)
    missing = fields - (oa | oo)
    duplicated = oa & oo
    unknown = (oa | oo) - fields
    return frozenset(missing), frozenset(duplicated), frozenset(unknown)


def assert_fields_classified() -> None:
    """Raise if ``OUTPUT_AFFECTING | OPERATIONAL_ONLY`` is not exactly
    ``VisionSettings.__dataclass_fields__`` (or the two overlap / name a non-field). This
    is the mechanism that FAILS the build until a newly added knob is classified — a knob
    left unclassified could rewrite record bytes under an unchanged record_id (D-13)."""
    missing, duplicated, unknown = unclassified_fields()
    if missing or duplicated or unknown:
        raise AssertionError(
            "VisionSettings fields are not cleanly classified in app/vision/version.py — "
            f"add each to OUTPUT_AFFECTING or OPERATIONAL_ONLY.\n"
            f"  unclassified (add to a list): {sorted(missing)}\n"
            f"  in BOTH lists (pick one):     {sorted(duplicated)}\n"
            f"  not a VisionSettings field:   {sorted(unknown)}"
        )
