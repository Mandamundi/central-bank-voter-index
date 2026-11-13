"""Combined model: each text's hawkishness (0-100) is measured twice, by its v10 level and by its comparisons.

    cd fed_speech_index && .venv/bin/python ../comparative_pilot/combined.py

  v10 value (level x 10) = score + noise with sd sigma_v
  comparisons: Bradley-Terry with ties on score / s   (s = how many points one "unit" of comparison odds is)

The scores and the tie rate are fitted by maximum likelihood. Two numbers cannot be fitted that way,
because each text has its own free score (with both free, the comparison scale collapses and every
comparison looks certain): sigma_v, how much to trust a v10 level against the comparisons, and s, how
many points one unit of comparison odds is worth. Both are chosen by held-out prediction: for each pair
of candidate values, fit on 4/5 of the comparison pairs and score how well the model predicts the other
1/5; keep the best. The whole grid is reported, so the choice can be checked.

v10's levels anchor the scale (50 = hold, 0 = crisis); the comparisons order texts within and across
neighbouring levels, and can move a text out of its level when they consistently disagree with it.
Writes combined_results.json and combined_scores.csv; reads only the pilot's files.
"""
from __future__ import annotations

import csv
import json
import math
import random
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUTCOME = {"A": 1.0, "B": 0.0, "same": 0.5}
GRID_V = [2, 3, 5, 7, 10, 15, 25]              # candidate sd of a v10 reading, in index points
GRID_S = [2, 3, 4, 6, 8, 12, 16]               # candidate points per unit of comparison odds
CASES = {"8c34327f8f30": "Waller 8 Oct 2026", "3df92926cf7f": "Waller 10 Nov 2026",
         "d58aee379744": "Logan 25 Aug 2025 (runoff; v10 8)", "433272da91cc": "Logan 15 Jul 2025",
         "796dcd32273e": "Miran 19 Nov 2025", "35ce0cbaad5f": "Musalem 22 Sep 2025",
         "38da20e41dbc": "Bernanke 18 Nov 2008 (crisis)", "9a97b48691e6": "Kohn 13 Jan 2009 (crisis)",
         "304e8e503f0e": "Barkin 27 Mar 2020 (crisis)", "704c75bec360": "Waller 13 Jul 2026 (corrected flip)",
         "6716f4e89bf2": "Waller 18 Oct 2023 (corrected flip)"}


def jsonl(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _davidson(xa, xb, nu):
    """P(a), P(b), P(same), computed stably (largest term subtracted before exponentiating)."""
    z = np.stack([xa, xb, math.log(nu) + (xa + xb) / 2])
    e = np.exp(z - z.max(axis=0))
    e /= e.sum(axis=0)
    return e[0], e[1], e[2]


def fit(y, comps, sigma_v, s_points, steps=3000, lr=0.03):
    """y: v10 levels for every text; comps: (i, j, outcome). Works in units of 10 points.
    Returns score (0-100), s (points), nu."""
    i, j, o = comps
    n = len(y)
    u = y.astype(float).copy()
    log_nu = 0.0
    s = s_points / 10
    sv = sigma_v / 10
    m = np.zeros(n + 2); v = np.zeros(n + 2)
    for t in range(1, steps + 1):
        nu = math.exp(log_nu)
        xa, xb = u[i] / s, u[j] / s
        pa, pb, pt = _davidson(xa, xb, nu)
        ga = (o == 1) + 0.5 * (o == 0.5) - (pa + 0.5 * pt)
        gb = (o == 0) + 0.5 * (o == 0.5) - (pb + 0.5 * pt)
        g = np.zeros(n + 2)
        np.add.at(g, i, ga / s); np.add.at(g, j, gb / s)
        g[:n] += -(u - y) / sv ** 2
        g[n + 1] = float(np.sum((o == 0.5) - pt))            # d/d log nu
        m = 0.9 * m + 0.1 * g; v = 0.999 * v + 0.001 * g ** 2
        step = lr * (m / (1 - 0.9 ** t)) / (np.sqrt(v / (1 - 0.999 ** t)) + 1e-8)
        u += step[:n]; log_nu += step[n + 1]
    return u * 10, s * 10, math.exp(log_nu)


def predict(score, s, nu, a, b):
    pa, pb, pt = _davidson(np.array([score[a] / s]), np.array([score[b] / s]), nu)
    return float(pa[0]), float(pb[0]), float(pt[0])


def main():
    sample = json.loads((HERE / "sample.json").read_text())
    idx = {s["id"]: k for k, s in enumerate(sample)}
    y = np.array([s["v10_level"] for s in sample], float)
    rows = {r["key"]: r for r in jsonl(HERE / "verdicts.jsonl") if r["mode"] == "passages"}
    crisis = HERE / "verdicts_crisis.jsonl"          # crisis comparisons re-asked with the sharpened crisis rule
    if crisis.exists():
        rows.update({r["key"]: r for r in jsonl(crisis)})
    rows = list(rows.values())
    arrays = lambda rs: (np.array([idx[r["a"]] for r in rs]), np.array([idx[r["b"]] for r in rs]),
                         np.array([OUTCOME[r["answer"]] for r in rs]))
    pair = lambda r: tuple(sorted((r["a"], r["b"])))
    pairs = sorted({pair(r) for r in rows})
    random.Random(3).shuffle(pairs)
    fold = {p: k % 5 for k, p in enumerate(pairs)}

    def held_out(sigma_v, s_points):
        preds = []
        for k in range(5):
            sc, s, nu = fit(y, arrays([r for r in rows if fold[pair(r)] != k]), sigma_v, s_points, steps=2000)
            preds += [(r, *predict(sc, s, nu, idx[r["a"]], idx[r["b"]])) for r in rows
                      if fold[pair(r)] == k and r["test"] == "main"]
        loss = -np.mean([math.log({"A": pa, "B": pb, "same": pt}[r["answer"]]) for r, pa, pb, pt in preds])
        dec = [(r, pa, pb) for r, pa, pb, pt in preds if r["answer"] != "same"]
        right = lambda r, pa, pb: (pa > pb) == (r["answer"] == "A")
        eq = [x for x in dec if y[idx[x[0]["a"]]] == y[idx[x[0]["b"]]]]
        return {"sigma_v": sigma_v, "s": s_points, "log_loss": round(float(loss), 4),
                "accuracy": round(sum(right(*x) for x in dec) / len(dec), 3),
                "accuracy_v10_equal_pairs": round(sum(right(*x) for x in eq) / len(eq), 3), "n_equal": len(eq)}

    curve = [held_out(sv, sp) for sv in GRID_V for sp in GRID_S]
    print("held-out log loss (rows: sigma_v; columns: s)")
    print("      " + "".join(f"{sp:>8}" for sp in GRID_S))
    for sv in GRID_V:
        print(f"{sv:>6}" + "".join(f"{c['log_loss']:>8.3f}" for c in curve if c["sigma_v"] == sv))
    best = min(curve, key=lambda c: c["log_loss"])
    print("chosen:", best)

    score, s, nu = fit(y, arrays(rows), best["sigma_v"], best["s"])
    # Bootstrap over comparisons for each text's uncertainty.
    rng = random.Random(5); boots = []
    for _ in range(150):
        draw = [rows[rng.randrange(len(rows))] for _ in rows]
        boots.append(fit(y, arrays(draw), best["sigma_v"], best["s"], steps=2000)[0])
    boots = np.array(boots); sd = boots.std(axis=0)

    out = {"curve": curve, "chosen": best, "points_per_comparison_unit": round(s, 2), "tie_param": round(nu, 3),
           "hold_texts_mean": round(float(score[y == 5].mean()), 1),
           "mean_by_v10_level": {int(l): round(float(score[y == l].mean()), 1) for l in sorted(set(y))},
           "moved_out_of_band": [{"id": sample[k]["id"], "date": sample[k]["date"], "speaker": sample[k]["speaker"],
                                  "v10": int(y[k]), "combined": round(float(score[k]), 1)}
                                 for k in np.argsort(-abs(score - 10 * y)) if abs(score[k] - 10 * y[k]) > 7.5][:12],
           "cases": {name: {"v10": int(y[idx[i]]) * 10, "combined": round(float(score[idx[i]]), 1), "sd": round(float(sd[idx[i]]), 1)}
                     for i, name in CASES.items() if i in idx},
           "waller_nov10_above_oct8_share": round(float(np.mean(boots[:, idx["3df92926cf7f"]] > boots[:, idx["8c34327f8f30"]])), 3)}
    comp_only = json.loads((HERE / "results.json").read_text())["held_out"]
    out["comparison_vs_alternatives"] = {
        "v10_levels_alone": {"accuracy": comp_only["accuracy_v10_levels"], "accuracy_v10_equal_pairs": 0.5},
        "comparisons_alone": {"accuracy": comp_only["accuracy_comparison_model"], "log_loss": comp_only["log_loss"],
                              "accuracy_v10_equal_pairs": comp_only["pairs_v10_scores_equal"]["accuracy_comparison_model"]},
        "combined": {k: best[k] for k in ["accuracy", "log_loss", "accuracy_v10_equal_pairs"]}}
    with open(HERE / "combined_scores.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["id", "date", "speaker", "v10_value", "combined", "sd"])
        for k in np.argsort(score):
            w.writerow([sample[k]["id"], sample[k]["date"], sample[k]["speaker"], int(y[k]) * 10, f"{score[k]:.1f}", f"{sd[k]:.1f}"])
    (HERE / "combined_results.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "curve"}, indent=2))


if __name__ == "__main__":
    main()
