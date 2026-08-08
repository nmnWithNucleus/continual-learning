# ocr_truth fixtures

Synthetic, hand-authored OCR ground-truth for the DP-side post-processing tests. **JSON
only — no binaries** (headless, offline, and no image files committed). Each file
is one synthetic screen layout: normalized `[x0,y0,x1,y1]` bboxes (0..1), the recognized
`text`, an engine `conf`, and the `expect_role` the bbox heuristic should assign. The tests
build `OcrRead`s from these and assert region-role assignment, the confidence / min-chars /
dedup gates, and the single-line render.

Hand-labelled *real* macOS frames belong to the recording/capture side, not to this
seam: these fixtures exist to pin post-processing, not engine accuracy.
