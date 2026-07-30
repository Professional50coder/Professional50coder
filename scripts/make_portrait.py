"""
make_portrait.py

Builds a self-typing ASCII-art SVG portrait for a GitHub profile README.

Pipeline:
  1. rembg cutout -> composite onto pure white background.
  2. Bilateral filter (skin smoothing, edge-preserving).
  3. CLAHE (clip limit 3.0) on the V channel for local contrast.
  4. Darkening curve (v/255)^1.7 on the V channel so facial features survive.
  5. Downsample to a character grid (90 cols) and map brightness -> ASCII ramp.
  6. Render as an SVG where each row is wiped in left-to-right via SMIL
     <animate> on a clipPath rect, staggered top-to-bottom, with a small
     "cursor" block riding the wipe edge. Everything uses fill="freeze".
  7. A JetBrains Mono subset (only the ramp's ~13 glyphs) is embedded as a
     base64 @font-face data URI so the grid's monospace advance width
     (0.600em at the font's native upm) lines up with the character grid math.

Usage:
    python make_portrait.py
"""

import base64
import io
import os
import sys

import cv2
import numpy as np
from PIL import Image
from rembg import remove, new_session

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_IMAGE = os.path.join(ROOT, "source_crop_upscaled.jpg")
FONT_PATH = os.path.join(ROOT, "assets", "fonts", "JetBrainsMono-Regular.ttf")
OUT_SVG = os.path.join(ROOT, "assets", "svg", "ascii.svg")

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
COLS = 90
ROW_ASPECT_FACTOR = 0.48          # compensates monospace char cell being ~2x taller than wide
CLAHE_CLIP_LIMIT = 3.0
CLAHE_TILE_GRID = (4, 4)   # finer tiles -> more local contrast for small facial features
DARKEN_GAMMA = 1.7
BILATERAL_D = 7
BILATERAL_SIGMA_COLOR = 40
BILATERAL_SIGMA_SPACE = 40

# Candidate glyph pool used to build the ramp. We don't trust a generic
# "print density" ordering (e.g. Paul Bourke's classic ramp) at face value,
# because it was not calibrated for JetBrains Mono specifically -- glyph ink
# coverage varies by font. Instead we render each candidate with the actual
# embedded font and measure its real ink coverage (see build_ramp), so the
# resulting ramp is a true light->dense gradient for this exact typeface.
_RAMP_CANDIDATES = " .`,-:;+~\"'^!><|/?[](){}1lIivzcxjrtfnuoYCLJZhkXaqpwUmbd#O0%Q8&BM$@W"

N_LEVELS = 13  # ~13 brightness levels as specified

FONT_ADVANCE_EM = 0.600  # JetBrains Mono advance width at its native UPM
FONT_SIZE_PX = 8.5
DISPLAY_WIDTH_PX = 460

FILL_COLOR = "#a86a2e"  # warm copper accent; ~4.4:1 on GitHub light, ~4.3:1 on GitHub dark

WIPE_DUR = 0.5
ROW_STAGGER = 0.09
CURSOR_FADE_DUR = 0.15


def _measure_ink(font_path: str, chars: str, render_px: int = 64) -> list[tuple[float, str]]:
    """Render each char with the real embedded font and measure ink coverage
    (fraction of darkened pixels). This calibrates ramp ordering/spacing to
    JetBrains Mono's actual glyph shapes instead of assuming a generic
    density ordering designed for a different typeface."""
    from PIL import ImageDraw, ImageFont

    font = ImageFont.truetype(font_path, render_px)
    box_w, box_h = render_px, int(render_px * 1.25)
    y_offset = render_px // 8

    scored = []
    for ch in chars:
        img = Image.new("L", (box_w, box_h), 255)
        draw = ImageDraw.Draw(img)
        draw.text((0, y_offset), ch, font=font, fill=0)
        arr = np.asarray(img, dtype=np.float64)
        ink = float((255.0 - arr).sum() / (255.0 * box_w * box_h))
        scored.append((ink, ch))
    return scored


def build_ramp(n_levels: int, font_path: str, candidates: str = _RAMP_CANDIDATES) -> str:
    """Pick n_levels characters evenly spaced (by rank of measured ink
    coverage) from the candidate pool, so the ramp is a real light->dense
    gradient for the font actually being embedded."""
    scored = sorted(set(_measure_ink(font_path, candidates)))
    idxs = np.linspace(0, len(scored) - 1, n_levels)
    idxs = np.round(idxs).astype(int)
    chars = [scored[i][1] for i in idxs]
    chars[0] = " "  # force lightest level to a literal space
    return "".join(chars)


RAMP = build_ramp(N_LEVELS, FONT_PATH)


# ---------------------------------------------------------------------------
# Stage 1: rembg cutout -> pure white background
# ---------------------------------------------------------------------------
def cutout_on_white(src_path: str) -> np.ndarray:
    with open(src_path, "rb") as f:
        input_bytes = f.read()

    session = new_session("u2net")
    result_bytes = remove(input_bytes, session=session)
    rgba = Image.open(io.BytesIO(result_bytes)).convert("RGBA")

    white_bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    composited = Image.alpha_composite(white_bg, rgba).convert("RGB")

    arr = np.array(composited)  # RGB
    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

    # Binary foreground mask from alpha channel, used later to re-force pure
    # white background after filtering steps that could disturb flat regions.
    alpha = np.array(rgba)[:, :, 3]
    fg_mask = alpha > 20  # generous threshold; soft edges already blended via alpha composite

    return bgr, fg_mask


# ---------------------------------------------------------------------------
# Stage 2-4: bilateral filter, CLAHE, darkening curve
# ---------------------------------------------------------------------------
def process_tone(bgr: np.ndarray, fg_mask: np.ndarray) -> np.ndarray:
    # Bilateral filter: smooth skin while preserving edges.
    smoothed = cv2.bilateralFilter(bgr, BILATERAL_D, BILATERAL_SIGMA_COLOR, BILATERAL_SIGMA_SPACE)

    hsv = cv2.cvtColor(smoothed, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)

    # CLAHE: local contrast per tile.
    clahe = cv2.createCLAHE(clipLimit=CLAHE_CLIP_LIMIT, tileGridSize=CLAHE_TILE_GRID)
    v_eq = clahe.apply(v)

    # Re-force pure white outside the subject: CLAHE is tile-local and could
    # otherwise introduce faint contrast in "flat" background tiles that
    # straddle the subject's silhouette.
    v_eq[~fg_mask] = 255

    # Darkening curve: (v/255)^1.7 on the value channel. This is what keeps
    # brows/lips/jawline visible instead of the face washing out.
    v_norm = v_eq.astype(np.float64) / 255.0
    v_dark = np.power(v_norm, DARKEN_GAMMA) * 255.0
    v_dark = np.clip(v_dark, 0, 255).astype(np.uint8)

    return v_dark


# ---------------------------------------------------------------------------
# Stage 5: downsample to character grid + map to ramp
# ---------------------------------------------------------------------------
def to_char_grid(v_dark: np.ndarray, cols: int) -> list[str]:
    h, w = v_dark.shape
    rows = max(1, round(cols * (h / w) * ROW_ASPECT_FACTOR))

    small = cv2.resize(v_dark, (cols, rows), interpolation=cv2.INTER_AREA)

    # brightness -> ramp index. v=255 (white) -> index 0 (space, lightest).
    # v=0 (black) -> index len(RAMP)-1 (densest char).
    norm = 1.0 - (small.astype(np.float64) / 255.0)
    idx = np.round(norm * (len(RAMP) - 1)).astype(int)
    idx = np.clip(idx, 0, len(RAMP) - 1)

    grid = []
    for r in range(rows):
        line = "".join(RAMP[i] for i in idx[r])
        grid.append(line.rstrip())  # trailing spaces render nothing; trim for size
    return grid


# ---------------------------------------------------------------------------
# Stage 6b: font subsetting
# ---------------------------------------------------------------------------
def build_font_subset_b64(font_path: str, ramp: str) -> tuple[str, int]:
    from fontTools import subset as ft_subset

    tmp_out = os.path.join(ROOT, "assets", "fonts", "_subset_tmp.woff")

    args = [
        font_path,
        f"--output-file={tmp_out}",
        f"--text={ramp}",
        "--flavor=woff",
        "--no-notdef-outline",
        "--no-glyph-names",
        "--layout-features=",
        "--drop-tables+=DSIG",
    ]
    try:
        ft_subset.main(args)
        with open(tmp_out, "rb") as f:
            subset_bytes = f.read()
    finally:
        if os.path.exists(tmp_out):
            os.remove(tmp_out)

    b64 = base64.b64encode(subset_bytes).decode("ascii")
    return b64, len(subset_bytes)


# ---------------------------------------------------------------------------
# Stage 7: SVG assembly with SMIL typing animation
# ---------------------------------------------------------------------------
def xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def build_svg(grid: list[str], font_b64: str) -> str:
    char_w = FONT_SIZE_PX * FONT_ADVANCE_EM
    line_h = char_w / ROW_ASPECT_FACTOR

    cols = max(len(line) for line in grid) if grid else COLS
    rows = len(grid)

    natural_w = cols * char_w
    natural_h = rows * line_h
    display_h = DISPLAY_WIDTH_PX * (natural_h / natural_w)

    baseline_offset = line_h * 0.78

    defs_clip_paths = []
    content_groups = []

    for i, line in enumerate(grid):
        content_len = len(line)
        full_w = max(content_len * char_w, char_w)  # never zero-width
        y_top = i * line_h
        begin = round(i * ROW_STAGGER, 3)
        cursor_fade_begin = round(begin + WIPE_DUR, 3)

        clip_id = f"c{i}"
        defs_clip_paths.append(
            f'<clipPath id="{clip_id}">'
            f'<rect x="0" y="{y_top:.2f}" width="0" height="{line_h:.2f}">'
            f'<animate attributeName="width" from="0" to="{full_w:.2f}" '
            f'dur="{WIPE_DUR}s" begin="{begin}s" fill="freeze"/>'
            f"</rect></clipPath>"
        )

        escaped = xml_escape(line) if line.strip() else ""
        text_el = ""
        if escaped:
            baseline = y_top + baseline_offset
            text_el = (
                f'<g clip-path="url(#{clip_id})">'
                f'<text x="0" y="{baseline:.2f}" xml:space="preserve">{escaped}</text>'
                f"</g>"
            )

        cursor_el = (
            f'<rect class="cursor" x="0" y="{y_top:.2f}" '
            f'width="{char_w:.2f}" height="{line_h:.2f}" fill="{FILL_COLOR}" opacity="0">'
            f'<set attributeName="opacity" to="1" begin="{begin}s"/>'
            f'<animate attributeName="x" from="0" to="{full_w:.2f}" '
            f'dur="{WIPE_DUR}s" begin="{begin}s" fill="freeze"/>'
            f'<animate attributeName="opacity" from="1" to="0" '
            f'dur="{CURSOR_FADE_DUR}s" begin="{cursor_fade_begin}s" fill="freeze"/>'
            f"</rect>"
        )

        content_groups.append(text_el + cursor_el)

    style = f"""
    @font-face {{
      font-family: "JBMSubset";
      src: url(data:font/woff;charset=utf-8;base64,{font_b64}) format("woff");
      font-weight: 400;
      font-style: normal;
    }}
    text {{
      font-family: "JBMSubset", monospace;
      font-size: {FONT_SIZE_PX}px;
      fill: {FILL_COLOR};
      white-space: pre;
    }}
    .cursor {{ }}
    """.strip()

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {natural_w:.2f} {natural_h:.2f}" '
        f'width="{DISPLAY_WIDTH_PX}" height="{display_h:.2f}" '
        f'xml:space="preserve">'
        f"<defs><style>{style}</style>{''.join(defs_clip_paths)}</defs>"
        f"{''.join(content_groups)}"
        f"</svg>"
    )
    return svg


def main():
    print(f"Ramp ({len(RAMP)} levels): {RAMP!r}")

    bgr, fg_mask = cutout_on_white(SRC_IMAGE)
    v_dark = process_tone(bgr, fg_mask)
    grid = to_char_grid(v_dark, COLS)

    print(f"\nGrid: {len(grid)} rows x up to {COLS} cols\n")
    for line in grid:
        print(line)

    font_b64, subset_size = build_font_subset_b64(FONT_PATH, RAMP)
    print(f"\nFont subset size: {subset_size} bytes")

    svg = build_svg(grid, font_b64)

    os.makedirs(os.path.dirname(OUT_SVG), exist_ok=True)
    with open(OUT_SVG, "w", encoding="utf-8") as f:
        f.write(svg)

    out_size = os.path.getsize(OUT_SVG)
    print(f"\nWrote {OUT_SVG} ({out_size} bytes)")


if __name__ == "__main__":
    main()
