"""Test 2 (BOE): do members' stances before a meeting predict how they vote, relative to their colleagues?

    cd boe && .venv/bin/python ../comparative_pilot/vote_test.py <path to mpcvoting.xlsx>

Pass marks P7 and P8 are in PREREGISTRATION.md. For each member at each meeting since the index starts:
  relative vote   = rate the member voted for - rate decided (basis points)
  relative stance = member's stance the day before - mean stance of that day's voters
Three stances: v10 carried forward (old method), v10 through the stance filter (current), combined scores
through the stance filter (proposed; filter refitted on combined readings by the same maximum likelihood).
Rows are kept only where the old method has a stance, so all three are scored on the same votes.
Writes comparative_pilot/boe/vote_test.json.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())
import numpy as np  # noqa: E402

import build_index  # noqa: E402
import history  # noqa: E402
import stance_filter  # noqa: E402
import voters  # noqa: E402

HERE = Path(__file__).resolve().parent


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
    if not len(pos) or not len(neg):
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return float(wins / (len(pos) * len(neg)))


def main(path):
    import re
    from io import BytesIO
    from zipfile import ZipFile
    from lxml import etree
    content = Path(path).read_bytes()
    meetings = history.parse_votes(content)                     # dates and who voted (validated parser)
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(BytesIO(content)) as a:
        strings = ["".join(n.itertext()) for n in etree.fromstring(a.read("xl/sharedStrings.xml")).findall("s:si", ns)]
        rows = etree.fromstring(a.read("xl/worksheets/sheet1.xml")).findall(".//s:row", ns)
        cells = []
        for row in rows:
            v = {}
            for c in row:
                x = c.findtext("s:v", namespaces=ns)
                if x is not None:
                    v[re.sub(r"\d", "", c.get("r"))] = strings[int(x)] if c.get("t") == "s" else x
            cells.append(v)
    header = next(r for r in cells if r.get("C") == "Current members")
    names = {col: " ".join(n.split()) for col, n in header.items() if col not in {"A", "B", "C", "M"} and n not in {"Current members", "Past members"}}
    votes = {}
    for r in cells:
        b = r.get("B", "")
        if re.fullmatch(r"\d+(?:\.0+)?", b) and 35000 <= float(b) <= 90000:
            day = (dt.date(1899, 12, 30) + dt.timedelta(days=int(float(b)))).isoformat()
            num = lambda x: re.fullmatch(r"-?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?", x or "")
            if not num(r.get("C")):
                continue                                  # no numeric Bank Rate decision on this row
            decided = float(r["C"])
            # Rate votes only: cells with words (votes on other instruments) are skipped.
            votes[day] = {names[c]: round((float(v) - decided) * 10000) for c, v in r.items() if c in names and num(v)}
    roster = voters.Roster()
    start = min(build_index.build_daily(build_index.policy_speeches(), roster, dt.date.today())[0], key=lambda r: r["date"])["date"]
    meetings = [m for m in meetings if m["date"] >= start]
    days = [(dt.date.fromisoformat(m["date"]) - dt.timedelta(days=1)).isoformat() for m in meetings]

    speeches = build_index.policy_speeches()
    pid = lambda key: "|".join(key)
    voters_on = lambda d: [pid(voters.raw_key(t["name"])) for t in roster.active_terms(d)]
    v10_obs = stance_filter.prepare((s["available_date"], pid(s["key"]), s["value"], s["date"]) for s in speeches)
    combined = {r["id"]: r["combined"] for r in json.loads((HERE / "boe" / "scores.json").read_text())}
    missing = [s["id"] for s in speeches if s["id"] not in combined]
    if missing:
        sys.exit(f"{len(missing)} BOE texts have no combined score")
    comb_obs = stance_filter.prepare((s["available_date"], pid(s["key"]), min(max(combined[s["id"]], 0.0), 100.0), s["date"]) for s in speeches)
    v10_params = stance_filter.load_params(build_index.FILTER_PARAMS)
    comb_params, _ = stance_filter.fit(comb_obs)
    f_v10 = {r["date"]: r["members"] for r in stance_filter.daily(v10_obs, sorted(set(days)), voters_on, v10_params)}
    f_comb = {r["date"]: r["members"] for r in stance_filter.daily(comb_obs, sorted(set(days)), voters_on, comb_params)}
    stances = build_index.Stances(speeches)

    rows = []
    for m, day in zip(meetings, days):
        terms = {pid(voters.raw_key(t["name"])): t for t in roster.active_terms(day)}
        carry, carry_c = {}, {}
        for p, t in terms.items():
            sp = stances.latest_speech(voters.raw_key(t["name"]), day, t["start_date"])
            if sp:
                carry[p] = sp["value"]
                carry_c[p] = float(np.mean([min(max(combined[i], 0.0), 100.0) for i in sp["ids"].split("|")]))
        if len(carry) < 2:
            continue
        for name, rel_vote in votes.get(m["date"], {}).items():
            p = pid(voters.raw_key(voters.resolve_speaker(name) or name))
            if p not in carry or p not in f_v10.get(day, {}):
                continue
            mean = lambda d: np.mean([d[q][0] if isinstance(d[q], tuple) else d[q] for q in d if q in terms])
            rows.append({"meeting": m["date"], "member": name, "rel_vote": rel_vote,
                         "old": carry[p] - np.mean(list(carry.values())),
                         "combined_carry": carry_c[p] - np.mean(list(carry_c.values())),
                         "current": f_v10[day][p][0] - mean(f_v10[day]),
                         "proposed": f_comb[day][p][0] - mean(f_comb[day])})
    y = np.array([r["rel_vote"] for r in rows], float)
    out = {"meetings": len({r["meeting"] for r in rows}), "member_votes": len(rows), "dissents": int((y != 0).sum()),
           "tighter_dissents": int((y > 0).sum()), "looser_dissents": int((y < 0).sum()),
           "filter_params_combined": comb_params, "filter_params_v10": v10_params}
    for k in ("old", "current", "proposed", "combined_carry"):
        x = np.array([r[k] for r in rows])
        d = y != 0
        out[k] = {"spearman_rel_vote": round(spearman(x, y), 3), "dissent_auc": round(auc(x[d], y[d] > 0), 3) if d.any() else None}
    # Bootstrap by meeting for proposed - current.
    by_meeting = {}
    for k, r in enumerate(rows):
        by_meeting.setdefault(r["meeting"], []).append(k)
    groups = list(by_meeting.values()); rng = random.Random(7); diffs, adiffs = [], []
    for _ in range(1000):
        sel = [k for _ in groups for k in groups[rng.randrange(len(groups))]]
        ys = y[sel]
        xc = np.array([rows[k]["current"] for k in sel]); xp = np.array([rows[k]["proposed"] for k in sel])
        diffs.append(spearman(xp, ys) - spearman(xc, ys))
        d = ys != 0
        if d.any() and (ys[d] > 0).any() and (ys[d] < 0).any():
            adiffs.append(auc(xp[d], ys[d] > 0) - auc(xc[d], ys[d] > 0))
    ci = lambda v: [round(float(np.percentile(v, 2.5)), 3), round(float(np.percentile(v, 97.5)), 3)]
    out["proposed_minus_current"] = {"spearman": round(out["proposed"]["spearman_rel_vote"] - out["current"]["spearman_rel_vote"], 3), "spearman_ci95": ci(diffs),
                                     "dissent_auc": round(out["proposed"]["dissent_auc"] - out["current"]["dissent_auc"], 3) if out["proposed"]["dissent_auc"] is not None else None,
                                     "dissent_auc_ci95": ci(adiffs) if adiffs else None}
    # Exploratory (not pre-registered): is the filter itself worse than carry-forward on dissents?
    pairs = {"current_minus_old": ("current", "old"), "combined_carry_minus_proposed": ("combined_carry", "proposed")}
    for name, (p_, q_) in pairs.items():
        sd_, ad_ = [], []
        for _ in range(1000):
            sel = [k for _ in groups for k in groups[rng.randrange(len(groups))]]
            ys = y[sel]; xp = np.array([rows[k][p_] for k in sel]); xq = np.array([rows[k][q_] for k in sel]); d = ys != 0
            sd_.append(spearman(xp, ys) - spearman(xq, ys))
            if d.any() and (ys[d] > 0).any() and (ys[d] < 0).any():
                ad_.append(auc(xp[d], ys[d] > 0) - auc(xq[d], ys[d] > 0))
        out.setdefault("exploratory", {})[name] = {"spearman_ci95": ci(sd_), "dissent_auc_ci95": ci(ad_)}
    out["pass_marks"] = {"P7_no_worse_on_votes": out["proposed"]["spearman_rel_vote"] >= out["current"]["spearman_rel_vote"] - 0.02,
                         "P8_no_worse_on_dissents": out["proposed"]["dissent_auc"] is not None and out["proposed"]["dissent_auc"] >= out["current"]["dissent_auc"] - 0.02}
    (HERE / "boe" / "vote_test.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main(sys.argv[1])
