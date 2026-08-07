"""DP-side OCR post-processing for the ``screentext`` stage (DP rebuild, Stage C).

What remains of the v0 OCR seam after L9 moved the engine behind the model-server
fleet: the pure post-processing pipeline the thin client runs over the ocr server's
regions — ``assemble`` (confidence gate → reading order + role → min-chars →
redaction → dedup → single-line render under the char budget) and ``redact`` (the
deterministic secret scrub, an access control not a knob).

Dead with the rebuild (v0 → v1 dispositions):

  * ``ppocr.py`` — the v0 OCR HTTP client (bespoke ``POST /ocr`` wire, per-process
    ``/health`` sha assertion). Superseded by ``servers/ocr`` + ``app/model_client.py``:
    the stage calls ``ctx.clients["ocr"].infer`` on the framework ``/infer`` envelope,
    and the det/rec sha pins moved into ``servers/manifest.json`` ``expected_identity``
    (verified by the client before a replica serves — the same guarantee, one home).
  * ``vlm.py`` — the OCR A/B arm over an OpenAI endpoint. An experiment path selected
    by env; under L4 an experiment is an in-code ``.exp-`` dialect or it does not
    exist. Rebuild it as a ``screentext`` instance with ``Backend("vlm", n)`` if the
    comparison is ever re-run.
  * ``mock.py`` — the env-selected canned backend. Mock dialects are client-level
    fakes in tests now (plan §3).
  * ``config.py`` — the ``VIDEO_OCR_*`` env shim. Every output-affecting knob is a
    code pin in ``app/stages/video/screentext.py``; the operational wire knobs
    (url/timeout) live in the server manifest.
"""
from __future__ import annotations
