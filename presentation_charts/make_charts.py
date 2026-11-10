"""Presentation charts for the speech hawk/dove index, one folder per bank (MacroMicro house style).

    fed_speech_index/.venv/bin/python presentation_charts/make_charts.py

Reads only the repo's existing outputs/data; writes PNGs to presentation_charts/{fed,boe,boj}/:
  1_scored_example.png   how one speech becomes a score (latest scored speech)
  2_index_and_decisions.png  index with tightening/easing cycles shaded, policy rate below
  3_member_heatmap.png   every member's score over time
  4_stance_groups.png    how many voters lean to ease / hold / lean to tighten
  5_disagreement.png     index with disagreement below
  6_coverage.png         scored speeches per month
"""
from __future__ import annotations

import ast
import bisect
import csv
import datetime as dt
import json
import sys
import textwrap
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.image as mpimg  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

csv.field_size_limit(sys.maxsize)
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "fed_speech_index"))
from rubric import LEVELS  # noqa: E402

# ---- MacroMicro style -------------------------------------------------------------------------------
BLUE, NAVY, BROWN = "#3bafda", "#3b3f4f", "#7d5b4f"
HAWK, DOVE, HOLD = "#e9573f", "#3bafda", "#c9c9c9"     # tighten / ease / hold
TEXT, MUTED, GRID, BASE = "#333333", "#666666", "#e6e6e6", "#d8d8d8"
TIGHT_BAND, EASE_BAND = "#fbe3de", "#dff1f8"
DIVERGING = LinearSegmentedColormap.from_list("dove_hawk", ["#1f7fa8", DOVE, "#ececec", HAWK, "#b8341f"])
W, H, DPI = 9.6, 5.4, 200                                # 960x540 at 2x
plt.rcParams.update({
    "font.family": ["Lato", "Hiragino Sans", "Arial Unicode MS", "DejaVu Sans"],
    "font.size": 10, "text.color": TEXT, "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.edgecolor": BASE, "axes.linewidth": 0.8, "xtick.major.size": 5, "ytick.major.size": 0,
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
})
WATERMARK = mpimg.imread(HERE / "assets" / "mm-watermark.png")

BANKS = {
    "fed": dict(folder="fed_speech_index", country="US", body="FOMC", start="2008-01-01",
                rate_label="Fed funds target, midpoint (%)", members="FOMC voters"),
    "boe": dict(folder="boe", country="UK", body="BoE MPC", start="2018-06-28",
                rate_label="Bank Rate (%)", members="MPC members"),
    "boj": dict(folder="boj", country="Japan", body="BoJ Policy Board", start="2018-03-01",
                rate_label="BoJ short-term policy rate (%)", members="Policy Board members"),
}
# BoJ has no rate file in the repo; same table the yield_comparison CSVs use.
BOJ_RATE = [("2016-01-29", -0.10), ("2024-03-19", 0.10), ("2024-07-31", 0.25), ("2025-01-24", 0.50),
            ("2025-12-19", 0.75), ("2026-06-16", 1.00), ("2026-09-18", 1.25)]


# ---- data -------------------------------------------------------------------------------------------
def d(s: str) -> dt.date:
    return dt.date.fromisoformat(s[:10])


def series(path: Path, col: str = "index") -> tuple[list[dt.date], np.ndarray]:
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    return [d(r["date"]) for r in rows], np.array([float(r[col]) for r in rows])


def policy_rate(bank: str) -> list[tuple[dt.date, float]]:
    if bank == "fed":
        rows = csv.DictReader(open(ROOT / "fed_speech_index/data/fed_funds.csv"))
        mid = lambda s: float(s) if s.startswith("-") else float(np.mean([float(x) for x in s.split("-")]))  # noqa: E731
        return [(d(r["date"]), mid(r["level"])) for r in rows]
    if bank == "boe":
        return [(d(r["date"]), float(r["level"])) for r in csv.DictReader(open(ROOT / "boe/data/policy_rates.csv"))]
    return [(d(a), b) for a, b in BOJ_RATE]


def rate_at(rates, day):
    i = bisect.bisect_right([r[0] for r in rates], day) - 1
    return rates[i][1] if i >= 0 else np.nan


def cycles(rates, start):
    """Consecutive same-direction moves -> (first, last, +1/-1)."""
    moves = [(rates[i][0], np.sign(rates[i][1] - rates[i - 1][1])) for i in range(1, len(rates))]
    moves = [(day, s) for day, s in moves if s != 0 and day >= start]
    out = []
    for day, s in moves:
        if out and out[-1][2] == s:
            out[-1][1] = day
        else:
            out.append([day, day, s])
    return out


def scores(folder: str) -> dict:
    out = {}
    for line in open(ROOT / folder / "data/scores.jsonl", encoding="utf-8"):
        r = json.loads(line)
        out[r["id"]] = r
    return out


def speeches(folder: str) -> dict:
    out = {}
    for f in sorted((ROOT / folder / "data/speeches").glob("*.csv")):
        for r in csv.DictReader(open(f, encoding="utf-8")):
            r.pop("text", None)
            out[r["id"]] = r
    return out


# ---- chart scaffolding ------------------------------------------------------------------------------
def figure(title: str, rows=1, ratios=None, sharex=True, mark=(0.13, 0.62)):
    fig, axes = plt.subplots(rows, 1, figsize=(W, H), dpi=DPI, sharex=sharex,
                             gridspec_kw={"height_ratios": ratios} if ratios else None)
    fig.patch.set_facecolor("white")
    fig.text(0.5, 0.955, title, ha="center", va="center", fontsize=17, color=TEXT)
    fig.text(0.5, 0.905, "MacroMicro.me | MacroMicro", ha="center", va="center", fontsize=12, color=MUTED)
    fig.figimage(WATERMARK, xo=int(mark[0] * W * DPI), yo=int(mark[1] * H * DPI), zorder=0.5, alpha=1)
    axes = np.atleast_1d(axes)
    for ax in axes:
        ax.set_facecolor("none")
        ax.grid(axis="y", color=GRID, lw=0.8)
        ax.set_axisbelow(True)
        ax.tick_params(labelsize=9)
    return fig, axes


def years(ax, step=None):
    ax.xaxis.set_major_locator(mdates.YearLocator(step or 1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))


def legend(fig, handles, labels, y=0.03):
    fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.5, y), ncol=len(labels), frameon=False,
               prop={"weight": "bold", "size": 11}, handlelength=1.4, columnspacing=1.6)


def save(fig, path: Path, bottom=0.14):
    fig.subplots_adjust(left=0.08, right=0.97, top=0.85, bottom=bottom, hspace=0.18)
    fig.savefig(path, dpi=DPI, facecolor="white")
    plt.close(fig)


def shade_cycles(ax, cyc):
    for a, b, s in cyc:
        b = max(b, a + dt.timedelta(days=25))
        ax.axvspan(a, b, color=TIGHT_BAND if s > 0 else EASE_BAND, lw=0, zorder=0)


# ---- charts -----------------------------------------------------------------------------------------
def scored_example(bank, cfg, out):
    sc, sp = scores(cfg["folder"]), speeches(cfg["folder"])
    good = [(sp[i]["date"], i) for i, r in sc.items() if i in sp and r.get("policy_relevant")
            and r.get("level") not in (None, 5) and all(r.get("evidence_verified") or [False])]
    _, sid = max(good)
    r, s = sc[sid], sp[sid]
    ev = r["evidence"]
    ev = ast.literal_eval(ev) if isinstance(ev, str) else ev
    level = int(r["level"])

    fig, _ = figure(f"{cfg['country']} - How a Speech Becomes a Score", rows=1, mark=(0.64, 0.72))
    fig.axes[0].remove()
    head = f"{s['speaker']}  ·  {d(s['date']).strftime('%b %-d, %Y')}"
    fig.text(0.06, 0.80, head, fontsize=13, weight="bold", color=TEXT)
    fig.text(0.06, 0.755, textwrap.shorten(s.get("title") or "", 70, placeholder=" …"), fontsize=10.5, color=MUTED, style="italic")

    # 11-step scale
    ax = fig.add_axes([0.06, 0.60, 0.88, 0.09])
    ax.set_xlim(-0.5, 10.5), ax.set_ylim(0, 1), ax.axis("off")
    for k in range(11):
        ax.add_patch(Rectangle((k - 0.46, 0.25), 0.92, 0.6, color=DIVERGING(k / 10), lw=0))
        ax.text(k, 0.55, str(k * 10), ha="center", va="center", fontsize=9,
                color="white" if k in (0, 1, 9, 10) else TEXT)
    ax.add_patch(Rectangle((level - 0.5, 0.18), 1.0, 0.74, fill=False, ec=TEXT, lw=2))
    ax.text(0, 0.0, "Emergency easing", ha="left", va="center", fontsize=8.5, color=MUTED)
    ax.text(5, 0.0, "Hold", ha="center", va="center", fontsize=8.5, color=MUTED)
    ax.text(10, 0.0, "Aggressive tightening", ha="right", va="center", fontsize=8.5, color=MUTED)

    defn = LEVELS[level][1]
    name, rest = defn.split(".", 1)
    fig.text(0.06, 0.535, f"Level {level} = {int(LEVELS[level][0])}:  {name.title()}.", fontsize=11.5,
             weight="bold", color=TEXT)
    fig.text(0.06, 0.505, "\n".join(textwrap.wrap(rest.strip(), 150)[:2]), fontsize=9, color=MUTED, va="top")

    fig.text(0.06, 0.40, "Evidence quoted from the speech", fontsize=10.5, weight="bold", color=TEXT)
    y = 0.365
    wrap = 64 if bank == "boj" else 150
    for q in ev[:2]:
        lines = textwrap.wrap(f"“{q.strip()}”", wrap)[:2]
        if len(textwrap.wrap(f"“{q.strip()}”", wrap)) > 2:
            lines[-1] = lines[-1].rstrip("”") + "…”"
        fig.text(0.075, y, "\n".join(lines), fontsize=9.5, color=TEXT, va="top", linespacing=1.4)
        y -= 0.05 * len(lines) + 0.015
    fig.text(0.06, y - 0.01, "Why this level", fontsize=10.5, weight="bold", color=TEXT)
    fig.text(0.075, y - 0.045, "\n".join(textwrap.wrap(r["rationale"], 150)[:3]), fontsize=9.5, color=TEXT,
             va="top", linespacing=1.4)
    fig.savefig(out / "1_scored_example.png", dpi=DPI, facecolor="white")
    plt.close(fig)


def index_and_decisions(bank, cfg, out, idx, rates):
    x, y = idx
    start = d(cfg["start"])
    fig, (a1, a2) = figure(f"{cfg['country']} - {cfg['body']} Speech Index and Policy Decisions", 2, [3, 1.3])
    cyc = cycles(rates, start)
    for ax in (a1, a2):
        shade_cycles(ax, cyc)
    a1.axhline(50, color=MUTED, lw=0.8, ls=(0, (4, 3)), zorder=1)
    l1, = a1.plot(x, y, color=BLUE, lw=1.6, zorder=3)
    a1.set_ylim(0, 100), a1.set_ylabel("Index (50 = hold)")
    rx = [start] + [r[0] for r in rates if r[0] > start] + [x[-1]]
    ry = [rate_at(rates, t) for t in rx]
    l2, = a2.step(rx, ry, where="post", color=NAVY, lw=1.6, zorder=3)
    a2.set_ylabel("Rate (%)")
    a2.set_xlim(start, x[-1])
    years(a2, 2 if bank == "fed" else 1)
    p1, p2 = Rectangle((0, 0), 1, 1, color=TIGHT_BAND), Rectangle((0, 0), 1, 1, color=EASE_BAND)
    legend(fig, [l1, l2, p1, p2], ["Speech index", cfg["rate_label"], "Hiking cycle", "Cutting cycle"])
    save(fig, out / "2_index_and_decisions.png")


def surname(bank, name):
    # The Fed file names pre-2010 members by surname only and later ones in full; key everyone by surname.
    return name.split()[0].title() if bank == "boj" else name.replace(".", "").split()[-1]


def member_heatmap(bank, cfg, out):
    agg = defaultdict(list)
    first = {}
    for r in csv.DictReader(open(ROOT / cfg["folder"] / f"output/{bank}_member_scores.csv", encoding="utf-8")):
        if not r["score"]:
            continue
        day = d(r["date"])
        name = surname(bank, r["member"])
        agg[(name, day.year, day.month)].append(float(r["score"]))
        first.setdefault(name, day)
    members = sorted(first, key=first.get)
    months = sorted({(y, m) for _, y, m in agg})
    grid = np.full((len(members), len(months)), np.nan)
    mi = {m: i for i, m in enumerate(members)}
    ti = {t: i for i, t in enumerate(months)}
    for (m, y, mo), v in agg.items():
        grid[mi[m], ti[(y, mo)]] = np.mean(v)
    fig, (ax,) = figure(f"{cfg['country']} - {cfg['body']} Member Scores Over Time")
    ax.grid(False)
    t0 = mdates.date2num(dt.date(*months[0], 1))
    t1 = mdates.date2num(dt.date(*months[-1], 28))
    im = ax.imshow(np.ma.masked_invalid(grid), aspect="auto", cmap=DIVERGING, vmin=0, vmax=100,
                   interpolation="nearest", extent=[t0, t1, len(members) - 0.5, -0.5], zorder=2)
    ax.set_yticks(range(len(members)))
    ax.set_yticklabels(members, fontsize=max(4.2, min(8.5, 300 / len(members))))
    ax.xaxis_date()
    years(ax, 2 if bank == "fed" else 1)
    ax.spines["bottom"].set_visible(False)
    cax = fig.add_axes([0.35, 0.075, 0.3, 0.025])
    cb = fig.colorbar(im, cax=cax, orientation="horizontal", ticks=[0, 25, 50, 75, 100])
    cb.ax.tick_params(labelsize=8, length=0)
    cb.outline.set_visible(False)
    fig.text(0.34, 0.087, "Dovish", ha="right", va="center", fontsize=10, weight="bold")
    fig.text(0.66, 0.087, "Hawkish", ha="left", va="center", fontsize=10, weight="bold")
    fig.text(0.97, 0.008, "Monthly average of each member's latest speech score; blank = not on the committee "
             "or no score yet", ha="right", fontsize=7.5, color=MUTED)
    fig.subplots_adjust(left=0.1, right=0.97, top=0.85, bottom=0.17)
    fig.savefig(out / "3_member_heatmap.png", dpi=DPI, facecolor="white")
    plt.close(fig)


def stance_groups(bank, cfg, out):
    rows = list(csv.DictReader(open(ROOT / f"voter_history/{bank}_stance_groups.csv")))
    x = [d(r["date"]) for r in rows]
    cols = ["Lean to ease", "Hold", "Lean to tighten"]
    v = np.array([[float(r[c] or 0) for c in cols] for r in rows]).T
    fig, (ax,) = figure(f"{cfg['country']} - Where the {cfg['members']} Stand")
    polys = ax.stackplot(x, v, colors=[DOVE, HOLD, HAWK], step="post", lw=0, edgecolor="white")
    ax.set_ylim(0, v.sum(0).max() * 1.05)
    ax.set_xlim(x[0], x[-1])
    ax.set_ylabel(f"Number of {cfg['members']}")
    years(ax, 2 if bank == "fed" else 1)
    legend(fig, polys, ["Lean to ease (below 50)", "Hold (50)", "Lean to tighten (above 50)"])
    save(fig, out / "4_stance_groups.png")


def disagreement(bank, cfg, out, idx):
    x, y = idx
    dx, dy = series(ROOT / cfg["folder"] / f"output/{bank}_speech_disagreement.csv")
    fig, (a1, a2) = figure(f"{cfg['country']} - {cfg['body']} Speech Index and Disagreement", 2, [3, 1.5])
    a1.axhline(50, color=MUTED, lw=0.8, ls=(0, (4, 3)))
    l1, = a1.plot(x, y, color=BLUE, lw=1.6)
    a1.set_ylim(0, 100), a1.set_ylabel("Index (50 = hold)")
    l2, = a2.plot(dx, dy, color=BROWN, lw=1.4)
    a2.set_ylim(0, None), a2.set_ylabel("Std. dev. (pts)")
    a2.set_xlim(x[0], x[-1])
    years(a2, 2 if bank == "fed" else 1)
    legend(fig, [l1, l2], ["Speech index", "Disagreement (spread of members' scores)"])
    save(fig, out / "5_disagreement.png")


def coverage(bank, cfg, out):
    sc, sp = scores(cfg["folder"]), speeches(cfg["folder"])
    start = d(cfg["start"])
    rel, irr = defaultdict(int), defaultdict(int)
    for i, r in sc.items():
        if i not in sp:
            continue
        day = d(sp[i]["date"])
        if day < start:
            continue
        key = dt.date(day.year, day.month, 15)
        (rel if r.get("policy_relevant") else irr)[key] += 1
    months = []
    t = dt.date(start.year, start.month, 15)
    end = max(rel | irr)
    while t <= end:
        months.append(t)
        t = dt.date(t.year + (t.month == 12), t.month % 12 + 1, 15)
    a = np.array([rel[m] for m in months])
    b = np.array([irr[m] for m in months])
    fig, (ax,) = figure(f"{cfg['country']} - {cfg['body']} Speeches Scored per Month")
    w = 24 if bank == "fed" else 25
    b1 = ax.bar(months, a, width=w, color=BLUE, lw=0)
    b2 = ax.bar(months, b, width=w, bottom=a, color=HOLD, lw=0)
    ax.set_ylabel("Speeches")
    ax.set_xlim(months[0] - dt.timedelta(days=20), months[-1] + dt.timedelta(days=20))
    years(ax, 2 if bank == "fed" else 1)
    fig.text(0.97, 0.008, f"Total {a.sum() + b.sum():,} speeches, {a.sum():,} with a policy view; "
             f"avg {(a.sum() + b.sum()) / len(months):.1f} a month", ha="right", fontsize=8, color=MUTED)
    legend(fig, [b1, b2], ["Has a policy view (scored)", "No policy view (excluded)"], y=0.035)
    save(fig, out / "6_coverage.png", bottom=0.18)


def main():
    for bank, cfg in BANKS.items():
        out = HERE / bank
        out.mkdir(exist_ok=True)
        x, y = series(ROOT / cfg["folder"] / f"output/{bank}_speech_index.csv")
        keep = [i for i, t in enumerate(x) if t >= d(cfg["start"])]
        idx = ([x[i] for i in keep], y[keep])
        rates = policy_rate(bank)
        scored_example(bank, cfg, out)
        index_and_decisions(bank, cfg, out, idx, rates)
        member_heatmap(bank, cfg, out)
        stance_groups(bank, cfg, out)
        disagreement(bank, cfg, out, idx)
        coverage(bank, cfg, out)
        print(bank, "->", out)


if __name__ == "__main__":
    main()
