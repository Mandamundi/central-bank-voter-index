"""Chart the filtered index against the live carry-forward index for each bank that has results.

    fed_speech_index/.venv/bin/python stance_filter_pilot/chart.py
"""
import csv
import datetime as dt
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
TITLES = {"fed": "Federal Reserve (FOMC voters)", "boe": "Bank of England (MPC)", "boj": "Bank of Japan (Policy Board)"}
banks = [b for b in TITLES if (HERE / b / "filtered_index.csv").exists()]

fig, axes = plt.subplots(len(banks), 1, figsize=(12, 3.6 * len(banks)), squeeze=False)
for ax, bank in zip(axes[:, 0], banks):
    rows = [r for r in csv.DictReader(open(HERE / bank / "filtered_index.csv")) if r["carry_forward"]]
    if bank == "fed":
        rows = [r for r in rows if r["date"] >= "2017-01-01"]   # same window as the other two
    d = [dt.date.fromisoformat(r["date"]) for r in rows]
    ax.fill_between(d, [float(r["band_low"]) for r in rows], [float(r["band_high"]) for r in rows],
                    color="#2a6fdb", alpha=0.15, lw=0, label="Filtered, 95% band")
    ax.plot(d, [float(r["carry_forward"]) for r in rows], color="#888888", lw=1.1, label="Live index (carry-forward)")
    ax.plot(d, [float(r["filtered"]) for r in rows], color="#2a6fdb", lw=1.4, label="Filtered index")
    ax.axhline(50, color="#444444", lw=0.6, ls=":")
    ax.set_ylim(0, 100)
    ax.set_title(TITLES[bank])
    ax.set_ylabel("0 ease · 50 hold · 100 tighten")
    ax.grid(alpha=0.25)
axes[0, 0].legend(loc="lower left", frameon=False)
fig.tight_layout()
fig.savefig(HERE / "filtered_vs_live.png", dpi=130)
print("wrote", HERE / "filtered_vs_live.png")
