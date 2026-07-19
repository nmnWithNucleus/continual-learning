"""Speaker diarization backend switch — DIARIZE_BACKEND = off | mock | pyannote.

``select`` (what runs) and ``version_tag`` (the record_id dialect) BOTH derive from one
private ``_resolve`` — the load-bearing invariant of this stage. If the two ever diverged
(the stage fills ``segments[].speaker`` / ``enrichments.speakers`` but the version tag
stayed ``''``), a diarized record would be written under the *undiarized* pipeline_version
``asr-mock-v0`` and mint the SAME ``record_id`` as the pristine primary — a silent
overwrite/corruption via the idempotent ``/context`` upsert. So both read the same resolver
and any unrecognized value resolves to ``off`` in BOTH (a typo can never fork or collide),
mirroring how ``app/asr`` treats an unknown ASR_BACKEND as the safe default.

Diarization MUTATES the primary transcript record (fills speakers), so activating it
version-forks that record (via ``version_tag``); ``off`` is a pure no-op that keeps the
mock dialect byte-identical and the M0/seam test baseline green.

``pyannote`` (+ torch) is LAZY-IMPORTED only inside ``select`` on the pyannote path, so
importing this package — which the Processor registry does on every ``/ingest`` — never
pulls torch/pyannote.
"""
from __future__ import annotations

from ..config import AudioConfig

# The SINGLE SOURCE OF TRUTH for both the runtime behavior (select) and the record_id
# dialect (version_tag). A backend not in this map resolves to 'off' everywhere.
_TAGS = {"mock": "+diar-mock-v1", "pyannote": "+diar-pyannote-v1"}


def _resolve(cfg: AudioConfig) -> str:
    """Canonical backend name — ``'off' | 'mock' | 'pyannote'``. Any unrecognized
    ``DIARIZE_BACKEND`` value resolves to ``'off'`` (never fork, never collide)."""
    backend = cfg.diarize_backend
    return backend if backend in _TAGS else "off"


def version_tag(cfg: AudioConfig) -> str:
    """The audio ``pipeline_version`` suffix for the active diarization dialect
    (``''`` when off). One pipeline_version stamps the whole chunk, so this forks the
    primary AND its translation/acoustic sidecars together — intended: they all share
    the run's dialect."""
    return _TAGS.get(_resolve(cfg), "")


def select(cfg: AudioConfig):
    """Return the resolved diarize backend module, or ``None`` when off. The backend
    exposes ``diarize(audio_bytes, codec, span_seconds, cfg) -> DiarizationResult``."""
    name = _resolve(cfg)
    if name == "mock":
        from . import mock

        return mock
    if name == "pyannote":
        from . import pyannote  # lazy: pulls torch + pyannote.audio only here

        return pyannote
    return None
