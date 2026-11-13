"""Test 1 evaluation for one bank (called by `confirm.py report <bank>`). Checks the pass marks in
PREREGISTRATION.md and writes <bank>/results.json and <bank>/scores.json."""
from __future__ import annotations

import json
import math
import random
from collections import Counter

import numpy as np

import combined
import report
from confirm import folder, jsonl, load_texts

GRID_V = [3, 5, 7, 10, 15]
GRID_S = [2, 3, 4, 6, 8]
OUTCOME = {"A": 1.0, "B": 0.0, "same": 0.5}


def main(bank):
    texts = load_texts(bank)
    ids = sorted(t["id"] for t in texts)
    idx = {i: k for k, i in enumerate(ids)}
    v10 = np.array([{t["id"]: t["v10_level"] for t in texts}[i] for i in ids], float)
    rows = jsonl(folder(bank) / "verdicts.jsonl")
    fit_rows = [r for r in rows if r["mode"] == "passages"]
    arrays = lambda rs: (np.array([idx[r["a"]] for r in rs]), np.array([idx[r["b"]] for r in rs]),
                         np.array([OUTCOME[r["answer"]] for r in rs]))
    pair = lambda r: tuple(sorted((r["a"], r["b"])))
    pairs = sorted({pair(r) for r in fit_rows})
    random.Random(3).shuffle(pairs)
    fold = {p: k % 5 for k, p in enumerate(pairs)}
    right = lambda r, pa, pb: (pa > pb) == (r["answer"] == "A")

    def held(fit_fn):
        out = []
        for k in range(5):
            predict = fit_fn([r for r in fit_rows if fold[pair(r)] != k])
            out += [(r, *predict(idx[r["a"]], idx[r["b"]])) for r in fit_rows if fold[pair(r)] == k and r["test"] == "main"]
        return out

    def summary(h):
        dec = [(r, pa, pb) for r, pa, pb, pt in h if r["answer"] != "same"]
        eq = [x for x in dec if v10[idx[x[0]["a"]]] == v10[idx[x[0]["b"]]]]
        k = sum(right(*x) for x in eq)
        return {"accuracy": round(sum(right(*x) for x in dec) / len(dec), 3),
                "log_loss": round(float(-np.mean([math.log(max({"A": a, "B": b, "same": t}[r["answer"]], 1e-9)) for r, a, b, t in h])), 3),
                "equal_pairs": {"n": len(eq), "accuracy": round(k / len(eq), 3) if eq else None, "ci95": report.binom_ci(k, len(eq))}}

    # Combined model, settings chosen by held-out prediction.
    grid, helds = [], []
    # Fed: the frozen prompts were tuned on the 180 pilot texts; P1 and P2 count only pairs with neither text in
    # the pilot (PREREGISTRATION_FED.md).
    pilot = {json.loads(l)["id"] for l in open(folder(bank).parent / "passages.jsonl")} if bank == "fed" else set()
    clean = lambda h: [x for x in h if x[0]["a"] not in pilot and x[0]["b"] not in pilot]
    for sv in GRID_V:
        for sp in GRID_S:
            def fit_fn(rs, sv=sv, sp=sp):
                sc, s, nu = combined.fit(v10, arrays(rs), sv, sp, steps=1500)
                return lambda a, b: combined.predict(sc, s, nu, a, b)
            h = held(fit_fn); helds.append(h)
            grid.append({"sigma_v": sv, "s": sp, **summary(h)})
    best = min(grid, key=lambda g: g["log_loss"])
    best_h = helds[grid.index(best)]
    # Comparisons alone, and v10 alone.
    def bt_fn(rs):
        th, nu = report.fit(len(ids), arrays(rs))
        return lambda a, b: report.probs(th, nu, a, b)
    alone_h = held(bt_fn)
    alone = summary(alone_h)
    dec_main = [r for r in fit_rows if r["test"] == "main" and r["answer"] != "same"]
    def v10_call(r):          # v10 picks the higher level; a tie between levels is a coin flip
        la, lb = v10[idx[r["a"]]], v10[idx[r["b"]]]
        return 0.5 if la == lb else float((la > lb) == (r["answer"] == "A"))
    v10_acc = sum(v10_call(r) for r in dec_main) / len(dec_main)
    dec_clean = [r for r in dec_main if r["a"] not in pilot and r["b"] not in pilot]
    nonpilot = {"v10_alone": {"accuracy": round(sum(v10_call(r) for r in dec_clean) / len(dec_clean), 3)},
                "comparisons_alone": summary(clean(alone_h)), "combined": summary(clean(best_h))} if pilot else None

    score, s, nu = combined.fit(v10, arrays(fit_rows), best["sigma_v"], best["s"])
    rng = random.Random(5); boots = []
    for _ in range(100):
        draw = [fit_rows[rng.randrange(len(fit_rows))] for _ in fit_rows]
        boots.append(combined.fit(v10, arrays(draw), best["sigma_v"], best["s"], steps=1200)[0])
    sd = np.array(boots).std(axis=0)

    first = {(r["a"], r["b"]): r["answer"] for r in rows if r["test"] == "main"}
    swap = {"A": "B", "B": "A", "same": "same"}
    rev = [(first[(r["b"], r["a"])], swap[r["answer"]]) for r in rows if r["test"] == "reversed" and (r["b"], r["a"]) in first]
    full = [(first[(r["a"], r["b"])], r["answer"]) for r in rows if r["test"] == "full_text" and (r["a"], r["b"]) in first]
    opp = lambda xs: round(sum({a, b} == {"A", "B"} for a, b in xs) / len(xs), 3) if xs else None
    same = lambda xs: round(sum(a == b for a, b in xs) / len(xs), 3) if xs else None
    passages = jsonl(folder(bank) / "passages.jsonl")
    res = {
        "bank": bank, "texts": len(ids), "comparisons": dict(Counter(r["test"] for r in rows)),
        "comparisons_per_text_main": round(2 * sum(r["test"] == "main" for r in rows) / len(ids), 1),
        "passages": {"returned": sum(r["returned"] for r in passages), "verbatim": sum(r["verbatim"] for r in passages),
                     "texts_without": sum(not r["passages"] for r in passages)},
        "chosen": {"sigma_v": best["sigma_v"], "s": best["s"]}, "tie_param": round(nu, 3), "grid": grid,
        "held_out": {"v10_alone": {"accuracy": round(v10_acc, 3)}, "comparisons_alone": alone,
                     "combined": {k: best[k] for k in ("accuracy", "log_loss", "equal_pairs")}},
        "anchors": {"hold_mean": round(float(score[v10 == 5].mean()), 1) if (v10 == 5).any() else None,
                    "level0_mean": round(float(score[v10 == 0].mean()), 1) if (v10 == 0).any() else None,
                    "mean_by_level": {int(l): round(float(score[v10 == l].mean()), 1) for l in sorted(set(v10))}},
        "order_bias_share_A": round(sum(r["answer"] == "A" for r in dec_main) / len(dec_main), 3),
        "reversed": {"n": len(rev), "same_verdict": same(rev), "opposite_winner": opp(rev)},
        "full_text": {"n": len(full), "same_verdict": same(full), "opposite_winner": opp(full)},
        "median_sd": round(float(np.median(sd)), 1),
    }
    res["held_out_nonpilot"] = nonpilot
    pm = nonpilot or res["held_out"]
    res["pass_marks"] = {
        "P1_combined_beats_both": pm["combined"]["accuracy"] > pm["v10_alone"]["accuracy"] and pm["combined"]["accuracy"] > pm["comparisons_alone"]["accuracy"],
        "P2_resolves_v10_ties": bool(pm["combined"]["equal_pairs"]["ci95"] and pm["combined"]["equal_pairs"]["accuracy"] >= 0.60
                                     and pm["combined"]["equal_pairs"]["ci95"][0] > 0.5),
        "P3_hold_anchor": res["anchors"]["hold_mean"] is not None and abs(res["anchors"]["hold_mean"] - 50) <= 3,
        "P3_crisis_anchor": res["anchors"]["level0_mean"] is None or res["anchors"]["level0_mean"] <= 10,
        "P4_no_order_bias": 0.45 <= res["order_bias_share_A"] <= 0.55,
        "P5_passages_ok": res["full_text"]["opposite_winner"] is not None and res["reversed"]["opposite_winner"] is not None
                          and res["full_text"]["opposite_winner"] <= res["reversed"]["opposite_winner"] + 0.05,
    }
    (folder(bank) / "scores.json").write_text(json.dumps(
        [{"id": i, "date": {t["id"]: t["date"] for t in texts}[i], "speaker": {t["id"]: t["speaker"] for t in texts}[i],
          "v10": int(v10[k]) * 10, "combined": round(float(score[k]), 2), "sd": round(float(sd[k]), 2)} for k, i in enumerate(ids)],
        ensure_ascii=False, indent=1), encoding="utf-8")
    (folder(bank) / "results.json").write_text(json.dumps({**res, "fit": {"s": s, "nu": nu}}, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "grid"}, indent=2))
