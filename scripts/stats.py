#!/usr/bin/env python3
"""
scripts/stats.py

Generates the four "live" stats SVGs for the Professional50coder profile
README, using ONLY the Python standard library (urllib, json, datetime, ...).
No third-party stats-card service, no pip dependency, nothing that can
rate-limit or go dark on us.

Output (all written to assets/svg/):
    hero.svg       total contributions + a weekly-aggregate sparkline
    streak.svg     current / longest contribution streaks
    languages.svg  top languages by bytes AND by repo count (public repos)
    year.svg       one character per day for the last 365 days

--------------------------------------------------------------------------
Design notes / the two determinism traps this script exists to avoid
--------------------------------------------------------------------------

TRAP #1 -- floating time windows.
    GitHub's GraphQL `contributionsCollection` defaults to "the past year
    from right now" if you omit from/to. Two workflow runs a few minutes
    apart would then bucket the same days into different week-columns and
    the sparkline/year-grid would shift pixel-by-pixel on every run, for no
    reason a human would ever care about. We instead pin the window to
    whole UTC days ourselves: `to` = today 23:59:59Z, `from` = (today - 364
    days) 00:00:00Z -- see contribution_window() below.

TRAP #2 -- privacy-dependent language totals.
    The script is meant to run under the workflow's built-in GITHUB_TOKEN,
    which can only see public repositories. If someone runs this locally
    with a personal token that also sees private repos, the language
    totals would silently disagree with what the nightly workflow produces.
    So the repository query always passes `privacy: PUBLIC` explicitly,
    regardless of what token is used to run it.

--------------------------------------------------------------------------
Why fonts are subset ahead of time, not at workflow run time
--------------------------------------------------------------------------
See assets/fonts/subset/ -- jbm-text.ttf and jbm-ramp.ttf were produced
once, locally, with fonttools' pyftsubset, and are committed as small
static files. This script only reads and base64-encodes them (stdlib
`base64`). That means the nightly workflow needs zero `pip install` step:
"no dependencies to break in CI" applies to the font pipeline too, not just
the GraphQL fetch. See the report / README notes for the full reasoning.

--------------------------------------------------------------------------
Structure
--------------------------------------------------------------------------
The GraphQL-fetch layer and the SVG-drawing layer are kept separable on
purpose:
    fetch_github_data()      -> raw dict shaped like the GraphQL response
    compute_*()               -> pure functions, no network, no I/O
    render_*_svg()            -> pure functions, no network, no I/O
This lets self_test() exercise the entire compute+render pipeline against
fabricated sample data with no GITHUB_TOKEN and no network access at all.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import svg_common  # noqa: E402

LOGIN = "Professional50coder"
WINDOW_DAYS = 364  # + the "to" day itself = 365 days total
GRAPHQL_URL = "https://api.github.com/graphql"

# Quiet -> loud character ramp used by year.svg.
#
# scripts/make_portrait.py already exists and builds its own 13-level ramp
# by evenly sampling (np.linspace) a 70-character master light->dense
# ASCII-density table (Bourke-style; see that file's
# _FULL_RAMP_DENSE_TO_LIGHT) down to 13 characters for photographic
# brightness levels: build_ramp(13) == ' ,i_{/xXQqaW$'.
#
# Reusing that exact 13-way sample here would be wrong for this graphic:
# daily contribution counts don't have anywhere near that much dynamic
# range, and several of its sampled glyphs (`_`, `x`, `q`, `,`) don't read
# as an intuitive "quiet -> loud" gradient at a glance the way the classic
# ASCII-art brightness sequence `.:+#@` does. Since a plain 5-way linspace
# sample of that same master table gives `' _xq$'` -- worse legibility for
# no benefit -- we instead hand-picked 5 characters that are still a
# genuine, correctly-ordered SUBSEQUENCE of the portrait's own master ramp
# (confirmed: in that table's light->dense order, ' ' < ':' < '+' < '#' <
# '@', at indices 0, 7, 16, 61, 68 of 69). So year.svg uses the same
# underlying density vocabulary as the portrait, just coarsened to the ~5
# meaningfully distinct buckets contribution counts actually have.
RAMP = [" ", ":", "+", "#", "@"]  # level 0 (no contributions) .. level 4 (busiest)

OUTPUT_DIR = svg_common.REPO_ROOT / "assets" / "svg"

TOP_N_LANGUAGES = 5
MAX_REPO_PAGES = 10  # safety cap: up to 1,000 public repos


# ===========================================================================
# Time window (determinism trap #1)
# ===========================================================================
def contribution_window(now: datetime | None = None) -> tuple[datetime, date, date, datetime]:
    """Return (from_dt, from_date, today, to_dt) pinned to whole UTC days.

    to   = today 23:59:59 UTC
    from = (today - WINDOW_DAYS) 00:00:00 UTC
    """
    now = now or datetime.now(timezone.utc)
    today = now.date()
    from_date = today - timedelta(days=WINDOW_DAYS)
    from_dt = datetime.combine(from_date, time(0, 0, 0), tzinfo=timezone.utc)
    to_dt = datetime.combine(today, time(23, 59, 59), tzinfo=timezone.utc)
    return from_dt, from_date, today, to_dt


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ===========================================================================
# GraphQL fetch layer
# ===========================================================================
GRAPHQL_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!, $repoCursor: String) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            weekday
            contributionCount
          }
        }
      }
    }
    repositories(
      first: 100
      after: $repoCursor
      privacy: PUBLIC
      isFork: false
      ownerAffiliations: [OWNER]
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        languages(first: 15, orderBy: {field: SIZE, direction: DESC}) {
          edges {
            size
            node { name }
          }
        }
      }
    }
  }
}
""".strip()


def _graphql(token: str, query: str, variables: dict) -> dict:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Professional50coder-profile-stats-script",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"GitHub GraphQL HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"GitHub GraphQL request failed: {e.reason}") from e
    data = json.loads(body)
    if "errors" in data and data["errors"]:
        raise RuntimeError(f"GitHub GraphQL errors: {data['errors']}")
    return data["data"]


def fetch_github_data(token: str, login: str, from_dt: datetime, to_dt: datetime) -> dict:
    """Fetch contribution calendar + all public, non-fork repo languages.

    Returns a dict: {"total_contributions": int, "weeks": [...], "repos": [...]}
    shaped so compute_* functions never need to know about GraphQL at all.
    """
    weeks = None
    total_contributions = 0
    repos: list[dict] = []
    cursor = None

    for _ in range(MAX_REPO_PAGES):
        variables = {
            "login": login,
            "from": _iso(from_dt),
            "to": _iso(to_dt),
            "repoCursor": cursor,
        }
        data = _graphql(token, GRAPHQL_QUERY, variables)
        user = data["user"]
        if user is None:
            raise RuntimeError(f"GitHub user {login!r} not found (check token scope/login).")

        if weeks is None:
            cal = user["contributionsCollection"]["contributionCalendar"]
            weeks = cal["weeks"]
            total_contributions = cal["totalContributions"]

        repo_page = user["repositories"]
        repos.extend(repo_page["nodes"])

        if not repo_page["pageInfo"]["hasNextPage"]:
            break
        cursor = repo_page["pageInfo"]["endCursor"]
    else:
        print(
            f"warning: repository list truncated at {MAX_REPO_PAGES} pages "
            f"({len(repos)} repos) -- more may exist",
            file=sys.stderr,
        )

    return {"total_contributions": total_contributions, "weeks": weeks or [], "repos": repos}


# ===========================================================================
# Compute layer (pure -- no network, no I/O; testable with fabricated data)
# ===========================================================================
def flatten_calendar(weeks: list[dict]) -> list[dict]:
    """weeks (raw GraphQL shape) -> ascending list of {"date": date, "count": int}."""
    days = []
    for week in weeks:
        for d in week["contributionDays"]:
            days.append({
                "date": datetime.strptime(d["date"], "%Y-%m-%d").date(),
                "count": d["contributionCount"],
            })
    days.sort(key=lambda d: d["date"])
    return days


def compute_streaks(days: list[dict]) -> dict:
    """Current streak (trailing run ending on the last day) + longest streak."""
    current = {"length": 0, "start": None, "end": None}
    longest = {"length": 0, "start": None, "end": None}

    if not days:
        return {"current": current, "longest": longest}

    # Longest streak: scan ascending, tracking the active run.
    run_len = 0
    run_start = None
    for d in days:
        if d["count"] > 0:
            if run_len == 0:
                run_start = d["date"]
            run_len += 1
            if run_len > longest["length"]:
                longest = {"length": run_len, "start": run_start, "end": d["date"]}
        else:
            run_len = 0
            run_start = None

    # Current streak: trailing run ending at the most recent day in the window.
    run_len = 0
    end_date = days[-1]["date"]
    for d in reversed(days):
        if d["count"] > 0:
            run_len += 1
        else:
            break
    if run_len > 0:
        current = {
            "length": run_len,
            "start": days[len(days) - run_len]["date"],
            "end": end_date,
        }

    return {"current": current, "longest": longest}


def weekly_totals(weeks: list[dict]) -> list[int]:
    """One aggregate total per GraphQL calendar week (Sun-Sat, incl. partial edge weeks)."""
    return [sum(d["contributionCount"] for d in week["contributionDays"]) for week in weeks]


def year_grid(weeks: list[dict], ramp: list[str]) -> list[list[dict]]:
    """weeks -> same shape, but each day dict also carries a "level" (0..len(ramp)-1).

    Levels are assigned by quartile of the *nonzero* counts, mirroring the
    familiar "quiet paint chip -> loud paint chip" contribution-graph feel,
    just expressed as characters instead of color swatches.
    """
    all_days = flatten_calendar(weeks)
    nonzero = [d["count"] for d in all_days if d["count"] > 0]
    max_count = max(nonzero) if nonzero else 0
    n_levels = len(ramp) - 1  # non-zero levels

    def level_for(count: int) -> int:
        if count <= 0 or max_count == 0:
            return 0
        # quartile-style bucketing into levels 1..n_levels
        frac = count / max_count
        lvl = min(n_levels, int(frac * n_levels) + 1)
        return lvl

    out = []
    for week in weeks:
        row = []
        for d in week["contributionDays"]:
            row.append({
                "date": datetime.strptime(d["date"], "%Y-%m-%d").date(),
                "weekday": d["weekday"],
                "count": d["contributionCount"],
                "level": level_for(d["contributionCount"]),
            })
        out.append(row)
    return out


def aggregate_languages(repos: list[dict], top_n: int = TOP_N_LANGUAGES) -> dict:
    """Public-repo language stats: top languages by total bytes AND by repo count."""
    bytes_by_lang: dict[str, int] = {}
    repos_by_lang: dict[str, int] = {}
    considered_repos = 0

    for repo in repos:
        edges = (repo.get("languages") or {}).get("edges", [])
        if not edges:
            continue
        considered_repos += 1
        seen_in_repo = set()
        for edge in edges:
            name = edge["node"]["name"]
            bytes_by_lang[name] = bytes_by_lang.get(name, 0) + edge["size"]
            if name not in seen_in_repo:
                repos_by_lang[name] = repos_by_lang.get(name, 0) + 1
                seen_in_repo.add(name)

    total_bytes = sum(bytes_by_lang.values())
    by_bytes = sorted(bytes_by_lang.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    by_bytes = [
        {"name": n, "value": v, "pct": (v / total_bytes * 100) if total_bytes else 0.0}
        for n, v in by_bytes
    ]

    denom = considered_repos or 1
    by_count = sorted(repos_by_lang.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    by_count = [
        {"name": n, "value": v, "pct": (v / denom * 100)}
        for n, v in by_count
    ]

    return {"by_bytes": by_bytes, "by_count": by_count, "repo_count": considered_repos}


# ===========================================================================
# Render layer (pure -- returns SVG strings; no network, no I/O)
# ===========================================================================
def render_hero_svg(total_contributions: int, weeks: list[dict],
                     from_date: date, to_date: date) -> str:
    W, H = 700, 220
    css = svg_common.style_block(
        families=[("JBMText", svg_common.FONT_TEXT_PATH)], text_family="JBMText",
    )

    totals = weekly_totals(weeks)
    max_total = max(totals) if totals else 0

    chart_x0, chart_x1 = 40, W - 40
    chart_y1 = 196
    chart_h = chart_y1 - 140

    n = len(totals)
    points = []
    for i, t in enumerate(totals):
        x = chart_x0 + (i * (chart_x1 - chart_x0) / (n - 1) if n > 1 else 0)
        y = chart_y1 - (t / max_total * chart_h if max_total else 0)
        points.append((x, y))

    line_pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area_pts = f"{chart_x0:.1f},{chart_y1:.1f} " + line_pts + f" {chart_x1:.1f},{chart_y1:.1f}"

    body = (
        f'<text x="40" y="36" font-size="15" class="muted">contributions</text>'
        f'<text x="40" y="112" font-size="64" class="fg">{svg_common.esc(f"{total_contributions:,}")}</text>'
        f'<text x="40" y="132" font-size="13" class="muted">'
        f'{svg_common.esc(from_date.strftime("%b %d, %Y"))} '
        f'- {svg_common.esc(to_date.strftime("%b %d, %Y"))} '
        f'| weekly totals below'
        f'</text>'
        f'<polygon points="{area_pts}" class="fg" fill-opacity="0.12" stroke="none"/>'
        f'<polyline points="{line_pts}" fill="none" stroke-width="1.5" class="stroke-fg"/>'
        f'<line x1="{chart_x0}" y1="{chart_y1}" x2="{chart_x1}" y2="{chart_y1}" '
        f'class="stroke-muted" stroke-width="1"/>'
    )

    return svg_common.svg_document(
        W, H, css, body,
        title=f"{total_contributions:,} contributions in the last 365 days",
    )


def render_streak_svg(streaks: dict) -> str:
    W, H = 700, 200
    css = svg_common.style_block(
        families=[("JBMText", svg_common.FONT_TEXT_PATH)], text_family="JBMText",
    )

    def fmt_range(block: dict) -> str:
        if not block["length"]:
            return "no active streak"
        s, e = block["start"], block["end"]
        if s == e:
            return s.strftime("%b %d, %Y")
        return f'{s.strftime("%b %d")} - {e.strftime("%b %d, %Y")}'

    cur, longest = streaks["current"], streaks["longest"]

    def block(x: int, label: str, data: dict) -> str:
        n = data["length"]
        return (
            f'<text x="{x}" y="36" font-size="15" class="muted">{svg_common.esc(label)}</text>'
            f'<text x="{x}" y="112" font-size="56" class="fg">{n}</text>'
            f'<text x="{x + svg_common.text_width(str(n), 56) + 10}" '
            f'y="112" font-size="20" class="muted">days</text>'
            f'<text x="{x}" y="140" font-size="13" class="muted">{fmt_range(data)}</text>'
        )

    body = (
        block(40, "current streak", cur)
        + block(370, "longest streak", longest)
        + f'<line x1="350" y1="20" x2="350" y2="160" class="stroke-muted" stroke-width="1"/>'
    )
    return svg_common.svg_document(W, H, css, body, title="Current and longest contribution streaks")


_LANG_HEADER_Y = 50  # column sub-header baseline -- kept well clear of the y=20 title
_LANG_ROW0_Y = 82    # first data row's bar/text baseline


def render_languages_svg(lang_stats: dict) -> str:
    W = 700
    rows = max(len(lang_stats["by_bytes"]), len(lang_stats["by_count"]))
    H = _LANG_ROW0_Y + rows * 30 + 20
    css = svg_common.style_block(
        families=[("JBMText", svg_common.FONT_TEXT_PATH)], text_family="JBMText",
    )

    def column(x0: int, col_w: int, title: str, items: list[dict], unit: str) -> str:
        max_val = max((it["value"] for it in items), default=1) or 1
        bar_x0 = x0
        bar_max_w = col_w - 90
        out = [f'<text x="{x0}" y="{_LANG_HEADER_Y}" font-size="15" class="muted">{svg_common.esc(title)}</text>']
        for i, it in enumerate(items):
            y = _LANG_ROW0_Y + i * 30
            bar_w = max(2, it["value"] / max_val * bar_max_w)
            pct_label = f'{it["pct"]:.1f}%'
            out.append(
                f'<text x="{x0}" y="{y - 6}" font-size="13" class="fg">{svg_common.esc(it["name"])}</text>'
                f'<rect x="{bar_x0}" y="{y}" width="{bar_w:.1f}" height="6" rx="2" '
                f'class="fg" fill-opacity="0.55"/>'
                f'<text x="{bar_x0 + bar_max_w + 10}" y="{y + 6}" font-size="12" class="muted">'
                f'{svg_common.esc(pct_label)}{svg_common.esc(unit)}</text>'
            )
        return "".join(out)

    body = (
        f'<text x="40" y="20" font-size="15" class="fg">top languages | public repos</text>'
        + column(40, 300, "by bytes", lang_stats["by_bytes"], "")
        + column(370, 300, "by repo count", lang_stats["by_count"], " of repos")
    )
    return svg_common.svg_document(W, H, css, body, title="Top languages by bytes and by repository count")


def render_year_svg(weeks_with_levels: list[list[dict]], ramp: list[str]) -> str:
    cell = 14
    margin_left, margin_top, margin_right, margin_bottom = 16, 16, 16, 16
    cols = len(weeks_with_levels)
    rows = 7
    W = margin_left + cols * cell + margin_right
    H = margin_top + rows * cell + margin_bottom

    css = svg_common.style_block(
        families=[("JBMRamp", svg_common.FONT_RAMP_PATH)], text_family="JBMRamp",
    )

    parts = []
    for col, week in enumerate(weeks_with_levels):
        for day in week:
            x = margin_left + col * cell
            y = margin_top + day["weekday"] * cell + (cell * 0.8)
            ch = ramp[day["level"]]
            if ch.strip():  # skip drawing bare spaces (nothing to render)
                parts.append(
                    f'<text x="{x:.1f}" y="{y:.1f}" font-size="13" class="fg">'
                    f'{svg_common.esc(ch)}</text>'
                )
    body = "".join(parts)
    return svg_common.svg_document(
        W, H, css, body,
        title="One character per day for the last 365 days, quiet to loud",
    )


# ===========================================================================
# Orchestration
# ===========================================================================
def generate_all(github_data: dict) -> dict[str, str]:
    """github_data -> {"hero.svg": "<svg...", ...}. Pure; no I/O."""
    weeks = github_data["weeks"]
    total = github_data["total_contributions"]
    repos = github_data["repos"]

    days = flatten_calendar(weeks)
    from_date = days[0]["date"] if days else date.today()
    to_date = days[-1]["date"] if days else date.today()

    streaks = compute_streaks(days)
    lang_stats = aggregate_languages(repos)
    grid = year_grid(weeks, RAMP)

    return {
        "hero.svg": render_hero_svg(total, weeks, from_date, to_date),
        "streak.svg": render_streak_svg(streaks),
        "languages.svg": render_languages_svg(lang_stats),
        "year.svg": render_year_svg(grid, RAMP),
    }


def write_svgs(svgs: dict[str, str], out_dir: Path = OUTPUT_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in svgs.items():
        (out_dir / name).write_text(content, encoding="utf-8", newline="\n")


# ===========================================================================
# Self-test: fabricated data, no network, no token required
# ===========================================================================
def _fabricate_github_data(seed: int = 42) -> dict:
    rng = random.Random(seed)
    _, from_date, to_date, _ = contribution_window()

    weeks: list[dict] = []
    d = from_date
    # Align to a Sunday-start week like GitHub does, by padding the first week.
    first_weekday = (d.weekday() + 1) % 7  # Python Mon=0 -> GitHub-style Sun=0
    current_week: list[dict] = []
    for pad in range(first_weekday):
        current_week.append({"date": (d - timedelta(days=first_weekday - pad)).isoformat(),
                              "weekday": pad, "contributionCount": 0})

    total = 0
    while d <= to_date:
        weekday = (d.weekday() + 1) % 7
        # bursty fake activity: mostly quiet, occasional busy streak
        count = 0
        if rng.random() < 0.6:
            count = rng.choice([0, 0, 1, 1, 2, 3, 5, 8])
        total += count
        current_week.append({"date": d.isoformat(), "weekday": weekday, "contributionCount": count})
        if weekday == 6:
            weeks.append({"contributionDays": current_week})
            current_week = []
        d += timedelta(days=1)
    if current_week:
        weeks.append({"contributionDays": current_week})

    fake_langs = ["Python", "JavaScript", "TypeScript", "HTML", "CSS", "Shell", "Go", "Rust"]
    repos = []
    for i in range(12):
        chosen = rng.sample(fake_langs, k=rng.randint(1, 4))
        edges = [{"size": rng.randint(500, 50000), "node": {"name": lang}} for lang in chosen]
        repos.append({"name": f"fake-repo-{i}", "languages": {"edges": edges}})

    return {"total_contributions": total, "weeks": weeks, "repos": repos}


def self_test() -> bool:
    print("[self-test] building fabricated sample data (no network, no token)...")
    data = _fabricate_github_data()

    print("[self-test] running compute + render pipeline...")
    svgs = generate_all(data)

    ok = True
    for name, content in svgs.items():
        try:
            ET.fromstring(content)
        except ET.ParseError as e:
            ok = False
            print(f"[self-test] FAIL {name}: not well-formed XML: {e}")
            continue
        size_kb = len(content.encode("utf-8")) / 1024
        print(f"[self-test] OK {name}: well-formed SVG, {size_kb:.1f} KiB")

    if ok:
        print("[self-test] all four SVGs are well-formed. Writing to assets/svg/ for inspection.")
        write_svgs(svgs)
    return ok


# ===========================================================================
# Entry point
# ===========================================================================
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test", action="store_true",
        help="Generate SVGs from fabricated sample data; no GITHUB_TOKEN or network needed.",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return 0 if self_test() else 1

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        if os.environ.get("GITHUB_ACTIONS"):
            # Running in CI with no token is a broken workflow, not a
            # convenience skip -- fail loudly instead of reporting green
            # while silently leaving assets/svg/ stale forever.
            print(
                "GITHUB_TOKEN is not set, but this is running in GitHub "
                "Actions (GITHUB_ACTIONS=true) -- failing instead of "
                "silently skipping the stats refresh.",
                file=sys.stderr,
            )
            return 1
        print(
            "GITHUB_TOKEN is not set -- skipping the live GraphQL fetch.\n"
            "This is expected when running locally without a token. "
            "Run `python scripts/stats.py --self-test` to validate SVG "
            "generation against fabricated sample data instead.",
            file=sys.stderr,
        )
        return 0

    from_dt, from_date, to_date, to_dt = contribution_window()
    print(f"Fetching contributions for {LOGIN} from {from_date} to {to_date} (UTC, pinned window)...")
    data = fetch_github_data(token, LOGIN, from_dt, to_dt)
    svgs = generate_all(data)
    write_svgs(svgs)
    print(f"Wrote {len(svgs)} SVGs to {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
