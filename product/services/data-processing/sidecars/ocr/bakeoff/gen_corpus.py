#!/usr/bin/env python3
# =============================================================================
# gen_corpus.py -- deterministic synthetic macOS-UI corpus for the O-2 bake-off.
#
#   O-2 asks: can an OCR model read 13 pt macOS UI text at 1728 px through
#   CRF 28? The *real* answer needs 200 hand-labelled real macOS frames, which a
#   headless build cannot capture. This generates the best reproducible PROXY:
#   realistic app frames (dark IDE, Gmail, Slack, terminal, spreadsheet, browser
#   article) rendered at 2x Retina with BODY TEXT at ~13 pt-equivalent, so after
#   the pipeline's downscale to 1728 a body em lands at ~15 px -- the exact
#   out-of-distribution case O-2 names (sub-pixel antialiasing + x264 ringing on
#   small UI text). Ground truth is EXACT (we rendered the strings), so scoring
#   is rigorous even though the corpus is synthetic.
#
#   Output:
#     frames_src/<id>.png        one 2x source frame per corpus item
#     ground_truth.json          [{id, archetype, png, key_strings, focus_bbox_2x, focus_text}]
#
#   Determinism: content is drawn from fixed pools indexed by a per-item seed;
#   no RNG, no clock. Re-running produces byte-identical PNGs and JSON.
# =============================================================================
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(HERE, "frames_src")

# 2x Retina canvas (16:10, mac-like). Downscaled to 1728 by the pipeline.
W2X, H2X = 3456, 2160

SANS = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
SANS_B = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
MONO = "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"

_FONT_CACHE: dict = {}


def font(path: str, px: int) -> ImageFont.FreeTypeFont:
    key = (path, px)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(path, px)
    return _FONT_CACHE[key]


# Body text at 2x. ~28px @2x -> ~14px @1728: the O-2 hard case.
BODY = 28
BODY_SM = 26
CHROME = 24
H1 = 46
H2 = 34


# --------- deterministic content pools ---------------------------------------
NAMES = ["Sarah Chen", "Miguel Alvarez", "Priya Nair", "Tom Becker", "Aiko Tanaka",
         "David Osei", "Lena Vogt", "Omar Haddad", "Grace Liu", "Noah Fischer"]
SUBJECTS = ["Q3 planning deck review", "Invoice 48201 is overdue", "Re: onboarding checklist",
            "Deploy window moved to Friday", "Contract redlines attached", "Standup notes 07-24",
            "Budget approval needed", "Customer escalation ticket 9042", "Design handoff ready",
            "Security audit findings v2"]
SNIPPETS = ["Can you take a look before the sync at", "Attached the latest numbers for the",
            "Following up on the thread from yesterday about", "Quick question on the migration plan for",
            "The staging build is green, please verify", "Reminder: the review is due end of day"]
CODE_LINES = [
    "def process_chunk(ctx, blob):",
    "    frames = extract_keyframes(blob)",
    "    regions = ocr_client.read(frame.jpeg_hi)",
    "    return build_c2(chunk_id, pipeline_version)",
    "class ScreentextStage(Stage):",
    "    provides = (\"ocr_text\",)",
    "    def run(self, ctx) -> ClipFrames:",
    "        conf = region.conf >= MIN_CONF",
    "for region in sorted(regions, key=reading_order):",
    "    text = redact_secrets(region.text)",
]
FILES = ["screentext.py", "assemble.py", "redact.py", "ppocr.py", "clip_types.py", "mode.py"]
CHANNELS = ["#eng-data-processing", "#incidents", "#design-review", "#general", "#pilot-feedback"]
SLACK_MSGS = ["can someone review the OCR sidecar PR", "staging is back up after the deploy",
              "the bake-off numbers are in the handoff", "moving standup to 10:30 today",
              "who owns the redaction filter now", "shipped the mock backend, tests green"]
CELL_LABELS = ["Revenue", "Churn rate", "Active users", "Gross margin", "Support tickets", "MRR growth"]
ARTICLE_TITLES = ["Choosing a Frame Rate for Screen Capture", "Why Deterministic Records Matter",
                  "The Economics of Continuous OCR", "Notes on x264 and Small Text"]
ARTICLE_BODY = [
    "The resolution at which text is captured determines whether an OCR pass can",
    "recover it at all. A thirteen point interface glyph, rendered on a retina",
    "display and then compressed at a constant rate factor of twenty-eight, sits",
    "near the boundary of what a document-trained recognizer was ever asked to see.",
    "This article measures where that boundary falls for several open models.",
]


def pick(pool: list, seed: int, offset: int = 0):
    return pool[(seed + offset) % len(pool)]


@dataclass
class Frame:
    id: str
    archetype: str
    key_strings: list = field(default_factory=list)
    focus_bbox_2x: list = field(default_factory=lambda: [0, 0, W2X, H2X])
    focus_text: str = ""


# --------- archetype renderers ------------------------------------------------
def draw_menubar(d, dark: bool):
    bg = (34, 34, 36) if dark else (240, 240, 242)
    fg = (200, 200, 205) if dark else (60, 60, 64)
    d.rectangle([0, 0, W2X, 48], fill=bg)
    for i, item in enumerate([" Finder", "File", "Edit", "View", "Window", "Help"]):
        d.text((28 + i * 150, 12), item, fill=fg, font=font(SANS, CHROME))
    d.text((W2X - 220, 12), "100%  9:41 AM", fill=fg, font=font(SANS, CHROME))


def render_ide(seed: int, f: Frame) -> Image.Image:
    img = Image.new("RGB", (W2X, H2X), (30, 30, 32))
    d = ImageDraw.Draw(img)
    draw_menubar(d, dark=True)
    # sidebar file tree
    d.rectangle([0, 48, 520, H2X], fill=(37, 37, 40))
    for i, fn in enumerate(FILES):
        col = (170, 170, 175) if i != (seed % len(FILES)) else (255, 255, 255)
        d.text((40, 120 + i * 44), fn, fill=col, font=font(SANS, BODY_SM))
    # tab strip
    d.rectangle([520, 48, W2X, 110], fill=(45, 45, 48))
    open_file = pick(FILES, seed)
    d.text((560, 66), open_file, fill=(230, 230, 235), font=font(SANS, BODY_SM))
    # code with line numbers
    x0, y0 = 620, 150
    lines = [pick(CODE_LINES, seed, k) for k in range(10)]
    focus_lines = []
    for i, line in enumerate(lines):
        d.text((540, y0 + i * 52), str(i + 1), fill=(90, 90, 95), font=font(MONO, BODY))
        d.text((x0, y0 + i * 52), line, fill=(214, 214, 214), font=font(MONO, BODY))
        focus_lines.append(line)
    d.rectangle([0, H2X - 44, W2X, H2X], fill=(0, 122, 204))
    d.text((30, H2X - 40), f"main*   Ln {12 + seed%40}, Col 5   UTF-8   Python", fill=(255, 255, 255), font=font(SANS, CHROME))
    f.focus_bbox_2x = [x0, y0, W2X, y0 + 10 * 52]
    f.focus_text = "\n".join(focus_lines)
    f.key_strings = [open_file] + [ln.strip() for ln in lines if len(ln.strip()) >= 5][:6]
    return img


def render_gmail(seed: int, f: Frame) -> Image.Image:
    img = Image.new("RGB", (W2X, H2X), (255, 255, 255))
    d = ImageDraw.Draw(img)
    draw_menubar(d, dark=False)
    d.rectangle([0, 48, W2X, 150], fill=(248, 248, 250))
    d.text((60, 82), "Search mail", fill=(120, 120, 125), font=font(SANS, BODY))
    d.text((W2X - 260, 82), "Compose", fill=(30, 100, 200), font=font(SANS_B, BODY))
    rows = 8
    keys = []
    focus = []
    # Clean columns: sender @60, subject @820, snippet @2120 (snippet is OUTSIDE
    # the focus box, so CER measures the sender+subject "meaning" only).
    SENDER_X, SUBJ_X, SNIP_X = 60, 820, 2120
    y = 190
    for i in range(rows):
        s = seed + i
        sender = pick(NAMES, s)
        subj = pick(SUBJECTS, s, 2)
        snip = pick(SNIPPETS, s, 1) + " " + pick(["3pm", "the deck", "Friday", "sprint"], s)
        d.line([40, y - 8, W2X - 40, y - 8], fill=(235, 235, 238))
        d.text((SENDER_X, y), sender, fill=(20, 20, 22), font=font(SANS_B, BODY))
        d.text((SUBJ_X, y), subj, fill=(20, 20, 22), font=font(SANS, BODY))
        d.text((SNIP_X, y), "- " + snip, fill=(140, 140, 145), font=font(SANS, BODY_SM))
        keys += [sender, subj]
        focus.append(f"{sender} {subj}")
        y += 92
    f.focus_bbox_2x = [40, 182, SNIP_X - 60, y]   # excludes the snippet column
    f.focus_text = "\n".join(focus)
    f.key_strings = keys
    return img


def render_slack(seed: int, f: Frame) -> Image.Image:
    img = Image.new("RGB", (W2X, H2X), (26, 29, 33))
    d = ImageDraw.Draw(img)
    draw_menubar(d, dark=True)
    d.rectangle([0, 48, 560, H2X], fill=(19, 21, 24))
    for i, ch in enumerate(CHANNELS):
        col = (255, 255, 255) if i == (seed % len(CHANNELS)) else (180, 184, 190)
        d.text((44, 140 + i * 50), ch, fill=col, font=font(SANS, BODY_SM))
    chan = pick(CHANNELS, seed)
    d.text((600, 90), chan, fill=(255, 255, 255), font=font(SANS_B, H2))
    y = 200
    keys = [chan]
    focus = []
    for i in range(6):
        s = seed + i
        who = pick(NAMES, s, 3)
        msg = pick(SLACK_MSGS, s)
        d.text((600, y), who, fill=(230, 230, 235), font=font(SANS_B, BODY))
        d.text((600, y + 40), msg, fill=(200, 204, 210), font=font(SANS, BODY))
        keys += [who, msg]
        focus.append(who)
        focus.append(msg)
        y += 118
    f.focus_bbox_2x = [600, 190, W2X - 40, y]
    f.focus_text = "\n".join(focus)
    f.key_strings = keys
    return img


def render_terminal(seed: int, f: Frame) -> Image.Image:
    img = Image.new("RGB", (W2X, H2X), (18, 18, 20))
    d = ImageDraw.Draw(img)
    draw_menubar(d, dark=True)
    cmds = [
        "$ ./run.sh OCR_MODE=ppocr",
        "[ocr-sidecar] engine=ppocr ort=1.27.0 ready",
        "$ curl 127.0.0.1:8091/health",
        "{\"ok\": true, \"ep\": \"CPUExecutionProvider\"}",
        "$ pytest -q tests/test_screentext.py",
        "173 passed in 12.40s",
        "$ git commit -m \"OCR sidecar service\"",
        f"[svc/vc-ws-c {seed:04x}] OCR sidecar service",
    ]
    y = 120
    focus = []
    keys = []
    for line in cmds:
        col = (120, 220, 120) if line.startswith("$") else (210, 210, 210)
        d.text((60, y), line, fill=col, font=font(MONO, BODY))
        focus.append(line)
        if len(line) >= 5:
            keys.append(line.lstrip("$ ").strip())
        y += 54
    f.focus_bbox_2x = [40, 110, W2X - 40, y]
    f.focus_text = "\n".join(focus)
    f.key_strings = keys[:8]
    return img


def render_sheet(seed: int, f: Frame) -> Image.Image:
    img = Image.new("RGB", (W2X, H2X), (255, 255, 255))
    d = ImageDraw.Draw(img)
    draw_menubar(d, dark=False)
    cols = ["Metric", "Q1", "Q2", "Q3", "Q4"]
    cw = 560
    x0, y0 = 80, 180
    for j, c in enumerate(cols):
        d.rectangle([x0 + j * cw, y0, x0 + (j + 1) * cw, y0 + 56], fill=(240, 240, 243))
        d.text((x0 + j * cw + 20, y0 + 12), c, fill=(30, 30, 33), font=font(SANS_B, BODY))
    keys = []
    # The header row is drawn inside the focus box, so it must be part of the CER
    # reference too (else the OCR'd header inflates the sheet CER spuriously).
    focus = [" ".join(cols)]
    for i in range(8):
        s = seed + i
        label = pick(CELL_LABELS, s)
        base = 1000 + (s * 37) % 9000
        vals = [f"{base + q*113:,}" for q in range(4)]
        yy = y0 + 56 + i * 56
        d.text((x0 + 20, yy + 10), label, fill=(20, 20, 22), font=font(SANS, BODY_SM))
        for q, v in enumerate(vals):
            d.text((x0 + (q + 1) * cw + 20, yy + 10), v, fill=(20, 20, 22), font=font(SANS, BODY_SM))
        for j in range(len(cols) + 1):
            d.line([x0 + j * cw, y0, x0 + j * cw, yy + 56], fill=(230, 230, 233))
        d.line([x0, yy + 56, x0 + len(cols) * cw, yy + 56], fill=(230, 230, 233))
        keys += [label] + vals
        focus.append(label + " " + " ".join(vals))
    f.focus_bbox_2x = [x0, y0, x0 + len(cols) * cw, y0 + 56 + 8 * 56]
    f.focus_text = "\n".join(focus)
    f.key_strings = keys
    return img


def render_article(seed: int, f: Frame) -> Image.Image:
    img = Image.new("RGB", (W2X, H2X), (252, 252, 250))
    d = ImageDraw.Draw(img)
    draw_menubar(d, dark=False)
    # url bar
    d.rectangle([0, 48, W2X, 130], fill=(236, 236, 238))
    url = f"https://notes.example.com/{pick(['ocr','frames','records','x264'], seed)}"
    d.text((120, 74), url, fill=(60, 60, 64), font=font(SANS, BODY_SM))
    title = pick(ARTICLE_TITLES, seed)
    d.text((300, 220), title, fill=(15, 15, 18), font=font(SANS_B, H1))
    y = 330
    focus = []
    for line in ARTICLE_BODY:
        d.text((300, y), line, fill=(40, 40, 44), font=font(SANS, BODY))
        focus.append(line)
        y += 56
    f.focus_bbox_2x = [300, 320, W2X - 300, y]
    f.focus_text = "\n".join(focus)
    f.key_strings = [title, url] + [ln for ln in ARTICLE_BODY if len(ln) >= 5]
    return img


ARCHETYPES = {
    "ide-dark": render_ide,
    "gmail-light": render_gmail,
    "slack-dark": render_slack,
    "terminal-dark": render_terminal,
    "sheet-light": render_sheet,
    "browser-article": render_article,
}


def main(per_archetype: int = 34) -> None:
    os.makedirs(SRC_DIR, exist_ok=True)
    gt: list[dict] = []
    for name, fn in ARCHETYPES.items():
        for k in range(per_archetype):
            fid = f"{name}-{k:03d}"
            f = Frame(id=fid, archetype=name)
            img = fn(k * 7 + 3, f)  # deterministic seed per item
            png = os.path.join(SRC_DIR, f"{fid}.png")
            img.save(png)
            # de-dup + keep >=5-char key strings only
            keys = []
            seen = set()
            for s in f.key_strings:
                s = s.strip()
                if len(s) >= 5 and s.lower() not in seen:
                    seen.add(s.lower())
                    keys.append(s)
            gt.append({
                "id": fid, "archetype": name, "png": os.path.relpath(png, HERE),
                "key_strings": keys, "focus_bbox_2x": [round(v) for v in f.focus_bbox_2x],
                "focus_text": f.focus_text,
            })
    with open(os.path.join(HERE, "ground_truth.json"), "w") as fh:
        json.dump(gt, fh, indent=1)
    n_keys = sum(len(g["key_strings"]) for g in gt)
    print(f"generated {len(gt)} frames across {len(ARCHETYPES)} archetypes, "
          f"{n_keys} key strings ({n_keys/len(gt):.1f}/frame)")


if __name__ == "__main__":
    import sys
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 34)
