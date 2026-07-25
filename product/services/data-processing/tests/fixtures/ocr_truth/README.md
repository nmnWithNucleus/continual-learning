# ocr_truth fixtures (WS-C seam)

Synthetic, hand-authored OCR ground-truth for the DP-side post-processing tests. **JSON
only — no binaries** (house rule 5: headless + offline, commit no image files). Each file
is one synthetic screen layout: normalized `[x0,y0,x1,y1]` bboxes (0..1), the recognized
`text`, an engine `conf`, and the `expect_role` the bbox heuristic should assign. The tests
build `OcrRead`s from these and assert region-role assignment, the confidence / min-chars /
dedup gates, and the single-line render.

The 200 hand-labelled *real* macOS frames + the O-2 bake-off are the SIDECAR tab's (C1)
deliverable, not this seam's; they live with the sidecar, not here.
