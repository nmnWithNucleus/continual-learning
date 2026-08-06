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

# speech_real_dialog.webm — real-speech golden input (cleanup round 2026-08-06)

`sha256 8b1905539f9949a0627719abe17a4d82ba5e19a8deef3ca81199beb5ef00b24b` · 17.808 s ·
webm/opus 24 kbps VBR, 16 kHz mono.

Real multi-speaker speech with natural turn-taking — the Scrooge/nephew
"Bah! Humbug!" exchange (narrator + two character readers) from the LibriVox
GROUP dramatic reading of *A Christmas Carol* (Dickens), Stave 1.

- Source: https://archive.org/download/christmascarol_1104_librivox/christmascarol_1_dickens_64kb.mp3
  (archive.org item `christmascarol_1104_librivox`; source-file
  sha256 `9e6ceb0103283d23bb7a392fd4149ea088b043a1323989e4af627a017758f365`)
- License: public domain (LibriVox; archive.org license URL
  http://creativecommons.org/publicdomain/mark/1.0/)
- Cut: `ffmpeg -ss 553.90 -t 17.80 -i <source> -ac 1 -ar 16000 -c:a libopus -b:a 24k -vbr on`
  (ffmpeg 7.1, node-7). Window chosen by diarizing minutes 3–10 of the stave and
  picking the densest two-voice window (10 turns, 7 speaker changes).

The committed BYTES are the fixture; the generator is not re-run by tests.
