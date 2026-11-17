#!/usr/bin/env python3
"""Generate the animated SVG cards used in the profile README.

Outputs (in assets/):
  chat.svg       iMessage-style intro bubbles
  languages.svg  live language dashboard (by code size and by repository)
  contributions.svg  contribution heatmap for the last year
  stats.json     raw numbers for the interactive dashboard

Data sources:
  * GitHub API (default, used by the scheduled GitHub Action). Needs GITHUB_TOKEN.
  * --local DIR  scans local clones in DIR (offline preview, no token needed).

Standard library only. No secrets are written to disk; the token is read from
the environment and only sent to api.github.com.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import math
import os
import subprocess
import urllib.request
from collections import Counter
from pathlib import Path

USER = os.environ.get("PROFILE_USER", "momacaes9")
ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

# Palette: UNC navy / Carolina blue on a dark card, so it reads in light and dark themes.
BG = "#0B1220"
CARD = "#111A2E"
LINE = "#22304D"
TEXT = "#E6EDF7"
MUTED = "#8FA3BF"
BLUE = "#4B9CD3"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace"

# Notebook JSON (outputs, images) inflates byte counts, so it is not ranked.
IGNORED = {"Jupyter Notebook"}

LANG_COLORS = {
    "Python": "#3572A5", "TypeScript": "#3178C6", "JavaScript": "#F1E05A",
    "HTML": "#E34C26", "CSS": "#663399", "SCSS": "#C6538C", "Swift": "#F05138",
    "R": "#198CE7", "Shell": "#89E051", "PLpgSQL": "#336790", "SQL": "#E38C00",
    "Dockerfile": "#384D54", "C": "#555555", "Objective-C": "#438EFF",
    "Batchfile": "#C1F12E", "Makefile": "#427819", "Rust": "#DEA584",
}

EXT_LANG = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
    ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".html": "HTML",
    ".css": "CSS", ".scss": "SCSS", ".swift": "Swift", ".r": "R", ".R": "R",
    ".sh": "Shell", ".sql": "PLpgSQL", ".ipynb": "Jupyter Notebook", ".c": "C",
    ".h": "C", ".m": "Objective-C", ".bat": "Batchfile",
}
SKIP_DIRS = {".git", "node_modules", "dist", "build", "vendor", ".venv", "venv",
             "__pycache__", ".next", "Pods", "DerivedData"}
AUTHOR_NAMES = {"Monica", "Monica Macaes", "Monica Grenadier Macaes", USER}


# --------------------------------------------------------------------------- data
def add_repo_share(share: Counter, primary: Counter, langs: dict) -> None:
    """Each repo counts once: its language mix is normalised before summing, so a
    single repo with large generated files cannot dominate the profile."""
    total = sum(langs.values())
    if not total:
        return
    for k, v in langs.items():
        share[k] += v / total
    primary[max(langs, key=langs.get)] += 1


def api(url: str, body: dict | None = None):
    token = os.environ.get("GITHUB_TOKEN", "")
    req = urllib.request.Request(
        url if url.startswith("http") else f"https://api.github.com/{url}",
        data=json.dumps(body).encode() if body else None,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "profile-stats",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def collect_api() -> dict:
    repos = [r for r in api(f"users/{USER}/repos?per_page=100&type=owner")
             if not r["fork"] and not r.get("archived")]
    by_bytes, by_repo, stars = Counter(), Counter(), 0
    for r in repos:
        stars += r["stargazers_count"]
        langs = {k: v for k, v in api(r["languages_url"]).items() if k not in IGNORED}
        add_repo_share(by_bytes, by_repo, langs)
    q = """query($u:String!){user(login:$u){contributionsCollection{
      totalCommitContributions contributionCalendar{totalContributions
      weeks{contributionDays{date contributionCount}}}}}}"""
    cal = api("graphql", {"query": q, "variables": {"u": USER}})
    cc = cal["data"]["user"]["contributionsCollection"]
    days = {d["date"]: d["contributionCount"]
            for w in cc["contributionCalendar"]["weeks"] for d in w["contributionDays"]}
    return {"by_bytes": dict(by_bytes), "by_repo": dict(by_repo), "repos": len(repos),
            "stars": stars, "commits": cc["totalCommitContributions"], "days": days}


def collect_local(folder: Path) -> dict:
    by_bytes, by_repo, days = Counter(), Counter(), Counter()
    repos = [p for p in sorted(folder.iterdir()) if (p / ".git").exists()]
    today = dt.date.today()
    start = today - dt.timedelta(days=364 + (today.weekday() + 1) % 7)
    commits = 0
    for repo in repos:
        local = Counter()
        for path in repo.rglob("*"):
            if path.is_file() and not SKIP_DIRS.intersection(path.parts):
                lang = EXT_LANG.get(path.suffix)
                if lang and lang not in IGNORED:
                    local[lang] += path.stat().st_size
        add_repo_share(by_bytes, by_repo, local)
        log = subprocess.run(["git", "-C", str(repo), "log", "--all", f"--since={start}",
                              "--format=%an|%ad", "--date=short"],
                             capture_output=True, text=True).stdout.splitlines()
        for line in log:
            name, date = line.rsplit("|", 1)
            if name in AUTHOR_NAMES:
                days[date] += 1
                commits += 1
    cal = {(start + dt.timedelta(i)).isoformat(): 0 for i in range((today - start).days + 1)}
    cal.update({d: n for d, n in days.items() if d in cal})
    return {"by_bytes": dict(by_bytes), "by_repo": dict(by_repo), "repos": len(repos),
            "stars": 0, "commits": commits, "days": cal}


# --------------------------------------------------------------------------- helpers
def esc(s: str) -> str:
    return html.escape(s, quote=True)


def text_w(s: str, size: float) -> float:
    """Rough rendered width of sans-serif text."""
    w = 0.0
    for ch in s:
        if ord(ch) > 0x2000:
            w += 1.15
        elif ch in "il.,:;'|!·":
            w += 0.30
        elif ch in "mwMW@":
            w += 0.85
        elif ch.isupper() or ch.isdigit():
            w += 0.64
        else:
            w += 0.53
    return w * size


def svg(w: int, h: int, body: str, style: str = "", title: str = "") -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}">'
            f'<title>{esc(title)}</title><style>{style}'
            f'@media (prefers-reduced-motion: reduce){{*{{animation:none!important;opacity:1!important;transform:none!important}}}}'
            f'</style>{body}</svg>\n')


# --------------------------------------------------------------------------- chat
CHAT = [
    ("in", "Hi, I'm Monica 👋"),
    ("in", "Economics + Data Science @ UNC Chapel Hill '30 · from São Paulo, Brazil"),
    ("in", "I build with data and AI: RL datasets for AI@UNC, iOS apps, maps and chatbots"),
    ("in", "Fed Challenge investment researcher · founder of OSDA · co-founder of ViaMundi"),
    ("out", "what are you working on right now?"),
    ("in", "Infinite RL dataset + oracle, MapChat, and a parking brain made of a fruit fly 🪰"),
    ("out", "how do I reach you?"),
    ("in", "momacaes@unc.edu · linkedin.com/in/monicamacaes · momacaes9.github.io"),
]


def build_chat(chat=CHAT, title="Chat introduction: Monica Macaes") -> str:
    W, size, pad, gap = 860, 15, 14, 10
    y, parts, t = 38, [], 0.4
    parts.append(f'<rect width="{W}" height="100%" rx="18" fill="{BG}"/>')
    parts.append(f'<text x="{W/2}" y="22" text-anchor="middle" font-family="{FONT}" '
                 f'font-size="11" fill="{MUTED}">iMessage · today</text>')
    for side, msg in chat:
        bw = text_w(msg, size) + pad * 2
        bh = size + 18
        x = 24 if side == "in" else W - 24 - bw
        fill = "#2A3550" if side == "in" else BLUE
        color = TEXT if side == "in" else "#FFFFFF"
        if side == "in":  # typing indicator before each incoming bubble
            parts.append(
                f'<g class="typing" style="animation-delay:{t:.2f}s">'
                f'<rect x="24" y="{y}" width="58" height="{bh}" rx="{bh/2}" fill="#2A3550"/>'
                + "".join(f'<circle cx="{41+i*12}" cy="{y+bh/2}" r="3.4" fill="{MUTED}"/>' for i in range(3))
                + "</g>")
            t += 0.5
        parts.append(
            f'<g class="msg" style="animation-delay:{t:.2f}s">'
            f'<rect x="{x:.1f}" y="{y}" width="{bw:.1f}" height="{bh}" rx="{bh/2}" fill="{fill}"/>'
            f'<text x="{x+pad:.1f}" y="{y+bh/2+size*0.36:.1f}" font-family="{FONT}" '
            f'font-size="{size}" fill="{color}">{esc(msg)}</text></g>')
        y += bh + gap
        t += 0.4
    H = y + 22
    parts.append(f'<text x="{W-24}" y="{H-12}" text-anchor="end" font-family="{FONT}" '
                 f'font-size="11" fill="{MUTED}" class="msg" style="animation-delay:{t:.2f}s">Read ✓</text>')
    style = (".msg{opacity:0;animation:pop .45s cubic-bezier(.2,.9,.3,1.3) forwards;"
             "transform-box:fill-box;transform-origin:left bottom}"
             ".typing{opacity:0;animation:typing .5s ease forwards}"
             "@keyframes pop{0%{opacity:0;transform:translateY(8px) scale(.92)}"
             "100%{opacity:1;transform:none}}"
             "@keyframes typing{0%{opacity:0}15%{opacity:1}85%{opacity:1}100%{opacity:0}}")
    return svg(W, H, "".join(parts), style, title)


# --------------------------------------------------------------------------- languages
def pct_list(counter: dict, top: int = 6):
    total = sum(counter.values()) or 1
    items = sorted(counter.items(), key=lambda kv: -kv[1])
    head, tail = items[:top], items[top:]
    out = [(k, v / total * 100) for k, v in head]
    if tail:
        out.append(("Other", sum(v for _, v in tail) / total * 100))
    return out


def donut(cx, cy, r, items, stroke=16, delay=0.3):
    circ = 2 * math.pi * r
    off = 0.0
    parts = [f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{LINE}" stroke-width="{stroke}"/>']
    for i, (lang, p) in enumerate(items):
        seg = circ * p / 100
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{LANG_COLORS.get(lang, MUTED)}" '
            f'stroke-width="{stroke}" stroke-dasharray="{seg:.2f} {circ:.2f}" '
            f'stroke-dashoffset="{-off:.2f}" transform="rotate(-90 {cx} {cy})" class="fade" '
            f'style="animation-delay:{delay+i*0.12:.2f}s"/>')
        off += seg
    return "".join(parts)


def build_languages(d: dict) -> str:
    W, H = 860, 330
    by_bytes, by_repo = pct_list(d["by_bytes"]), pct_list(d["by_repo"], top=5)
    stamp = f"live · updated {dt.date.today():%b %d, %Y}"
    p = [f'<rect width="{W}" height="{H}" rx="18" fill="{BG}"/>',
         f'<text x="28" y="40" font-family="{FONT}" font-size="20" font-weight="700" fill="{TEXT}">'
         f'Languages I code in</text>',
         f'<circle cx="{W-28-len(stamp)*6.7-10:.0f}" cy="36" r="4" fill="#3FB950" class="pulse"/>',
         f'<text x="{W-28}" y="40" text-anchor="end" font-family="{MONO}" font-size="11" fill="{MUTED}">'
         f'{stamp}</text>']
    kpis = [("public repos", d["repos"]), ("languages", len(d["by_bytes"])),
            ("commits this year", d["commits"]), ("stars", d["stars"])]
    for i, (label, val) in enumerate(kpis):
        x = 28 + i * 204
        p.append(f'<g class="fade" style="animation-delay:{i*0.1:.1f}s">'
                 f'<rect x="{x}" y="58" width="192" height="58" rx="12" fill="{CARD}" stroke="{LINE}"/>'
                 f'<text x="{x+16}" y="90" font-family="{FONT}" font-size="22" font-weight="700" fill="{BLUE}">{val}</text>'
                 f'<text x="{x+16}" y="107" font-family="{FONT}" font-size="11" fill="{MUTED}">{label}</text></g>')
    # left panel: share of code size
    p.append(f'<rect x="28" y="132" width="496" height="176" rx="12" fill="{CARD}" stroke="{LINE}"/>'
             f'<text x="44" y="156" font-family="{MONO}" font-size="11" fill="{MUTED}" letter-spacing="1">'
             f'CODE MIX · EACH REPO WEIGHTED EQUALLY</text>')
    bx, bw = 44, 464
    p.append(f'<clipPath id="bar"><rect x="{bx}" y="166" width="{bw}" height="10" rx="5"/></clipPath>'
             f'<g clip-path="url(#bar)"><rect x="{bx}" y="166" width="{bw}" height="10" fill="{LINE}"/>')
    acc = 0.0
    for i, (lang, pc) in enumerate(by_bytes):
        w = bw * pc / 100
        p.append(f'<rect x="{bx+acc:.1f}" y="166" width="{w:.1f}" height="10" '
                 f'fill="{LANG_COLORS.get(lang, MUTED)}" class="fade" style="animation-delay:{0.3+i*0.08:.2f}s"/>')
        acc += w
    p.append("</g>")
    for i, (lang, pc) in enumerate(by_bytes):
        col, row = i % 2, i // 2
        x, y = 44 + col * 236, 204 + row * 30
        c = LANG_COLORS.get(lang, MUTED)
        p.append(f'<g class="fade" style="animation-delay:{0.5+i*0.08:.2f}s">'
                 f'<circle cx="{x+5}" cy="{y-4}" r="5" fill="{c}"/>'
                 f'<text x="{x+16}" y="{y}" font-family="{FONT}" font-size="13" fill="{TEXT}">{esc(lang)}</text>'
                 f'<text x="{x+212}" y="{y}" text-anchor="end" font-family="{MONO}" font-size="12" fill="{MUTED}">{pc:.1f}%</text>'
                 f'<rect x="{x+16}" y="{y+7}" width="196" height="3" rx="1.5" fill="{LINE}"/>'
                 f'<rect x="{x+16}" y="{y+7}" width="{196*pc/100:.1f}" height="3" rx="1.5" fill="{c}" class="grow" '
                 f'style="animation-delay:{0.6+i*0.08:.2f}s"/></g>')
    # right panel: primary language per repository
    p.append(f'<rect x="536" y="132" width="296" height="176" rx="12" fill="{CARD}" stroke="{LINE}"/>'
             f'<text x="552" y="156" font-family="{MONO}" font-size="11" fill="{MUTED}" letter-spacing="1">'
             f'BY REPOSITORY</text>')
    p.append(donut(618, 232, 46, by_repo))
    if by_repo:
        p.append(f'<text x="618" y="234" text-anchor="middle" font-family="{FONT}" font-size="18" '
                 f'font-weight="700" fill="{TEXT}">{by_repo[0][1]:.0f}%</text>'
                 f'<text x="618" y="249" text-anchor="middle" font-family="{FONT}" font-size="10" '
                 f'fill="{MUTED}">{esc(by_repo[0][0])}</text>')
    for i, (lang, pc) in enumerate(by_repo):
        y = 186 + i * 22
        p.append(f'<g class="fade" style="animation-delay:{0.6+i*0.1:.2f}s">'
                 f'<rect x="690" y="{y-9}" width="10" height="10" rx="2" fill="{LANG_COLORS.get(lang, MUTED)}"/>'
                 f'<text x="706" y="{y}" font-family="{FONT}" font-size="12" fill="{TEXT}">{esc(lang)}</text>'
                 f'<text x="816" y="{y}" text-anchor="end" font-family="{MONO}" font-size="11" fill="{MUTED}">{pc:.0f}%</text></g>')
    style = (".fade{opacity:0;animation:fade .5s ease forwards}"
             ".grow{transform-box:fill-box;transform-origin:left;transform:scaleX(0);"
             "animation:grow .9s cubic-bezier(.2,.8,.2,1) forwards}"
             ".pulse{animation:pulse 2s ease-in-out infinite}"
             "@keyframes fade{to{opacity:1}}@keyframes grow{to{transform:scaleX(1)}}"
             "@keyframes pulse{50%{opacity:.25}}")
    return svg(W, H, "".join(p), style, "Language dashboard")


# --------------------------------------------------------------------------- puzzle
def edge_sign(*key) -> int:
    return 1 if hashlib.md5(repr(key).encode()).digest()[0] & 1 else -1


def piece_path(x, y, s, top, right, bottom, left) -> str:
    """Jigsaw piece outline. Edge value 0 = flat, +1 = knob out, -1 = knob in."""
    corners = [(x, y), (x + s, y), (x + s, y + s), (x, y + s)]
    out = [f"M{x:.2f},{y:.2f}"]
    for i, k in enumerate((top, right, bottom, left)):
        (x0, y0), (x1, y1) = corners[i], corners[(i + 1) % 4]
        dx, dy = (x1 - x0) / s, (y1 - y0) / s
        nx, ny = dy, -dx  # outward normal for clockwise traversal

        def P(t, u):
            return f"{x0 + dx*t*s + nx*k*u*s:.2f},{y0 + dy*t*s + ny*k*u*s:.2f}"
        if k:
            h = 0.27
            out.append(f"L{P(0.36, 0)}")
            out.append(f"C{P(0.24, h*0.55)} {P(0.30, h)} {P(0.5, h)}")
            out.append(f"C{P(0.70, h)} {P(0.76, h*0.55)} {P(0.64, 0)}")
        out.append(f"L{x1:.2f},{y1:.2f}")
    return "".join(out) + "Z"


def streaks(days: dict):
    dates = sorted(days)
    longest = run = cur = 0
    for d in dates:
        run = run + 1 if days[d] else 0
        longest = max(longest, run)
    for i, d in enumerate(reversed(dates)):
        if days[d]:
            cur += 1
        elif i:  # today may still be empty
            break
    best = max(dates, key=lambda d: days[d]) if dates else None
    return cur, longest, best


def build_puzzle(d: dict) -> str:
    days = d["days"]
    dates = sorted(days)
    first = dt.date.fromisoformat(dates[0])
    start = first - dt.timedelta(days=(first.weekday() + 1) % 7)  # Sunday
    ox, oy, W = 48, 70, 860
    grid = {}
    for ds in dates:
        idx = (dt.date.fromisoformat(ds) - start).days
        grid[(idx // 7, idx % 7)] = days[ds]
    weeks = max(w for w, _ in grid) + 1
    s = (W - ox - 28) / weeks
    H = oy + 7 * s + 96
    nz = sorted(v for v in days.values() if v)
    q = [nz[int(len(nz) * f)] if nz else 1 for f in (0.25, 0.5, 0.75)]
    colors = ["#16213A", "#1E4E79", "#2F74AD", "#4B9CD3", "#9AD0F5"]

    def level(v):
        return 0 if v == 0 else 1 + sum(v > t for t in q)

    p = [f'<rect width="{W}" height="{H}" rx="18" fill="{BG}"/>',
         f'<text x="{ox-20}" y="38" font-family="{FONT}" font-size="20" font-weight="700" fill="{TEXT}">'
         f'Contributions this year</text>']
    seen = set()
    for w in range(weeks):
        day = start + dt.timedelta(days=w * 7)
        if day.day <= 7 and (day.year, day.month) not in seen and w < weeks - 1:
            seen.add((day.year, day.month))
            p.append(f'<text x="{ox + w*s}" y="{oy-10}" font-family="{FONT}" font-size="10" '
                     f'fill="{MUTED}">{day:%b}</text>')
    for wd, lab in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        p.append(f'<text x="{ox-8}" y="{oy + wd*s + 10}" text-anchor="end" font-family="{FONT}" '
                 f'font-size="9" fill="{MUTED}">{lab}</text>')

    empty, filled = [], []
    for (w, wd), v in sorted(grid.items()):
        top = -edge_sign("v", w, wd - 1) if (w, wd - 1) in grid else 0
        bottom = edge_sign("v", w, wd) if (w, wd + 1) in grid else 0
        left = -edge_sign("h", w - 1, wd) if (w - 1, wd) in grid else 0
        right = edge_sign("h", w, wd) if (w + 1, wd) in grid else 0
        cell = f'x="{ox + w*s + 1.5:.2f}" y="{oy + wd*s + 1.5:.2f}" width="{s-3:.2f}" height="{s-3:.2f}" rx="3"'
        lv = level(v)
        if lv == 0:
            empty.append(f'<rect {cell} fill="{colors[0]}"/>')
        else:
            day = start + dt.timedelta(days=w * 7 + wd)
            filled.append(f'<rect {cell} fill="{colors[lv]}" class="pc" '
                          f'style="animation-delay:{0.3 + w*0.03 + wd*0.01:.2f}s">'
                          f'<title>{v} contribution{"s" if v != 1 else ""} on {day:%b %d}</title></rect>')
    p += empty + filled

    total = sum(days.values())
    active = sum(1 for v in days.values() if v)
    cur, longest, best = streaks(days)
    ly = oy + 7 * s + 28
    p.append(f'<text x="{ox}" y="{ly}" font-family="{FONT}" font-size="10" fill="{MUTED}">less</text>')
    for i, c in enumerate(colors):
        lx = ox + 30 + i * 15
        p.append(f'<rect x="{lx}" y="{ly-10}" width="11" height="11" rx="3" fill="{c}"/>')
    p.append(f'<text x="{ox + 30 + 5*15 + 4}" y="{ly}" font-family="{FONT}" font-size="10" fill="{MUTED}">more</text>')
    stats = [(f"{total}", "contributions"), (f"{active}", "active days"),
             (f"{cur} days", "current streak"), (f"{longest} days", "longest streak"),
             (f"{dt.date.fromisoformat(best):%b %d}" if best and days[best] else "—", "best day")]
    sw = (W - ox - 28) / len(stats)
    for i, (val, lab) in enumerate(stats):
        x = ox + i * sw
        p.append(f'<g class="fade" style="animation-delay:{1.2+i*0.12:.2f}s">'
                 f'<text x="{x:.1f}" y="{ly+40}" font-family="{FONT}" font-size="19" font-weight="700" fill="{BLUE}">{val}</text>'
                 f'<text x="{x:.1f}" y="{ly+56}" font-family="{FONT}" font-size="10" fill="{MUTED}">{lab}</text></g>')
    style = (".pc{opacity:0;transform-box:fill-box;transform-origin:center;"
             "animation:snap .4s ease-out forwards}"
             ".fade{opacity:0;animation:fade .6s ease forwards}"
             "@keyframes snap{0%{opacity:0;transform:scale(.3)}"
             "100%{opacity:1;transform:none}}@keyframes fade{to{opacity:1}}")
    return svg(int(W), int(H), "".join(p), style, "Contribution heatmap")


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", type=Path, help="scan local clones instead of the API")
    a = ap.parse_args()
    data = collect_local(a.local) if a.local else collect_api()
    ASSETS.mkdir(exist_ok=True)
    (ASSETS / "chat.svg").write_text(build_chat(), encoding="utf-8")
    (ASSETS / "languages.svg").write_text(build_languages(data), encoding="utf-8")
    (ASSETS / "contributions.svg").write_text(build_puzzle(data), encoding="utf-8")
    # Raw numbers for the interactive dashboard (momacaes9.github.io/dashboard.html)
    data["updated"] = dt.date.today().isoformat()
    (ASSETS / "stats.json").write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: v for k, v in data.items() if k != "days"}, indent=1))


if __name__ == "__main__":
    main()
