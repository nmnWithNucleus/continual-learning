# Choosing Frame Rate and Resolution for Screen Recording of Desktop Use

## Overview

This report examines how often a typical desktop screen needs to be sampled (FPS) and at what resolution to faithfully reconstruct a user’s activity over time for analysis, with a focus on "regular" computer use (coding, browsing, docs, email) rather than high‑end gaming or video production. It distinguishes between user‑driven events (clicking, typing, scrolling) and machine‑driven visual changes (video playback, animations), and synthesizes research from human–computer interaction and visual perception to suggest practical, evidence‑backed sampling regimes for different scenarios.[^1][^2][^3]

## Human Interaction and Content Change Rates

### Typing speeds and textual change

Human typing speed places a bound on how quickly text content can change on the screen.[^4][^1]

- Average composition typing speeds on computers are around 19–33 words per minute (WPM), equivalent to roughly 2–3 characters per second; fast typists may reach 60–70 WPM and very fast typists 150+ WPM (around 5–12 characters per second).[^1][^4]
- These speeds imply that meaningful new characters appear at most every 80–200 ms for typical users, and every 80–100 ms for experts.[^4][^1]

From an information standpoint, capturing a new frame every time a new character appears is excessive; a frame every 100–200 ms (5–10 fps) is sufficient to see the evolution of the text and cursor position with high fidelity for most tasks.[^1][^4]

### Clicks and discrete UI state changes

User studies on interaction in professional contexts show on the order of a few hundred mouse clicks and wheel events over roughly 10 minutes, corresponding to less than one click per second on average. Even in more intense tasks, humans rarely generate more than a small number of discrete UI state changes per second (window switches, button presses, menu selections).[^5]

Therefore, for event‑driven changes such as opening folders, switching tabs, or pressing buttons, sampling at 5–10 fps will record the before/after states of nearly every transition, assuming each transition takes on the order of hundreds of milliseconds.[^2][^5]

### Reading and scrolling speeds

Reading speed constrains how fast a user can scroll through content while still consuming it.[^4][^1]

- Typical reading speed for English prose is about 250–300 words per minute.[^1][^4]
- Proofreading on a monitor is slower, around 180 words per minute.[^1]

If a page contains, for example, 400–600 visible words, a user cannot meaningfully read more than about one to two full screens per minute if they are actually comprehending the text. In practice, users may scroll faster while skimming, but human limits still cap scrolling to a few screen heights per second before the content becomes unreadable.[^4][^1]

Screen‑recording guidance for usability testing often recommends lowering frame rate until performance is acceptable but cautioning not to go below about 5 fps, reflecting a practical lower bound to maintain recognizable motion and state transitions while users scroll and interact.[^2]

## Visual Perception and Just Noticeable Frame Rates

### Minimum frame rate for perceived motion

Research on the illusion of motion in film and animation indicates that around 10–12 fps is the absolute minimum for perceiving continuous motion rather than a slideshow. Traditional film standards converged on 24 fps as a rate where motion appears smooth enough for most viewers when natural motion blur is present.[^6][^7]

For desktop UI, where motion is relatively simple (cursor moves, windows slide), many guidelines suggest that 10–15 fps is sufficient for recognizably smooth motion in recordings, especially when the goal is analysis rather than aesthetic presentation.[^8][^2]

### Upper bounds of visual temporal resolution

Human vision does not have a fixed frame rate, but several concepts indicate that humans can detect temporal changes substantially beyond 24 fps under some conditions:[^9][^3]

- Flicker fusion threshold: under typical lighting, flickering light appears continuous above roughly 60–90 Hz; in very bright conditions, thresholds above 100 Hz have been observed.[^3][^9]
- Motion perception and high‑speed recognition: experimental work (including military visual recognition tasks) shows humans can recognize information from images flashed for about 1/200–1/220 of a second, implying sensitivity to very rapid changes, although this does not directly mandate such frame rates for everyday desktop recordings.[^3]

For standard desktop UIs, 60 fps already exceeds what is necessary for clear recognition of state changes and smooth motion; higher frame rates (120+ fps) primarily matter for fast, high‑contrast motion such as gaming and are not required for reconstructing typical office‑style workflows.[^8]

## Practical FPS Recommendations by Activity Type

### 1. Typing in editors, terminals, and forms

Characteristics:

- Changes localized to text regions and caret position.
- Human‑limited update rate on the order of tens of characters per second at most.[^4][^1]

Recommendations:

- A frame every 100–200 ms (5–10 fps) is sufficient to reconstruct what was typed, where, and when, including backspaces and cursor movements.[^1][^4]
- For fine‑grained temporal analysis (e.g., hesitation patterns, micro‑pauses), 10–15 fps may be preferred, but 60 fps is unnecessary for the underlying information content.[^4][^1]

### 2. Clicking through folders, tabs, and UI controls

Characteristics:

- Discrete state transitions with short animations (folder opens, tab content swaps, menus).[^5]
- Typical human click rates of well under a few per second across tasks.[^5]

Recommendations:

- 5–10 fps captures the pre‑click, click, and post‑click states with enough context.[^2][^5]
- For smoother visualization of short UI animations (e.g., animated tab transitions, dock magnification), 15–20 fps is usually adequate for analysis purposes, with diminishing returns above 30 fps.[^8][^2]

### 3. Scrolling documents, web pages, and code

Characteristics:

- Continuous motion controlled by scroll wheel, trackpad, or scrollbar; rate bounded by reading speed and comfort.[^1][^4]
- Human users cannot read content if it is scrolling at multiple full pages per second.

Recommendations:

- 10–15 fps generally provides enough temporal resolution to reconstruct the path through the document (which regions were visible in what order) and perceive scrolling smoothness.[^2][^8]
- For high‑fidelity visualization of smooth inertia‑based scrolling (e.g., macOS trackpad), 20–30 fps is usually sufficient; higher rates primarily improve aesthetic smoothness rather than information capture.[^8][^2]

### 4. Machine‑driven video playback and animations

Characteristics:

- Content may be rendered at 24, 30, 60 fps or higher (games, movies, UI animations).
- Here, the content itself carries high‑frequency motion information that could be relevant if one cares about the exact playback.

Recommendations:

- If the *content* is important (e.g., analyzing what the user watched in a video), capturing at or above the source frame rate is ideal; for most web and video content this means 24–30 fps.[^6][^8]
- For desktop UI animations or casual video preview, 15–30 fps is typically sufficient to understand what happened, even if some motion smoothness is lost.[^8]
- The human ability to detect flicker up to 60–90 Hz suggests that 60 fps is more than enough for any motion a user can perceive in everyday desktop use; going higher is unnecessary for information extraction unless working with specialized high‑speed stimuli.[^9][^3]

## Recommended Sampling Regimes for a 10‑Minute Session

For a 10‑minute session, recording at a constant 60 fps would yield 36,000 frames, which is excessive given human interaction speeds and typical desktop content. Research and practical guidelines suggest much lower frame rates are adequate for analysis while sharply reducing storage and processing requirements.[^6][^2][^8]

### Single fixed frame rate strategy

A simple approach is to choose one rate for all content:

- **Low‑bandwidth analytic mode:** 5 fps. Captures all major state changes, adequate for retrospective reconstruction of what happened (which windows, which documents, major scroll and click events). This aligns with usability recording advice not to drop below 5 fps.[^2]
- **Balanced mode:** 10–15 fps. Good default for mixed activities (typing, clicking, scrolling, occasional simple animations). Provides smooth enough motion and fine‑grained temporal resolution for most analytic tasks while reducing frames by a factor of 4–6 compared to 60 fps.[^2][^8]
- **High‑fidelity mode:** 24–30 fps. Reserved for sessions where video playback or subtle motion must be captured more faithfully, or where you want visually smooth recordings for human review.[^6][^8]

### Activity‑adaptive sampling

A more sophisticated strategy is to vary FPS based on detected activity:

- Idle/mostly static screen: drop to 1–2 fps, sufficient to record occasional notifications or minor changes.[^2]
- Typing or discrete UI operations (clicks, form filling, navigating menus): 8–12 fps.[^5][^1]
- Continuous scrolling: 12–20 fps, depending on desired smoothness and the importance of exact scroll dynamics.[^2]
- Video playback or complex animations: match or slightly exceed the source content, e.g., 24–30 fps for typical web/video content.[^6][^8]

This adaptive approach can dramatically cut average frame rate over a long session while preserving more detail where it matters, effectively implementing a form of temporal importance sampling.

## Resolution Considerations

For information extraction (reading text, recognizing UI elements), resolution required depends on target text size and UI scale, not on the original display resolution.

- Many screen‑recording and usability tools recommend capturing at or down‑scaling to 1080p unless extremely fine detail is needed, because 1080p reliably preserves legible text for typical desktop font sizes.[^10][^8]
- If the user’s display is higher resolution (e.g., 4K), down‑sampling the capture region to 1080p or a similar level often preserves information content while reducing storage and processing cost, as long as text remains comfortably readable in the resulting video.[^8]

For ML‑based information extraction, further down‑scaling may be possible if combined with appropriate OCR and UI‑element recognition models, but this becomes a model‑specific optimization problem rather than a purely perceptual one.

## Summary of Key Points

- Human interaction speeds (typing, clicking, scrolling) are slow relative to 60 fps; typical desktop state changes occur on the order of 100–500 ms, making 5–15 fps sufficient for reconstructing most workflows.[^5][^4][^1]
- Visual perception research suggests 10–12 fps is the bare minimum for motion perception, 24 fps yields familiar smooth motion, and 60 fps exceeds what is needed for everyday desktop recordings.[^7][^6][^8]
- Usability and screen‑recording guidance often recommends frame rates in the 5–15 fps range to balance performance with fidelity for capturing user interactions on desktop applications.[^8][^2]
- For a 10‑minute session of regular computer use, recording at 10–15 fps at a reasonable resolution (e.g., 1080p) is generally a research‑backed compromise for information extraction, avoiding the 36,000 frames produced at 60 fps while preserving key temporal and spatial information.[^8][^2]
- Activity‑adaptive sampling (lower fps when idle, higher fps during scrolling or video playback) can further optimize storage and processing while maintaining analytic utility.

---

## References

1. [Human Interaction Speeds](https://www.brainkart.com/article/Human-Interaction-Speeds_9017/) - Fast typist: 150 words per minute and higher. Average typist: 60–70 words per minute.

2. [Recording Screen Activity During Usability Testing](https://boxesandarrows.com/recording-screen-activity-during-usability-testing/) - Reduce the frame rate until either performance is acceptable or the frame rate is 5 frames per secon...

3. [How Many FPS Does the Human Eye See? The Real Science](https://valerion.com/blog/how-many-fps-does-the-human-eye-see) - While there is no absolute cap, research shows the human brain can perceive visual changes at rates ...

4. [Human Interaction Speeds](https://www.humanfactors.com/newsletters/human_interaction_speeds.asp) - The fastest typists can enter well over 150 words per minute. Many jobs require keyboard speeds of 6...

5. [Keystrokes, Mouse Clicks, and Gazing at the Computer - PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC5880755/) - by RL Street Jr · 2017 · Cited by 70 — On average, each visit had 216 (SD = 174) mouse clicks or whe...

6. [Frame rate](https://en.wikipedia.org/wiki/Frame_rate) - Frame rate, commonly expressed in frames per second (frame/s or FPS), is the frequency (rate) at whi...

7. [Frames per second, or: The Illusion of Motion](https://www.paulbakaus.com/the-illusion-of-motion/) - This rate ended up being 24 fps and remains the standard for motion pictures today. ... Human percep...

8. [Frame Rate: a Beginner's Guide](https://www.techsmith.com/blog/frame-rate-beginners-guide/) - 60+fps Anything higher than 30fps is usually reserved for recording busy scenes with lots of motion,...

9. [The Intricate Dance of Frame Rates and Human Perception](https://pixiogaming.com/blogs/articles-1/the-intricate-dance-of-frame-rates-and-human-perception-beyond-the-numbers) - Experts generally agree that the human eye perceives reality at a rate somewhere between 24 and 60 f...

10. [How to Record PC Screen in 1080p 60 FPS w/ OBS Studio 2018! (Best ...](https://www.youtube.com/watch?v=7pulzZBHaPc) - Learn how to record your PC screen (for gameplays/tutorials) with no lag at 1080p 60 FPS in OBS Stud...

