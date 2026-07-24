# Thought process for working with OCR

The right approach is: keep your *visual* stream at ~10 fps for reconstruction, but make OCR *event‑driven* rather than “run on every frame”. Use cheap visual change detection and temporal aggregation so OCR only runs when something in a text region actually changes, and you propagate that state across intermediate frames.[^1][^2]

## Core idea: separate “video fps” from “OCR fps”

- Store/process the video at 1080p@10fps so you can always reconstruct the session visually.
- Build a second logical layer for text extraction that samples *keyframes* and *key regions* based on change detection, not a fixed 10fps clock.[^3][^2]
- Between frame 5 and 6, if the pixels in text regions are essentially identical, you just reuse the last OCR result and attach it to frame 6 as metadata (or treat it as “no text change since frame 5”).

This aligns with video-text and keyframe literature: most methods explicitly exploit temporal redundancy and only process frames or regions that differ significantly.[^2][^1]

## Step 1: detect redundant frames (cheaply)

Before OCR, do a cheap similarity check between consecutive frames (or small windows):

- Global or region-based similarity:
    - Downscale frames (e.g., 256–512 px wide), convert to gray, compute SSIM or simple normalized difference; high similarity ⇒ likely redundant.[^4][^5]
    - Alternatively, use perceptual hashes and compare Hamming distance; small distance ⇒ visually same.[^6]
- Thresholding:
    - If similarity ≥ threshold (e.g., SSIM > 0.98) and you’re not in a known “fast changing” state (video region), skip OCR for this frame and reuse prior text.[^5][^4]
    - If similarity drops below threshold, mark that frame as a keyframe for OCR.

Recent work on temporal compression modules for video tasks does essentially this—identify redundant frames via similarity metrics and drop them before the heavy model with negligible loss in recognition accuracy and ~20–25%+ compute savings.[^2]

## Step 2: work at text-region granularity, not whole frames

You can be even smarter by operating per text region instead of per frame:

1. **Text detector:**
    - Run a light text detector (or a generic layout model) on keyframes to get regions that contain text.[^1]
    - Crop those regions and track them across time (simple IoU-based tracking or optical flow).
2. **Region-level change detection:**
    - For each tracked region, compute SSIM/diff only within that crop between successive frames.
    - If a region’s visual difference is tiny, skip re-OCR and reuse previous text for that region only.
    - If a region changes (new characters, blinking caret moving to another line, etc.), re-OCR that region.

Video text benchmarks explicitly show that tracking text regions over time and only updating when content changes improves efficiency and robustness by leveraging temporal redundancy.[^1]

## Step 3: temporal aggregation of OCR outputs

Even when you *do* OCR multiple adjacent frames, you don’t need to keep all copies:

- Hash or fingerprint OCR output per region (e.g., normalized text + position, or an embedding), and store a new “state” only when the hash changes beyond some edit distance.[^7]
- Maintain a time interval for each text state: `[t_start, t_end, region_bbox, text]`.
- When reconstructing, you can interpolate that state over all intermediate frames.

This is analogous to video deduplication and keyframe clustering techniques where highly similar frames are clustered and only representative ones kept for heavy processing.[^8][^6]

## Practical pipeline at 1080p@10fps

One concrete design that will scale:

1. **Ingest:** store frames at 1080p@10fps in a compressed video format.
2. **Fast pre-pass (per frame):**
    - Downsample + compute global SSIM or diff vs previous frame; if almost identical and you know there’s no active text editing/video, tag as “redundant” and skip deeper processing.[^4][^5]
3. **Keyframe selection:**
    - Frames with significant change ⇒ keyframes for model processing.[^9][^3]
4. **Model pass on keyframes only:**
    - Run your “video understanding” model on the full frame.
    - Run text detector + OCR only on detected text regions.[^1]
5. **Region tracking \& state propagation:**
    - Track text boxes forward; for non-key frames, propagate last OCR state unless region-level change detection says otherwise.
6. **Temporal consolidation:**
    - Deduplicate OCR outputs in time by merging identical successive states; store as a change log.

You still *have* the raw 10fps stream if you ever need to visually inspect every frame, but your OCR compute scales more like “number of meaningful text changes” than “fps × session length”.

## When would you OCR every frame?

There are a few edge cases where OCR-per-frame might be justified:

- Very noisy / low-contrast text where temporal multi-frame aggregation improves accuracy (e.g., averaging predictions across adjacent frames).[^1]
- Fast-changing textual overlays (ticker-style animations, rapidly updating logs).
- Environments where it’s cheaper to batch OCR on GPU for a big chunk of frames than to maintain complex change-detection logic—though with your infra profile, you can likely afford the smarter pipeline.

Even then, you can still deduplicate outputs afterward using hashing/embedding clustering, just as large-scale video dedup solutions do at the frame level.[^10][^6]

***

So the answer to “what do we do about frames 5 and 6 being nearly identical?” is: keep the video at 10fps, but don’t naively OCR all 10 frames every second. Use similarity-based keyframe/region selection and temporal state propagation so OCR is triggered by *content change*, not by the video frame clock.[^2][^1]
<span style="display:none">[^11][^12][^13][^14][^15]</span>

<div align="center">⁂</div>

[^1]: https://vision.ucsd.edu/sites/default/files/publications/pdfs/Video Text Detection and Recognition Dataset and Benchmark.pdf

[^2]: https://www.nature.com/articles/s41598-024-61286-x

[^3]: https://www.mdpi.com/1424-8220/25/9/2677

[^4]: https://github.com/swhan0329/scene_change_detection_ssim

[^5]: https://eonr.github.io/week2/week2.html

[^6]: https://ojs.istp-press.com/jait/article/view/799/688

[^7]: https://arxiv.org/pdf/2411.04257.pdf

[^8]: https://github.com/ttharden/Keyframe-Extraction-for-video-summarization

[^9]: https://pmc.ncbi.nlm.nih.gov/articles/PMC11535061/

[^10]: https://aws.amazon.com/blogs/media/using-computer-vision-to-automate-media-content-deduplication-workflows/

[^11]: https://pmc.ncbi.nlm.nih.gov/articles/PMC12027938/

[^12]: https://www.reddit.com/r/computervision/comments/1fsy52c/keyframe_extraction_from_a_video/

[^13]: https://pmt.physicsandmathstutor.com/download/Computer-Science/A-level/Topic-Qs/OCR/1.3-Exchanging-Data/Set-A/1.3.1 Compression, Encryption and Hashing.pdf

[^14]: https://openaccess.thecvf.com/content/ICCV2023/papers/Chung_Shortcut-V2V_Compression_Framework_for_Video-to-Video_Translation_Based_on_Temporal_Redundancy_ICCV_2023_paper.pdf

[^15]: https://arxiv.org/abs/2406.11210

