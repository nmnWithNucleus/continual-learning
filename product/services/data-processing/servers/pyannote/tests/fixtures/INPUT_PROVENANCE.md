# speech_two_speakers.webm — golden-smoke input provenance

`sha256 a2e29465ffc9c4df72a7571e7270fb4d304910637ef56e1770c01ba95ea0756d` · 19 693 bytes ·
webm/opus 24 kbps VBR, 16 kHz mono, 6.496 s.

Synthesized 2026-08-06 on node-7 (no captured user audio may be committed; the repo had
no real speech fixture — the committed-binary precedent is `tests/fixtures/video_scenes.mp4`):

1. piper-tts 1.6.0 (onnxruntime 1.28.0, CPU), two voices so diarization has two speakers:
   - `en_US-ryan-medium.onnx` (sha256 `abf4c274862564ed647b…`), text:
     "The quick brown fox jumps over the lazy dog."
   - `en_US-lessac-medium.onnx` (sha256 `5efe09e69902187827af…`), text:
     "Machine learning models run as long lived server processes."
2. ffmpeg 7.1: both resampled to 16 kHz mono, concatenated with 0.8 s silence between,
   encoded `-c:a libopus -b:a 24k -vbr on`.

The committed BYTES are the fixture; the generator is not re-run by tests.
