"""DP-side OCR post-processing for the ``screentext`` stage.

The OCR engine itself lives behind the model-server fleet (L9); what belongs to DP is
the pure post-processing pipeline the thin client runs over the ocr server's
regions — ``assemble`` (confidence gate → reading order + role → min-chars →
redaction → dedup → single-line render under the char budget) and ``redact`` (the
deterministic secret scrub, an access control not a knob).

What this package deliberately does NOT hold, and where each thing lives instead:

  * No OCR HTTP client. The stage calls ``ctx.clients["ocr"].infer`` on the framework
    ``/infer`` envelope (``app/model_client.py``), and the det/rec sha pins live in
    ``servers/manifest.json`` ``expected_identity``, verified by the client before a
    replica serves — one home for the guarantee.
  * No alternate OCR backend. An experiment is an in-code ``.exp-`` dialect or it does
    not exist (L4); a VLM-based comparison arm would be a ``screentext`` instance with
    ``Backend("vlm", n)``.
  * No canned backend module. Mock dialects are client-level fakes in tests.
  * No env shim. Every output-affecting value is a code pin in
    ``app/stages/video/screentext.py``; the operational wire settings (url/timeout)
    live in the server manifest.
"""
from __future__ import annotations
