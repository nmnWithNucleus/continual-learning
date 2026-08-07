"""Audio stage files (WP-C4) — one drop-in file per stage.

REGISTERED (the v1 audio dialect): ``asr`` (whisper client, required) ·
``diarize`` -> ``diarization`` (pyannote client) · ``acoustic`` (ast client) ·
``speaker_align`` -> ``transcript`` (pure-CPU derived view).

Built but NOT registered (no contract slot yet; joining a dialect is a code
change): ``translate`` · ``injected_caption`` (dogfood replay descriptions;
index path is a constructor argument).
"""
