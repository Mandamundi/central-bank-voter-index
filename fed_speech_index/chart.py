"""Chart whatever has been built so far (local use, not run by the GitHub Action).

    python chart.py    # writes charts/chart.png and charts/chart.html
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

import config  # noqa: E402
import store  # noqa: E402

CHARTS = config.ROOT / "charts"

PANELS = [
    ("fed_speech_index", "FOMC voters' speech hawk/dove index (50 = hold; below 50 = pressure to ease, 0 = emergency easing; above 50 = pressure to tighten)"),
    ("fed_speech_disagreement", "Disagreement (std. dev. of voters' latest speech scores)"),
]


def read_series(name: str) -> tuple[list[dt.date], list[float]]:
    path = config.OUTPUT / f"{name}.csv"
    if not path.exists():
        return [], []
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [dt.date.fromisoformat(r["date"]) for r in rows], [float(r["index"]) for r in rows]


def coverage() -> tuple[list[dt.date], list[dt.date]]:
    scored = store.load_scores()
    speeches = store.load_speeches()
    done = [dt.date.fromisoformat(s["date"]) for s in speeches if s["id"] in scored]
    todo = [dt.date.fromisoformat(s["date"]) for s in speeches if s["id"] not in scored]
    return done, todo


def png(series: dict, done: list, todo: list) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(13, 9), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1.4, 0.5]})
    for ax, (name, title) in zip(axes, PANELS):
        x, y = series[name]
        ax.plot(x, y, lw=1.4, color="#2a78d6")
        ax.set_title(title, fontsize=10, loc="left")
        ax.grid(axis="y", color="#e1e0d9", lw=0.8)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_ylim(0, 100)
    cov = axes[2]
    cov.eventplot([done, todo], colors=["#1baf7a", "#c3c2b7"], lineoffsets=[1, 0], linelengths=0.8)
    cov.set_yticks([0, 1], [f"unscored ({len(todo)})", f"scored ({len(done)})"], fontsize=8)
    cov.set_title("Speech scoring progress", fontsize=10, loc="left")
    cov.spines[["top", "right"]].set_visible(False)
    cov.xaxis.set_major_locator(mdates.YearLocator(2))
    cov.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout()
    fig.savefig(CHARTS / "chart.png", dpi=130)


def html(series: dict, done: list, todo: list) -> None:
    traces = []
    for k, (name, title) in enumerate(PANELS, start=1):
        x, y = series[name]
        traces.append({"x": [d.isoformat() for d in x], "y": y, "name": title, "xaxis": "x",
                       "yaxis": f"y{k if k > 1 else ''}", "type": "scatter", "mode": "lines",
                       "line": {"width": 1.5}})
    for label, dates, y, color in [("scored", done, 1, "#1baf7a"), ("unscored", todo, 0, "#c3c2b7")]:
        traces.append({"x": [d.isoformat() for d in dates], "y": [y] * len(dates), "name": f"{label} ({len(dates)})",
                       "xaxis": "x", "yaxis": "y3", "type": "scatter", "mode": "markers",
                       "marker": {"symbol": "line-ns-open", "size": 10, "color": color}})
    layout = {
        "height": 750, "showlegend": True, "legend": {"orientation": "h"}, "hovermode": "x unified",
        "grid": {"rows": 3, "columns": 1, "shared_xaxes": True},
        "yaxis": {"domain": [0.40, 1], "range": [0, 100]}, "yaxis2": {"domain": [0.12, 0.35]},
        "yaxis3": {"domain": [0, 0.08], "showticklabels": False},
    }
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>Fed speech index</title>
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script></head>
<body style="font-family:sans-serif"><div id="c"></div>
<script>Plotly.newPlot("c", {json.dumps(traces)}, {json.dumps(layout)});</script></body></html>"""
    (CHARTS / "chart.html").write_text(page, encoding="utf-8")


def main() -> int:
    series = {name: read_series(name) for name, _ in PANELS}
    done, todo = coverage()
    CHARTS.mkdir(exist_ok=True)
    png(series, done, todo)
    html(series, done, todo)
    print(f"Wrote {CHARTS / 'chart.png'} and chart.html ({len(done)} scored, {len(todo)} unscored).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
