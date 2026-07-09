"""Recording service (:8084) — the learn-loop capture spine.

Carves a CONTINUOUS, always-on audio life-stream into dense, wall-clock-stamped
chunks and, blob-first, lands each chunk in storage /raw then pushes a C1 envelope
to data-processing. See CHARTER.md (mission/scope) and HANDOFF.md (working state).
"""
