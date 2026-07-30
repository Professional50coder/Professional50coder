#!/usr/bin/env python3
"""
scripts/make_heading.py

Generates section-heading SVGs: a lowercase, monospace label followed by a
hairline rule running to the right edge -- e.g.

    about ------------------------------------------------------------

This is the only way to get a custom typeface into a README "heading",
since GitHub strips <style>/CSS from rendered markdown text but happily
renders an <img> of an SVG that carries its own inline <style>.

Not part of the nightly workflow: these are static, hand-picked section
labels for the README, not data that changes day to day, so they're
generated once (or whenever a label is added/edited) and committed like
any other static asset. That's also why this script is free to use
fontTools directly (see scripts/stats.py's docstring for why *that* script
stays stdlib-only instead) -- it never runs in CI.

Usage:
    python scripts/make_heading.py about stats stack
    python scripts/make_heading.py            # regenerates the defaults below

Output: assets/svg/heading-<label>.svg

Note for whoever assembles the README: image headings have no anchor
links -- GitHub's markdown TOC/outline only picks up real `#`/`##` text
headings, and it can't see text baked into a picture. That's expected,
not a bug. Give the <img> meaningful alt text (e.g. `alt="stats"`) so
screen readers still get the label.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import svg_common  # noqa: E402

DEFAULT_LABELS = ["about", "stats", "stack"]

WIDTH = 760
HEIGHT = 40
FONT_SIZE = 18
PAD_LEFT = 4
PAD_RIGHT = 4
GAP_BEFORE_RULE = 16  # px between the label's text and the start of the rule
BASELINE_Y = HEIGHT / 2 + FONT_SIZE * 0.32  # rough visual vertical centering

OUTPUT_DIR = svg_common.REPO_ROOT / "assets" / "svg"


def render_heading_svg(label: str) -> str:
    css = svg_common.style_block(
        families=[("JBMText", svg_common.FONT_TEXT_PATH)], text_family="JBMText",
    )
    label = label.lower()
    text_w = svg_common.text_width(label, FONT_SIZE)
    rule_x0 = PAD_LEFT + text_w + GAP_BEFORE_RULE
    rule_x1 = WIDTH - PAD_RIGHT

    body = (
        f'<text x="{PAD_LEFT}" y="{BASELINE_Y:.1f}" font-size="{FONT_SIZE}" '
        f'class="fg">{svg_common.esc(label)}</text>'
    )
    if rule_x1 > rule_x0:
        body += (
            f'<line x1="{rule_x0:.1f}" y1="{HEIGHT / 2:.1f}" '
            f'x2="{rule_x1:.1f}" y2="{HEIGHT / 2:.1f}" '
            f'class="stroke-muted" stroke-width="1"/>'
        )

    return svg_common.svg_document(
        WIDTH, HEIGHT, css, body, title=f"section heading: {label}",
    )


def main(argv: list[str] | None = None) -> int:
    labels = list(argv) if argv else DEFAULT_LABELS
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for label in labels:
        svg = render_heading_svg(label)
        out_path = OUTPUT_DIR / f"heading-{label.lower()}.svg"
        out_path.write_text(svg, encoding="utf-8", newline="\n")
        print(f"wrote {out_path.relative_to(svg_common.REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
