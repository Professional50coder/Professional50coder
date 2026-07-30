#!/usr/bin/env python3
"""
scripts/make_impact.py

Generates assets/svg/impact.svg -- a small "impact" panel (LinkedIn reach,
Arcade mentorship completion rate vs. the overall average, top posts by
impressions, hackathon podium count) in the same one-color, embedded-font
visual language as the rest of the README's graphics.

Unlike scripts/stats.py, this is NOT wired into the nightly GitHub Actions
workflow: there is no public LinkedIn API to poll, so these numbers are a
manually-updated snapshot, not a live feed. The panel says so explicitly
(a dated caption) rather than quietly implying it's as fresh as the GitHub
stats above it -- see the accessibility/optimization review notes earlier
in this repo's history for why silent staleness is worse than an honest
timestamp.

Source: linkedin.com/in/hitanshgopani, read directly, 2026-07-30.
Re-run this script by hand (with updated SNAPSHOT_* constants below) the
next time these numbers are worth refreshing.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import svg_common  # noqa: E402

OUTPUT_DIR = svg_common.REPO_ROOT / "assets" / "svg"
SNAPSHOT_DATE = "Jul 2026"

SNAPSHOT_FOLLOWERS = 1581
SNAPSHOT_CONNECTIONS = "500+"

ARCADE_GROUP_PCT = 94
ARCADE_OVERALL_PCT = 22

TOP_POSTS = [
    ("AWS Summit Mumbai 2025", 7255),
    ("AI/ML Engineer @ Pitch Perfekt Collective", 5505),
    ("December recap: QUASTECH, Avishkar, GitHub Universe", 4719),
    ("1st Runner-Up, Code Odyssey 4.0", 3059),
    ("Global Fintech Fest 2025", 2210),
]

HACKATHON_COUNT = 2
HACKATHON_DETAIL = "Zonal Winner (Avishkar)  +  1st Runner-Up (Code Odyssey 4.0)"


def render_impact_svg() -> str:
    W = 700
    css = svg_common.style_block(
        families=[("JBMText", svg_common.FONT_TEXT_PATH)], text_family="JBMText",
    )
    parts = []

    # --- Panel 1: reach -----------------------------------------------
    def stat_block(x: int, label: str, value: str) -> str:
        return (
            f'<text x="{x}" y="36" font-size="15" class="muted">{svg_common.esc(label)}</text>'
            f'<text x="{x}" y="96" font-size="48" class="fg">{svg_common.esc(value)}</text>'
        )

    parts.append(stat_block(40, "followers", f"{SNAPSHOT_FOLLOWERS:,}"))
    parts.append(stat_block(370, "connections", SNAPSHOT_CONNECTIONS))
    parts.append(
        f'<line x1="350" y1="12" x2="350" y2="100" class="stroke-muted" stroke-width="1"/>'
        f'<text x="40" y="120" font-size="12" class="muted">LinkedIn | snapshot {SNAPSHOT_DATE}</text>'
    )
    y = 160

    # --- Panel 2: Arcade mentee completion vs overall ------------------
    parts.append(
        f'<text x="40" y="{y}" font-size="15" class="muted">'
        f"arcade 2025 mentee completion rate vs. overall average</text>"
    )
    y += 34
    bar_max_w = 620

    def compare_bar(y0: int, label: str, pct: int, muted: bool) -> str:
        bar_w = max(2, pct / 100 * bar_max_w)
        cls = "muted" if muted else "fg"
        return (
            f'<text x="40" y="{y0 - 6}" font-size="13" class="fg">{svg_common.esc(label)}</text>'
            f'<rect x="40" y="{y0}" width="{bar_w:.1f}" height="10" rx="3" '
            f'class="{cls}" fill-opacity="{"0.35" if muted else "0.55"}"/>'
            f'<text x="{40 + bar_w + 10:.1f}" y="{y0 + 9}" font-size="13" class="fg">{pct}%</text>'
        )

    parts.append(compare_bar(y, "mentee group (Hitansh + Sambhav)", ARCADE_GROUP_PCT, muted=False))
    y += 34
    parts.append(compare_bar(y, "overall Arcade average", ARCADE_OVERALL_PCT, muted=True))
    y += 50

    # --- Panel 3: top posts by impressions -----------------------------
    parts.append(f'<text x="40" y="{y}" font-size="15" class="muted">top posts by reach (impressions)</text>')
    y += 30
    max_impressions = max(v for _, v in TOP_POSTS)
    bar_max_w2 = 460
    for name, impressions in TOP_POSTS:
        bar_w = max(2, impressions / max_impressions * bar_max_w2)
        parts.append(
            f'<text x="40" y="{y - 6}" font-size="13" class="fg">{svg_common.esc(name)}</text>'
            f'<rect x="40" y="{y}" width="{bar_w:.1f}" height="6" rx="2" class="fg" fill-opacity="0.55"/>'
            f'<text x="{40 + bar_max_w2 + 10}" y="{y + 6}" font-size="12" class="muted">{impressions:,}</text>'
        )
        y += 30
    y += 20

    # --- Panel 4: hackathon podium count --------------------------------
    parts.append(
        f'<text x="40" y="{y}" font-size="15" class="muted">hackathon podium finishes</text>'
    )
    y += 60
    parts.append(f'<text x="40" y="{y}" font-size="48" class="fg">{HACKATHON_COUNT}</text>')
    parts.append(
        f'<text x="{40 + svg_common.text_width(str(HACKATHON_COUNT), 48) + 14:.1f}" '
        f'y="{y}" font-size="13" class="muted">{svg_common.esc(HACKATHON_DETAIL)}</text>'
    )
    y += 30

    H = y + 20
    body = "".join(parts)
    return svg_common.svg_document(W, H, css, body, title="Impact snapshot: reach, mentorship results, and podium finishes")


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    svg = render_impact_svg()
    out_path = OUTPUT_DIR / "impact.svg"
    out_path.write_text(svg, encoding="utf-8", newline="\n")
    print(f"wrote {out_path.relative_to(svg_common.REPO_ROOT)} ({len(svg.encode('utf-8'))} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
