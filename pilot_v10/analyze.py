"""Summarize pilot_v10/pilot_results.json (run `pilot.py report` first). Run from the repository root:

    fed_speech_index/.venv/bin/python pilot_v10/analyze.py
"""
from __future__ import annotations

import bisect
import collections
import csv
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
rows = json.loads((HERE / "pilot_results.json").read_text())


def rate_lookup(bank):
    if bank == "fed":
        data = [(r["date"], np.mean([float(x) for x in r["level"].split("-")]))
                for r in csv.DictReader(open(ROOT / "fed_speech_index/data/fed_funds.csv"))]
    elif bank == "boe":
        data = [(r["date"], float(r["level"])) for r in csv.DictReader(open(ROOT / "boe/data/policy_rates.csv"))]
    else:
        return None
    dates = [d for d, _ in data]
    return lambda day: data[bisect.bisect_right(dates, day) - 1][1]


print("== Repeatability (run 1 vs run 2)")
for bank in ("fed", "boe", "boj", None):
    sel = [r for r in rows if bank in (None, r["bank"])]
    rel_same = sum(r["v10_relevant"][0] == r["v10_relevant"][1] for r in sel)
    both = [r for r in sel if all(r["v10_relevant"])]
    exact = sum(r["v10_levels"][0] == r["v10_levels"][1] for r in both)
    within1 = sum(abs(r["v10_levels"][0] - r["v10_levels"][1]) <= 1 for r in both)
    print(f"{bank or 'all':4} texts {len(sel):3}  relevance agrees {rel_same}/{len(sel)}  "
          f"level exact {exact}/{len(both)} ({exact / max(len(both), 1):.0%})  within one {within1}/{len(both)}")

print("\n== Relevance vs v9 (run 1)")
for bank in ("fed", "boe", "boj"):
    c = collections.Counter((r["v9_relevant"], r["v10_relevant"][0]) for r in rows if r["bank"] == bank)
    print(bank, {f"v9 {'rel' if a else 'not'} -> v10 {'rel' if b else 'not'}": n for (a, b), n in sorted(c.items())})

print("\n== Quotes found verbatim in the text:", sum(r["quotes_ok"] for r in rows), "/", len(rows))

print("\n== How much of the score is the rate level (texts relevant in v9 and both v10 runs)")
for bank in ("fed", "boe"):
    rate = rate_lookup(bank)
    sel = [r for r in rows if r["bank"] == bank and r["v9_relevant"] and all(r["v10_relevant"])]
    x = np.array([rate(r["date"]) for r in sel])
    v9 = np.array([r["v9_level"] for r in sel])
    v10 = np.array([np.mean(r["v10_levels"]) for r in sel])
    print(f"{bank}: n={len(sel)}  corr(level, policy rate)  v9 {np.corrcoef(x, v9)[0, 1]:+.2f}   v10 {np.corrcoef(x, v10)[0, 1]:+.2f}")

print("\n== Level distribution (run 1)")
for bank in ("fed", "boe", "boj"):
    c = collections.Counter(r["v10_levels"][0] for r in rows if r["bank"] == bank and r["v10_relevant"][0])
    print(bank, dict(sorted(c.items())))

print("\n== Every text (v9 level -> v10 run1/run2)")
for r in sorted(rows, key=lambda r: (r["bank"], r["date"])):
    v10 = "/".join("-" if lv is None else str(lv) for lv in r["v10_levels"])
    print(f"{r['bank']} {r['date']} {r['speaker'][:22]:22} v9 {r['v9_level'] if r['v9_level'] is not None else '-':>2} -> v10 {v10:5} "
          f"[{r['why']}] {r['rationale']}")
