"""Draw the README logo and benchmark charts as SVG, in a light and a dark version.

Every number below was measured; where each comes from is in bench/README.md. After a new
benchmark run, update the numbers here and run ``uv run python assets/build.py``.
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parent
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"

THEMES = {
    "light": {"text": "#1F2328", "muted": "#59636E", "grid": "#D1D9E0", "other": "#AEB8C2",
              "other2": "#7D8894", "row": "#6D5DFC14", "empty": "#EEF1F4", "bg": "#FFFFFF"},
    "dark": {"text": "#F0F6FC", "muted": "#9198A1", "grid": "#3D444D", "other": "#4A535D",
             "other2": "#6E7781", "row": "#8B7FFF1F", "empty": "#262C33", "bg": "#0D1117"},
}  # fmt: skip
ACCENT_A, ACCENT_B = "#6D5DFC", "#22C3EE"

# 50 live-verified WebWalkerQA tasks, same answering LLM and judge for every system (2026-10-12).
SCOREBOARD = [
    # name, accuracy %, found answer page %, cost per task $, seconds per task
    ("jevcrawl", 40, 54, 0.025, 27.5),
    ("Claude picks every link", 30, 33, 0.194, 61.7),
    ("Breadth-first crawl", 8, 11, 0.228, 22.4),
]
# 20 other verified tasks, crawl only, no LLM (bench/reach.py, 2026-10-13).
VS_CRAWL4AI = [
    # metric, jevcrawl, Crawl4AI, scale max, unit, lower is better
    ("Found a page with the answer", 10, 5, 20, "/20", False),
    ("Found every page the answer needs", 5, 2, 20, "/20", False),
    ("Median crawl time", 29, 43, 43, " s", True),
    ("Sites it could not load", 0, 3, 3, "/20", True),
]
OVERLAP = {"only_jev": 6, "both": 4, "only_c4": 1, "neither": 9}


def svg(width: int, height: int, body: str, title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{title}">\n'
        f"<title>{title}</title>\n"
        f"<style>text{{font-family:{FONT}}}</style>\n"
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{ACCENT_A}"/><stop offset="1" stop-color="{ACCENT_B}"/>'
        f"</linearGradient></defs>\n{body}</svg>\n"
    )


def text(x: float, y: float, s: str, fill: str, size: int = 14, weight: int = 400,
         anchor: str = "start") -> str:  # fmt: skip
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" fill="{fill}" font-size="{size}" '
        f'font-weight="{weight}" text-anchor="{anchor}">{s}</text>\n'
    )


def rect(x: float, y: float, w: float, h: float, rx: float, fill: str, extra: str = "") -> str:
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h}" rx="{rx}" '
        f'fill="{fill}"{extra}/>\n'
    )


def bar(x: float, y: float, w: float, h: float, fill: str) -> str:
    return rect(x, y, max(w, 3), h, h / 2, fill)


def mark(x: float, y: float, size: float) -> str:
    """A crawl graph: many branches, one lit path to the answer."""
    s = size / 120
    faint = [((26, 92), (30, 58)), ((26, 92), (58, 98)), ((48, 70), (76, 82)), ((70, 48), (60, 22))]
    lit = [(26, 92), (48, 70), (70, 48), (94, 26)]
    p = f'<g transform="translate({x},{y}) scale({s:.4f})">\n'
    p += '<rect width="120" height="120" rx="28" fill="url(#g)"/>\n'
    for (x1, y1), (x2, y2) in faint:
        p += (
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#fff" stroke-opacity=".38" '
            'stroke-width="4" stroke-linecap="round"/>\n'
            f'<circle cx="{x2}" cy="{y2}" r="5" fill="#fff" fill-opacity=".45"/>\n'
        )
    points = " ".join(f"{a},{b}" for a, b in lit)
    p += (
        f'<polyline points="{points}" fill="none" stroke="#fff" stroke-width="7" '
        'stroke-linecap="round" stroke-linejoin="round"/>\n'
    )
    for a, b in lit[:-1]:
        p += f'<circle cx="{a}" cy="{b}" r="7" fill="#fff"/>\n'
    p += '<circle cx="94" cy="26" r="15" fill="none" stroke="#fff" stroke-width="4"/>\n'
    p += '<circle cx="94" cy="26" r="8" fill="#fff"/>\n</g>\n'
    return p


def logo(t: dict[str, str]) -> str:
    body = mark(8, 8, 104)
    body += (
        f'<text x="134" y="84" font-size="72" font-weight="800" letter-spacing="-2">'
        f'<tspan fill="url(#g)">jev</tspan><tspan fill="{t["text"]}">crawl</tspan></text>\n'
    )
    return svg(470, 120, body, "jevcrawl")


def scoreboard(t: dict[str, str]) -> str:
    w, row_h, top = 980, 58, 112
    h = top + row_h * len(SCOREBOARD) + 44
    cols = [
        ("Answered correctly", 1, 100.0, lambda v: f"{v}%", False),
        ("Found the answer page", 2, 100.0, lambda v: f"{v}%", False),
        ("Cost per task", 3, 0.25, lambda v: f"${v:.3f}", True),
        ("Time per task", 4, 70.0, lambda v: f"{v:.1f} s", True),
    ]
    body = text(24, 38, "50 real websites, one question each", t["text"], 22, 700)
    body += text(24, 62, "WebWalkerQA tasks verified live · same answering LLM and judge for "
                 "every system · 30-page budget", t["muted"], 14)  # fmt: skip
    x0, col_w, bar_max = 236, 186, 104
    for i, (name, _, _, _, lower) in enumerate(cols):
        cx = x0 + i * col_w
        body += text(cx, top - 16, name, t["text"], 14, 600)
        if lower:
            body += text(cx + col_w - 18, top - 16, "lower is better", t["muted"], 11, 400, "end")
    for r, row in enumerate(SCOREBOARD):
        y = top + r * row_h
        mine = r == 0
        if mine:
            body += rect(12, y, w - 24, row_h - 6, 12, t["row"])
        body += text(24, y + 32, row[0], t["text"], 16, 700 if mine else 500)
        for i, (_, k, top_v, fmt, lower) in enumerate(cols):
            values = [s[k] for s in SCOREBOARD]
            best = (min if lower else max)(values)
            v = row[k]
            cx = x0 + i * col_w
            fill = "url(#g)" if mine else t["other"]
            body += bar(cx, y + 18, bar_max * v / top_v, 16, fill)
            body += text(cx + bar_max * v / top_v + 10, y + 31, fmt(v),
                         t["text"], 15, 800 if v == best else 400)  # fmt: skip
    body += text(24, h - 18, "Bold: best in column. Cost per task includes the answering LLM's "
                 "tokens at list price; jevcrawl's own Jev calls were $0.011.",
                 t["muted"], 12)  # fmt: skip
    return svg(w, h, body, "jevcrawl benchmark scoreboard")


def cost_per_answer(t: dict[str, str]) -> str:
    rows = [(name, cost / (acc / 100)) for name, acc, _, cost, _ in SCOREBOARD]
    w, h, x0, bar_max = 980, 320, 236, 560
    body = text(24, 38, "What one correct answer costs", t["text"], 22, 700)
    body += text(24, 62, "Cost per task ÷ accuracy, same 50 tasks", t["muted"], 14)
    top_v = max(c for _, c in rows)
    for r, (name, c) in enumerate(rows):
        y = 96 + r * 58
        mine = r == 0
        if mine:
            body += rect(12, y - 4, w - 24, 50, 12, t["row"])
        body += text(24, y + 26, name, t["text"], 16, 700 if mine else 500)
        bw = bar_max * c / top_v
        body += bar(x0, y + 10, bw, 20, "url(#g)" if mine else t["other"])
        body += text(x0 + max(bw, 3) + 12, y + 26, f"${c:.2f}", t["text"], 16, 800 if mine else 400)
    jev, claude, bfs = (c for _, c in rows)
    note = (
        f"jevcrawl: {claude / jev:.0f}x less than letting Claude pick the links, "
        f"{bfs / jev:.0f}x less than a breadth-first crawl"
    )
    body += text(24, h - 24, note, t["text"], 15, 600)
    return svg(w, h, body, "Cost per correct answer")


def crawl4ai(t: dict[str, str]) -> str:
    w, h = 980, 430
    body = text(24, 38, "jevcrawl vs Crawl4AI", t["text"], 22, 700)
    body += text(24, 62, "20 WebWalkerQA tasks · both crawl toward the question without an LLM · "
                 "30-page budget", t["muted"], 14)  # fmt: skip
    x0, bar_max = 24, 330
    for m, (metric, jev, c4, top_v, unit, lower) in enumerate(VS_CRAWL4AI):
        y = 100 + m * 78
        label = metric + (" (lower is better)" if lower else "")
        body += text(x0, y, label, t["text"], 14, 600)
        for k, (name, v, fill) in enumerate(
            [("jevcrawl", jev, "url(#g)"), ("Crawl4AI", c4, t["other2"])]
        ):
            by = y + 10 + k * 22
            body += text(x0, by + 13, name, t["muted"], 13)
            bw = bar_max * v / top_v
            body += bar(x0 + 80, by + 2, bw, 14, fill)
            better = (v <= (c4 if k == 0 else jev)) if lower else (v >= (c4 if k == 0 else jev))
            body += text(x0 + 80 + max(bw, 3) + 10, by + 14, f"{v}{unit}", t["text"], 14,
                         800 if better else 400)  # fmt: skip
    # Which tasks each one reached: one square per task.
    px = 560
    body += text(px, 100, "Who found the answer page, task by task", t["text"], 14, 600)
    kinds = [
        ("only_jev", "url(#g)", None, "Only jevcrawl"),
        ("both", ACCENT_A, ".45", "Both"),
        ("only_c4", t["other2"], None, "Only Crawl4AI"),
        ("neither", t["empty"], None, "Neither"),
    ]
    cells = [(fill, op) for key, fill, op, _ in kinds for _ in range(OVERLAP[key])]
    for i, (fill, op) in enumerate(cells):
        cx, cy = px + (i % 10) * 38, 116 + (i // 10) * 38
        opacity = f' fill-opacity="{op}"' if op else ""
        body += rect(cx, cy, 32, 32, 7, fill, opacity)
    for j, (key, fill, op, label) in enumerate(kinds):
        lx, ly = px + (j % 2) * 190, 216 + (j // 2) * 30
        opacity = f' fill-opacity="{op}"' if op else ""
        body += rect(lx, ly - 13, 16, 16, 4, fill, opacity)
        body += text(lx + 24, ly, f"{label}: {OVERLAP[key]}", t["text"], 14)
    body += text(px, 296, "jevcrawl found 6 answer pages Crawl4AI missed;", t["text"], 14, 600)
    body += text(px, 318, "Crawl4AI found 1 that jevcrawl missed.", t["text"], 14, 600)
    body += text(24, h - 18, "Crawl4AI 0.9.4, BestFirstCrawlingStrategy + KeywordRelevanceScorer, "
                 "depth 3, nav/footer/aside removed. Its 3 failures were sites its browser "
                 "could not load.", t["muted"], 12)  # fmt: skip
    return svg(w, h, body, "jevcrawl vs Crawl4AI")


STEPS = [
    # title, description, uses Jev
    ("Seed", "Sitemap URLs that share words with the goal", False),
    ("Fetch", "The best 4 pages at a time, HTML only, 5 MB cap", False),
    ("Segment", "Passages with heading path and character offsets", False),
    ("Filter for free", "Boilerplate, link lists, login and cart pages, other hosts", False),
    ("Score", "One request per page: every passage, every link", True),
    ("Stop?", "Do the kept passages answer the goal?", True),
]


def wrap(s: str, width: int) -> list[str]:
    lines: list[str] = []
    for word in s.split():
        if lines and len(lines[-1]) + 1 + len(word) <= width:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines


def how_it_works(t: dict[str, str]) -> str:
    w, h, top, card_w, card_h, gap = 980, 410, 92, 144, 164, 15.2
    body = text(24, 38, "How a crawl works", t["text"], 22, 700)
    body += text(24, 62, "No LLM in the loop: Jev scores, plain code decides what to fetch "
                 "and what to keep", t["muted"], 14)  # fmt: skip
    centers = []
    for i, (title, desc, jev) in enumerate(STEPS):
        x = 20 + i * (card_w + gap)
        centers.append(x + card_w / 2)
        if jev:
            body += rect(x, top, card_w, card_h, 14, t["row"], ' stroke="url(#g)" stroke-width="2"')
            body += rect(x + card_w - 50, top + 14, 38, 20, 10, "url(#g)")
            body += text(x + card_w - 31, top + 28, "Jev", "#fff", 12, 700, "middle")
        else:
            body += rect(x, top, card_w, card_h, 14, "none", f' stroke="{t["grid"]}"')
        fill = "url(#g)" if jev else t["other2"]
        body += f'<circle cx="{x + 26}" cy="{top + 24}" r="13" fill="{fill}"/>\n'
        body += text(x + 26, top + 29, str(i + 1), "#fff", 13, 700, "middle")
        body += text(x + 14, top + 66, title, t["text"], 16, 700)
        for k, line in enumerate(wrap(desc, 19)):
            body += text(x + 14, top + 92 + k * 18, line, t["muted"], 13)
        if i < len(STEPS) - 1:  # arrow to the next step
            ax, ay = x + card_w + 3, top + card_h / 2
            body += (
                f'<path d="M{ax:.1f} {ay - 6} l7 6 l-7 6" fill="none" stroke="{t["other2"]}" '
                'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>\n'
            )
    # Not answered yet: back to fetching.
    y0, y1 = top + card_h, top + card_h + 34
    body += (
        f'<path d="M{centers[5]:.1f} {y0} V{y1} H{centers[1]:.1f} V{y0 + 8}" fill="none" '
        f'stroke="url(#g)" stroke-width="2.5" stroke-dasharray="6 5" stroke-linecap="round"/>\n'
        f'<path d="M{centers[1] - 7:.1f} {y0 + 14} l7 -8 l7 8" fill="none" stroke="{ACCENT_A}" '
        'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>\n'
    )
    mid = (centers[1] + centers[5]) / 2
    body += rect(mid - 140, y1 - 13, 280, 26, 13, t["bg"])
    body += text(mid, y1 + 5, "not answered yet: follow the next best links", t["text"], 13, 600,
                 "middle")  # fmt: skip
    # What the agent gets.
    oy = 326
    body += rect(20, oy, w - 40, 64, 14, t["row"])
    body += text(40, oy + 27, "Your agent gets", t["text"], 15, 700)
    body += text(40, oy + 48, "Verbatim passages, source URL, heading path, character offsets",
                 t["muted"], 13)  # fmt: skip
    body += (
        f'<text x="{w - 40}" y="{oy + 30}" font-size="22" font-weight="800" text-anchor="end">'
        f'<tspan fill="{t["muted"]}">80,034</tspan><tspan fill="{t["text"]}"> → </tspan>'
        f'<tspan fill="url(#g)">1,873 tokens</tspan></text>\n'
    )
    body += text(w - 40, oy + 50, "docs.python.org example · $0.0069 · 8.3 s", t["muted"], 13,
                 400, "end")  # fmt: skip
    return svg(w, h, body, "How a jevcrawl crawl works")


def main() -> None:
    for name, t in THEMES.items():
        for stem, draw in [("logo", logo), ("scoreboard", scoreboard), ("cost", cost_per_answer),
                           ("crawl4ai", crawl4ai), ("how", how_it_works)]:  # fmt: skip
            (OUT / f"{stem}-{name}.svg").write_text(draw(t), encoding="utf-8")
    (OUT / "mark.svg").write_text(svg(120, 120, mark(0, 0, 120), "jevcrawl"), encoding="utf-8")


if __name__ == "__main__":
    main()
