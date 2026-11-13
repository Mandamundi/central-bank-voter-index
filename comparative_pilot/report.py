"""Fit the comparison model and run the pilot's tests (called by `pilot.py report`). Writes scores.csv and
results.json in comparative_pilot/.

Model (Bradley-Terry with ties, Davidson 1970): each text i has a hawkishness theta_i. For a pair,
  P(i more hawkish) = e^ti / D,  P(j) = e^tj / D,  P(same) = nu e^((ti+tj)/2) / D,  D = the sum of the three.
theta and nu are fitted by maximum likelihood (with a weak normal prior on theta so the scale is anchored).
No level boundaries and no spacing are chosen: the scale comes from the comparisons. It is mapped onto
0-100 only for reading, by matching the mean and spread of the texts' v10 values.
"""
from __future__ import annotations

import csv
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

import score
import store

HERE = Path(__file__).resolve().parent
OUTCOME = {"A": 1.0, "B": 0.0, "same": 0.5}
WALLER = ("8c34327f8f30", "3df92926cf7f")
FLIPS = ["704c75bec360", "6716f4e89bf2", "28923f8352cf"]


def jsonl(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def fit(n, comps, prior_sd=3.0, steps=3000, lr=0.05):
    """comps: arrays i, j, y (1 = i more hawkish, 0 = j, 0.5 = same). Returns theta, nu."""
    i, j, y = comps
    theta = np.zeros(n); log_nu = 0.0
    m = np.zeros(n + 1); v = np.zeros(n + 1)
    for t in range(1, steps + 1):
        nu = math.exp(log_nu)
        ea, eb, et = np.exp(theta[i]), np.exp(theta[j]), nu * np.exp((theta[i] + theta[j]) / 2)
        d = ea + eb + et
        pa, pb, pt = ea / d, eb / d, et / d
        win, lose, tie = (y == 1), (y == 0), (y == 0.5)
        ga = win * 1.0 + tie * 0.5 - (pa + 0.5 * pt)
        gb = lose * 1.0 + tie * 0.5 - (pb + 0.5 * pt)
        g = np.zeros(n + 1)
        np.add.at(g, i, ga); np.add.at(g, j, gb)
        g[:n] -= theta / prior_sd ** 2
        g[n] = float(np.sum(tie * 1.0 - pt))
        m = 0.9 * m + 0.1 * g; v = 0.999 * v + 0.001 * g ** 2
        step = lr * (m / (1 - 0.9 ** t)) / (np.sqrt(v / (1 - 0.999 ** t)) + 1e-8)
        theta += step[:n]; log_nu += step[n]
    return theta - theta.mean(), math.exp(log_nu)


def probs(theta, nu, a, b):
    ea, eb, et = math.exp(theta[a]), math.exp(theta[b]), nu * math.exp((theta[a] + theta[b]) / 2)
    d = ea + eb + et
    return ea / d, eb / d, et / d


def binom_ci(k, n):
    if n == 0:
        return None
    p = k / n; se = math.sqrt(p * (1 - p) / n)
    return [round(p - 1.96 * se, 3), round(p + 1.96 * se, 3)]


def main():
    sample = json.loads((HERE / "sample.json").read_text())
    idx = {s["id"]: k for k, s in enumerate(sample)}
    v10 = np.array([s["v10_level"] for s in sample], float)
    verdicts = jsonl(HERE / "verdicts.jsonl")
    by_test = defaultdict(list)
    for r in verdicts:
        by_test[r["test"]].append(r)
    passage = [r for r in verdicts if r["mode"] == "passages"]
    print(f"{len(verdicts)} verdicts {dict(Counter(r['test'] for r in verdicts))}")

    def arrays(rows):
        return (np.array([idx[r["a"]] for r in rows]), np.array([idx[r["b"]] for r in rows]),
                np.array([OUTCOME[r["answer"]] for r in rows]))

    n = len(sample)
    theta, nu = fit(n, arrays(passage))
    out = {"texts": n, "verdicts": dict(Counter(r["test"] for r in verdicts)), "tie_rate": round(sum(r["answer"] == "same" for r in passage) / len(passage), 3), "nu": nu}

    # ---- 1. Held-out comparisons (5 folds by pair; both orders of a pair stay together)
    pair_of = lambda r: tuple(sorted((r["a"], r["b"])))
    pairs = sorted({pair_of(r) for r in passage})
    rng = random.Random(3); rng.shuffle(pairs)
    fold = {p: k % 5 for k, p in enumerate(pairs)}
    held = []
    for k in range(5):
        train = [r for r in passage if fold[pair_of(r)] != k]
        th, nv = fit(n, arrays(train))
        for r in passage:
            if fold[pair_of(r)] == k and r["test"] == "main":
                pa, pb, pt = probs(th, nv, idx[r["a"]], idx[r["b"]])
                held.append((r, pa, pb, pt))
    decisive = [(r, pa, pb) for r, pa, pb, pt in held if r["answer"] != "same"]
    def bt_right(r, pa, pb):
        return (pa > pb) == (r["answer"] == "A")
    def v10_call(r):
        la, lb = v10[idx[r["a"]]], v10[idx[r["b"]]]
        return None if la == lb else ((la > lb) == (r["answer"] == "A"))
    bt_k = sum(bt_right(*x) for x in decisive)
    v10_calls = [v10_call(r) for r, _, _ in decisive]
    v10_k = sum(1.0 if c else (0.5 if c is None else 0.0) for c in v10_calls)
    equal = [x for x in decisive if v10_call(x[0]) is None]
    eq_k = sum(bt_right(*x) for x in equal)
    logloss = -np.mean([math.log({"A": pa, "B": pb, "same": pt}[r["answer"]]) for r, pa, pb, pt in held])
    out["held_out"] = {
        "comparisons": len(held), "decisive": len(decisive),
        "accuracy_comparison_model": round(bt_k / len(decisive), 3),
        "accuracy_v10_levels": round(v10_k / len(decisive), 3), "v10_cannot_call_share": round(len(equal) / len(decisive), 3),
        "pairs_v10_scores_equal": {"n": len(equal), "accuracy_comparison_model": round(eq_k / len(equal), 3) if equal else None,
                                   "ci95": binom_ci(eq_k, len(equal)), "chance": 0.5},
        "log_loss": round(float(logloss), 3), "log_loss_uniform": round(math.log(3), 3)}
    # Calibration: is the one-score model adequate? Predicted vs observed on held-out decisive comparisons.
    bins = defaultdict(lambda: [0, 0, 0.0])
    for r, pa, pb in decisive:
        p = pa / (pa + pb); b = min(int(p * 5), 4)
        bins[b][0] += 1; bins[b][1] += r["answer"] == "A"; bins[b][2] += p
    out["calibration"] = [{"predicted_A": round(c[2] / c[0], 2), "observed_A": round(c[1] / c[0], 2), "n": c[0]}
                          for b, c in sorted(bins.items())]

    # ---- 2. Order bias and repeatability
    main_dec = [r for r in by_test["main"] if r["answer"] != "same"]
    out["order_bias_share_A_wins"] = round(sum(r["answer"] == "A" for r in main_dec) / len(main_dec), 3)
    first = {(r["a"], r["b"]): r["answer"] for r in by_test["main"]}
    swap = {"A": "B", "B": "A", "same": "same"}
    rev = [(first[(r["b"], r["a"])], swap[r["answer"]]) for r in by_test["reversed"] if (r["b"], r["a"]) in first]
    out["reversed_repeat"] = {"n": len(rev), "same_verdict": round(sum(a == b for a, b in rev) / len(rev), 3) if rev else None,
                              "opposite_winner": round(sum({a, b} == {"A", "B"} for a, b in rev) / len(rev), 3) if rev else None}

    # ---- 3. Passages vs full text
    full = [(first.get((r["a"], r["b"])), r["answer"]) for r in by_test["full_text"]]
    full = [(a, b) for a, b in full if a]
    out["full_text_vs_passages"] = {"n": len(full), "same_verdict": round(sum(a == b for a, b in full) / len(full), 3) if full else None,
                                    "opposite_winner": round(sum({a, b} == {"A", "B"} for a, b in full) / len(full), 3) if full else None}

    # ---- Scores on a 0-100 reading scale, with bootstrap uncertainty
    values = v10 * 10
    scale = values.std() / theta.std(); shift = values.mean() - scale * theta.mean()
    to100 = lambda th: shift + scale * th
    boots = []
    rows = passage
    for b in range(150):
        draw = [rows[rng.randrange(len(rows))] for _ in rows]
        th, _ = fit(n, arrays(draw), steps=1500)
        boots.append(to100(th))
    boots = np.array(boots)
    sd = boots.std(axis=0)
    out["scale"] = {"points_per_theta": round(scale, 2), "v10_hold_texts_mean": round(float(np.mean(to100(theta)[v10 == 5])), 1)}
    rho = lambda x, y: float(np.corrcoef(np.argsort(np.argsort(x)), np.argsort(np.argsort(y)))[0, 1])
    out["rank_correlation_with_v10"] = round(rho(theta, v10), 3)
    within = {}
    for lv in range(11):
        k = v10 == lv
        if k.sum() >= 5:
            within[lv] = {"n": int(k.sum()), "spread_sd": round(float(to100(theta)[k].std()), 1),
                          "median_bootstrap_sd": round(float(np.median(sd[k])), 1)}
    out["spread_within_each_v10_level"] = within

    # Policy-rate level correlation (v10 was built to have none)
    dates, levels = score.load_fed_funds()
    mid = lambda s: float(np.mean([float(x) for x in s.replace("–", "-").split("-")])) if "-" in s.strip("-") else float(s)
    rate = np.array([mid(score.fed_funds_on(s["date"], dates, levels)) for s in sample])
    out["correlation_with_rate_level"] = {"comparison_score": round(float(np.corrcoef(theta, rate)[0, 1]), 3),
                                          "v10": round(float(np.corrcoef(v10, rate)[0, 1]), 3)}

    # ---- Known cases
    sc = to100(theta)
    a, b = idx[WALLER[0]], idx[WALLER[1]]
    out["waller"] = {"oct8": {"v10": int(v10[a]), "score": round(sc[a], 1), "sd": round(sd[a], 1)},
                     "nov10": {"v10": int(v10[b]), "score": round(sc[b], 1), "sd": round(sd[b], 1)},
                     "share_of_bootstraps_nov10_higher": round(float(np.mean(boots[:, b] > boots[:, a])), 3),
                     "direct_verdicts": [{"order": "Oct8=A" if r["a"] == WALLER[0] else "Nov10=A", "answer": r["answer"], "reason": r["reason"]}
                                         for r in verdicts if {r["a"], r["b"]} == set(WALLER)]}
    out["corrected_flips"] = {i: {"v10": int(v10[idx[i]]), "score": round(sc[idx[i]], 1), "sd": round(sd[idx[i]], 1)} for i in FLIPS if i in idx}

    # Largest disagreements with v10, for a human look
    gap = sc - values
    out["largest_disagreements_with_v10"] = [
        {"id": sample[k]["id"], "date": sample[k]["date"], "speaker": sample[k]["speaker"], "v10": int(v10[k]), "score": round(sc[k], 1)}
        for k in np.argsort(-abs(gap))[:8]]

    with open(HERE / "scores.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["id", "date", "speaker", "v10_level", "comparison_score", "sd"])
        for k in np.argsort(sc):
            w.writerow([sample[k]["id"], sample[k]["date"], sample[k]["speaker"], int(v10[k]), f"{sc[k]:.1f}", f"{sd[k]:.1f}"])
    (HERE / "results.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))
