"""OCR backend seam — VIDEO_OCR_BACKEND = off | mock | ppocr | vlm.

``select`` (what runs) and ``version_tag`` (the record_id dialect suffix) BOTH derive
from one private ``_resolve`` — the load-bearing invariant, transposed from
``app/audio/diarize/__init__.py``: if the two ever diverged, an OCR read would be
injected into the caption and emitted as a record under a ``pipeline_version`` that
does not name the OCR backend, and the mismatch would upsert silently over ``/context``.
Any unrecognized ``VIDEO_OCR_BACKEND`` resolves to ``off`` in BOTH resolvers (a typo can
never fork or collide), mirroring how ``app/asr`` treats an unknown backend as the safe
default.

WHY ``off`` CARRIES A NON-EMPTY FRAGMENT (the one deliberate departure from the diarize
precedent, where ``off`` maps to ``''``). ``diarize`` is a *mutate* whose enabledness IS
its fragment, so ``off`` means "the stage does not run". ``screentext`` is different: it
is a *sidecar that FEEDS the primary* — it ``provides=("ocr_text",)``, which ``clipcap``
injects (D-09), and it is enabled in EVERY clip-mode configuration (``clipcap`` hard-needs
it). By §4.3 R1 (the fork rider — "a sidecar declaring a non-empty ``provides`` must
return a non-empty fragment when enabled"), its fragment must be non-empty for every
backend value, ``off`` included. So ``off`` is an honest dialect — "OCR was configured off
for this chunk" (A-10's honest escape) — not stage-disablement. ``version_tag`` therefore
never returns ``''`` (``off`` is a key in ``_TAGS``); the coarse EP/model is baked into the
tag, and the precise model shas fork via WS-D's ``cfg_tag`` (they are in ``OUTPUT_AFFECTING``).

Heavy backends (``ppocr``/``vlm``) are LAZY-IMPORTED only inside ``select`` on their path,
so importing this package — which stage discovery does on every ``/ingest`` — never pulls
``httpx`` on the mock/off default beyond what the base image already carries.
"""
from __future__ import annotations

from .config import OcrConfig

# SINGLE SOURCE OF TRUTH for both runtime behavior (select) and the record_id dialect
# (version_tag). A backend not in this map resolves to 'off' everywhere. Every value is
# non-empty (incl. 'off'): screentext feeds the caption, so R1 forbids an empty fragment
# for any enabled configuration (see the module docstring).
_TAGS = {
    "off": "+ocr-off-v1",
    "mock": "+ocr-mock-v1",
    "ppocr": "+ocr-ppv6-cpu-v1",
    "vlm": "+ocr-vlm-v1",
}


def _resolve(cfg: OcrConfig) -> str:
    """Canonical backend name — ``'off' | 'mock' | 'ppocr' | 'vlm'``. Any unrecognized
    ``VIDEO_OCR_BACKEND`` resolves to ``'off'`` (never fork, never collide)."""
    backend = cfg.ocr_backend
    return backend if backend in _TAGS else "off"


def version_tag(cfg: OcrConfig) -> str:
    """The ``pipeline_version`` fragment for the active OCR dialect — ALWAYS non-empty
    (``off`` included), because ``screentext`` feeds the caption and R1 requires a fork
    for every enabled configuration. One pipeline_version stamps the whole chunk, so this
    fragment forks the caption (its input changed) and its own OCR record together."""
    return _TAGS[_resolve(cfg)]


def select(cfg: OcrConfig):
    """Return the resolved OCR backend module, or ``None`` when ``off``. Each backend
    exposes ``make_client(cfg) -> httpx.Client | None`` and
    ``read(cfg, frame, client, chunk_id) -> OcrRead``."""
    name = _resolve(cfg)
    if name == "mock":
        from . import mock

        return mock
    if name == "ppocr":
        from . import ppocr  # lazy: only import the httpx sidecar client on this path

        return ppocr
    if name == "vlm":
        from . import vlm  # lazy: the A/B arm's OpenAI-compatible client

        return vlm
    return None


def assert_health(cfg: OcrConfig) -> None:
    """Assert the co-located OCR sidecar's ``GET /health`` model shas match the pinned
    config, failing LOUD on mismatch — a swapped model file must fail before any corpus
    is written, not silently in it (D-06). A no-op for every backend but ``ppocr`` (the
    only one with a sha-pinned sidecar); ``vlm`` hits an OpenAI-compatible endpoint that
    carries no det/rec shas, and ``mock``/``off`` reach no network at all."""
    if _resolve(cfg) == "ppocr":
        from . import ppocr

        ppocr.assert_health(cfg)
