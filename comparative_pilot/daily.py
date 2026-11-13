"""The daily step: score one new text against a fixed history (past scores are never revised).

A new text gets its passages (one extraction call), then 6 comparisons in 3 rounds of 2 against existing texts
near its current estimate (one partner in four is drawn at random, as in the full run). Its score maximises

    log N(v10 value | score, sigma_v) + sum over its comparisons of log P(verdict | score, partner's fixed score)

with sigma_v, s and the tie rate taken from the last full fit. Nothing else moves.

P10 test (PREREGISTRATION_FED.md):
    cd fed_speech_index && .venv/bin/python ../comparative_pilot/daily.py test fed
Writes comparative_pilot/fed/daily_test.json and fed/daily_verdicts.jsonl.
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

import numpy as np

import combined
import confirm

GRID = np.arange(-20.0, 120.0001, 0.05)
ROUNDS, PER_ROUND, RANDOM_SHARE, NEAREST = 3, 2, 0.25, 3


def score_one(v10_value, partner_scores, outcomes, sigma_v, s, nu):
    """Maximum of the posterior over GRID. outcomes: 1 the new text more hawkish, 0 less, 0.5 same."""
    ll = -((GRID - v10_value) ** 2) / (2 * sigma_v ** 2)
    for q, o in zip(partner_scores, outcomes):
        pa, pb, pt = combined._davidson(GRID / s, np.full_like(GRID, q / s), nu)
        ll += np.log({1: pa, 0: pb, 0.5: pt}[o])
    return float(GRID[int(np.argmax(ll))])


def pick(est, history, used, rng):
    others = [i for i in history if i not in used]
    if rng.random() < RANDOM_SHARE:
        return rng.choice(others)
    return rng.choice(sorted(others, key=lambda i: abs(history[i] - est))[:NEAREST])


def run_text(t, history, fitp, ask, rng):
    """history: {id: fixed score}. ask(a_id, b_id) -> 'A' | 'B' | 'same' with a = first shown."""
    est, partners, outcomes, used, log = t["v10_level"] * 10.0, [], [], set(), []
    for r in range(ROUNDS):
        for _ in range(PER_ROUND):
            j = pick(est, history, used, rng); used.add(j)
            first_new = rng.random() < 0.5
            ans = ask(t["id"], j) if first_new else ask(j, t["id"])
            if ans is None:
                continue
            o = 0.5 if ans == "same" else float((ans == "A") == first_new)
            partners.append(history[j]); outcomes.append(o)
            log.append({"new": t["id"], "partner": j, "round": r, "new_first": first_new, "answer": ans})
        est = score_one(t["v10_level"] * 10.0, partners, outcomes, fitp["sigma_v"], fitp["s"], fitp["nu"])
    return est, log


def cmd_test(bank, n=100):
    key = confirm.api_key()
    texts, passages, appendix = confirm.bank_material(bank)
    res = json.loads((confirm.folder(bank) / "results.json").read_text())
    full = {r["id"]: r["combined"] for r in json.loads((confirm.folder(bank) / "scores.json").read_text())}
    ids = sorted(texts, key=lambda i: (texts[i]["date"], i))
    held = ids[-n:]
    keep = ids[:-n]
    idx = {i: k for k, i in enumerate(keep)}
    rows = [r for r in confirm.jsonl(confirm.folder(bank) / "verdicts.jsonl")
            if r["mode"] == "passages" and r["a"] in idx and r["b"] in idx]
    y = np.array([texts[i]["v10_level"] for i in keep], float)
    sv, sp = res["chosen"]["sigma_v"], res["chosen"]["s"]
    sc, s, nu = combined.fit(y, (np.array([idx[r["a"]] for r in rows]), np.array([idx[r["b"]] for r in rows]),
                                 np.array([combined.OUTCOME[r["answer"]] for r in rows])), sv, sp)
    fitp = {"sigma_v": sv, "s": s, "nu": nu}
    history = {i: float(sc[idx[i]]) for i in keep}
    out = confirm.folder(bank) / "daily_verdicts.jsonl"
    cache = {(r["a"], r["b"]): r["answer"] for r in confirm.jsonl(out)} if out.exists() else {}

    def ask(a, b):
        if (a, b) in cache:
            return cache[(a, b)]
        A, B = texts[a], texts[b]
        notes = f"Notes on the {confirm.BANK_NAMES[bank]}:\n{appendix.strip()}\n\n" if appendix.strip() else ""
        user = notes + confirm.describe("A", A, passages[a], None) + "\n\n" + confirm.describe("B", B, passages[b], None)
        try:
            v = confirm.call(confirm.COMPARE_SYSTEM, user, confirm.Verdict, key)
        except Exception as e:
            print("  compare failed:", str(e)[:100]); return None
        confirm.append(out, {"a": a, "b": b, "answer": v.answer, "reason": v.reason, "prompt": confirm.prompt_hash()})
        cache[(a, b)] = v.answer
        return v.answer

    rng = random.Random(11)
    daily = {}
    for k, i in enumerate(held):               # date order; each scored text joins the fixed history
        daily[i], _ = run_text(texts[i], history, fitp, ask, rng)
        history[i] = daily[i]
        if (k + 1) % 20 == 0:
            print(f"  daily {k + 1}/{n}", flush=True)
    a = np.array([daily[i] for i in held]); b = np.array([full[i] for i in held])
    diff = np.abs(a - b)
    report = {"texts": n, "from": texts[held[0]]["date"], "to": texts[held[-1]]["date"],
              "median_abs_diff": round(float(np.median(diff)), 2), "p90_abs_diff": round(float(np.percentile(diff, 90)), 2),
              "max_abs_diff": round(float(diff.max()), 2),
              "rank_correlation": round(float(np.corrcoef(np.argsort(np.argsort(a)), np.argsort(np.argsort(b)))[0, 1]), 3),
              "v10_only_median_abs_diff": round(float(np.median(np.abs(np.array([texts[i]["v10_level"] * 10 for i in held]) - b))), 2),
              "fit": fitp}
    report["P10_pass"] = report["median_abs_diff"] <= 4 and report["rank_correlation"] >= 0.9
    report["rows"] = [{"id": i, "date": texts[i]["date"], "speaker": texts[i]["speaker"], "v10": texts[i]["v10_level"] * 10,
                       "daily": round(daily[i], 2), "full": round(full[i], 2)} for i in held]
    (confirm.folder(bank) / "daily_test.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    {"test": cmd_test}[sys.argv[1]](*sys.argv[2:])
