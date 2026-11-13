"""Test 2 (Fed): P7, P8 and P9 in PREREGISTRATION_FED.md, on meetings from 2008 (the published index's span).

    cd fed_speech_index && .venv/bin/python ../comparative_pilot/fed_vote_test.py

Votes: fed/dissents_coded.json (+1 tighter than the decision, -1 looser, 0 with the majority).
Stances the day before each meeting, relative to the mean of that meeting's voters:
  old       v10 carried forward
  current   v10 through the stance filter (live)
  proposed  combined scores (texts from 2005) through the filter, parameters refitted by maximum likelihood
  combined_carry  combined scores carried forward (reported, no mark)
P9: the index the day before each meeting minus the index 90 days earlier, against the change in the federal
funds target at that meeting. Writes comparative_pilot/fed/vote_test.json.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402

import build_index  # noqa: E402
import score  # noqa: E402
import stance_filter  # noqa: E402
import voters  # noqa: E402


def ranks(x):
    order = np.argsort(x, kind="mergesort")
    r = np.empty(len(x)); r[order] = np.arange(len(x))
    for v in np.unique(x):                       # average ranks for ties
        k = x == v; r[k] = r[k].mean()
    return r


def spearman(a, b):
    return float(np.corrcoef(ranks(np.asarray(a)), ranks(np.asarray(b)))[0, 1])


def auc(score, positive):
    pos, neg = score[positive], score[~positive]
    return float(np.mean([(p > n) + 0.5 * (p == n) for p in pos for n in neg])) if len(pos) and len(neg) else None

HERE = Path(__file__).resolve().parent / "fed"
START = "2008-01-01"
day = lambda d, n: (dt.date.fromisoformat(d) + dt.timedelta(days=n)).isoformat()


def upper(level):
    return float(level.split("-")[-1])


def main():
    roster = voters.Roster()
    v10 = build_index.policy_speeches()
    combined = {r["id"]: min(max(r["combined"], 0.0), 100.0) for r in json.loads((HERE / "scores.json").read_text())}
    comb = [{**s, "value": combined[s["id"]]} for s in v10 if s["id"] in combined]
    print(f"{len(v10)} v10 texts; {len(comb)} with combined scores (from {comb[0]['date']})")
    today = dt.date(2026, 11, 12)

    pid, obs_v10 = build_index.filter_observations(v10, roster)
    _, obs_comb = build_index.filter_observations(comb, roster)
    p_v10 = stance_filter.load_params(build_index.FILTER_PARAMS)
    p_comb, _ = stance_filter.fit(obs_comb)
    print("filter params, combined:", {k: round(v, 2) for k, v in p_comb.items()})

    codes = [d for d in json.loads((HERE / "dissents_coded.json").read_text()) if d["direction"] and d["date"] >= START]
    meetings = sorted({r["date"] for r in voters.load() if r["date"] >= START} | {d["date"] for d in codes})
    vote = {}
    for d in codes:
        vote[(d["date"], voters.name_key(d["name"]))] = d["direction"]
    matched = {k for k in vote if any(voters.same_person(k[1], v) for v in roster.on(k[0]) + roster.on(day(k[0], 1)))}

    # Members the day before each meeting: who votes at the meeting (roster on the meeting date).
    eve = {day(m, -1): m for m in meetings}
    voters_at = lambda d: [pid(k) for k in roster.on(eve.get(d, d))]
    f_v10 = {r["date"]: r["members"] for r in stance_filter.daily(obs_v10, sorted(eve), voters_at, p_v10)}
    f_comb = {r["date"]: r["members"] for r in stance_filter.daily(obs_comb, sorted(eve), voters_at, p_comb)}
    st_v10, st_comb = build_index.Stances(v10), build_index.Stances(comb)

    rows = []
    for m in meetings:
        d = day(m, -1)
        keys = roster.on(m)
        carry = {k: st_v10.latest(k, d) for k in keys}
        carry_c = {k: st_comb.latest(k, d) for k in keys}
        keys = [k for k in keys if carry[k] is not None and carry_c[k] is not None and pid(k) in f_v10.get(d, {})]
        if len(keys) < 3:
            continue
        mean = lambda f: np.mean([f(k) for k in keys])
        for k in keys:
            v = next((x for (dd, kk), x in vote.items() if dd == m and voters.same_person(kk, k)), 0)
            rows.append({"meeting": m, "member": "|".join(k), "vote": v,
                         "old": carry[k] - mean(lambda q: carry[q]),
                         "current": f_v10[d][pid(k)][0] - mean(lambda q: f_v10[d][pid(q)][0]),
                         "proposed": f_comb[d][pid(k)][0] - mean(lambda q: f_comb[d][pid(q)][0]),
                         "combined_carry": carry_c[k] - mean(lambda q: carry_c[q])})
    y = np.array([r["vote"] for r in rows], float)
    found = sum(r["vote"] != 0 for r in rows)
    out = {"meetings": len({r["meeting"] for r in rows}), "member_votes": len(rows),
           "tighter_dissents": int((y > 0).sum()), "looser_dissents": int((y < 0).sum()),
           "dissents_coded_from_2008": len(codes), "dissents_matched_to_roster": len(matched), "dissents_in_rows": found,
           "filter_params_combined": p_comb}
    for k in ("old", "current", "proposed", "combined_carry"):
        x = np.array([r[k] for r in rows]); dmask = y != 0
        out[k] = {"spearman_vote": round(spearman(x, y), 3), "dissent_auc": round(auc(x[dmask], y[dmask] > 0), 3)}

    # P9: the published index itself.
    idx = {}
    for name, sp, prm in (("current", v10, p_v10), ("proposed", comb, p_comb)):
        _, rws = build_index.filtered_days(sp, roster, today, prm)
        idx[name] = {r["date"]: r["index"] for r in rws}
    dates, levels = score.load_fed_funds()
    dec = []
    for m in meetings:
        a, b = day(m, -1), day(m, -91)
        if a in idx["current"] and b in idx["current"] and a in idx["proposed"] and b in idx["proposed"]:
            change = round((upper(score.fed_funds_on(day(m, 2), dates, levels)) - upper(score.fed_funds_on(day(m, -1), dates, levels))) * 100)
            dec.append({"meeting": m, "change_bp": change, "current": idx["current"][a] - idx["current"][b],
                        "proposed": idx["proposed"][a] - idx["proposed"][b]})
    z = np.array([r["change_bp"] for r in dec], float)
    out["index_decisions"] = {"meetings": len(dec), "hikes": int((z > 0).sum()), "cuts": int((z < 0).sum()),
                              **{k: round(spearman(np.array([r[k] for r in dec]), z), 3) for k in ("current", "proposed")}}

    # Bootstrap by meeting.
    rng = random.Random(7)
    groups = {}
    for i, r in enumerate(rows):
        groups.setdefault(r["meeting"], []).append(i)
    groups = list(groups.values())
    def boot(p_, q_):
        s_, a_ = [], []
        for _ in range(1000):
            sel = [i for _ in groups for i in groups[rng.randrange(len(groups))]]
            ys = y[sel]; xp = np.array([rows[i][p_] for i in sel]); xq = np.array([rows[i][q_] for i in sel]); dm = ys != 0
            s_.append(spearman(xp, ys) - spearman(xq, ys))
            if (ys[dm] > 0).any() and (ys[dm] < 0).any():
                a_.append(auc(xp[dm], ys[dm] > 0) - auc(xq[dm], ys[dm] > 0))
        ci = lambda v: [round(float(np.percentile(v, 2.5)), 3), round(float(np.percentile(v, 97.5)), 3)]
        return {"spearman_ci95": ci(s_), "dissent_auc_ci95": ci(a_)}
    out["proposed_minus_current"] = boot("proposed", "current")
    out["exploratory"] = {"current_minus_old": boot("current", "old"), "combined_carry_minus_proposed": boot("combined_carry", "proposed")}
    zb = []
    for _ in range(1000):
        sel = [rng.randrange(len(dec)) for _ in dec]
        zz = z[sel]
        zb.append(spearman(np.array([dec[i]["proposed"] for i in sel]), zz) - spearman(np.array([dec[i]["current"] for i in sel]), zz))
    out["index_decisions"]["proposed_minus_current_ci95"] = [round(float(np.percentile(zb, 2.5)), 3), round(float(np.percentile(zb, 97.5)), 3)]

    out["pass_marks"] = {
        "P7_votes_no_worse": out["proposed"]["spearman_vote"] >= out["current"]["spearman_vote"] - 0.02,
        "P8_dissents_no_worse": out["proposed"]["dissent_auc"] >= out["current"]["dissent_auc"] - 0.02,
        "P9_index_decisions_no_worse": out["index_decisions"]["proposed"] >= out["index_decisions"]["current"] - 0.02,
    }
    (HERE / "vote_test.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({k: v for k, v in out.items() if k != "filter_params_combined"}, indent=2))


if __name__ == "__main__":
    main()
