"""Test 2 (BOJ): do members' stances before a meeting predict their votes? Same measures as vote_test.py (BOE).

    cd boj && .venv/bin/python ../comparative_pilot/boj_vote_test.py

The BOJ publishes no table of each member's preferred rate, only who dissented and why, so each member's
vote at a meeting is +1 (dissented for tighter policy), -1 (dissented for easier policy, or against a
tightening the majority made) or 0 (voted with the majority). The direction of each dissent is read from
the statement's wording with the fixed rules in DIRECTION and printed for checking; dissents the rules
cannot place are left out and listed. Writes comparative_pilot/boj/vote_test.json.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402

import build_index  # noqa: E402
import config  # noqa: E402
import stance_filter  # noqa: E402
import voters  # noqa: E402
from vote_test import auc, spearman  # noqa: E402

HERE = Path(__file__).resolve().parent
TIGHTER = r"appropriate to raise|should raise|larger (?:increase|hike)|raise .{0,40} by a larger|faster pace of (?:rate )?increases"
LOOSER = (r"easing|lower(?:ing)? (?:the )?(?:short|long|interest|policy)|further cut|deepen|forward guidance|"
          r"after confirming|premature|downside risks|below 2 percent|should decide on changing|not appropriate to raise|"
          r"maintain(?:ing)? the (?:current )?guideline")


def rate_increases():
    """Dates of short-term policy-rate increases, read from the guideline sentences of the statements."""
    import policy
    hikes, prev = set(), None
    for date, sentences, _ in policy._statements():
        rate = None
        for sent in sentences:
            if re.search(r"call rate|negative interest rate", sent, re.I) and not re.search(r"yield", sent, re.I):
                m = re.search(r"(minus\s+)?(\d+(?:\.\d+)?)\s*percent", sent, re.I)
                if m:
                    rate = (-1 if m.group(1) else 1) * float(m.group(2)); break
        if rate is not None:
            if prev is not None and rate > prev:
                hikes.add(date)
            prev = rate
    return hikes


HIKES = set()


def dissents(roster):
    """Dissenters matched against the roster: the board member whose surname appears just before "dissented"."""
    out = []
    for r in sorted(json.loads((config.DATA / "policy_context.json").read_text()).values(), key=lambda r: r["date"]):
        text = re.sub(r"\s+", " ", r["context"]).replace("V oting", "Voting")
        members = roster.active_terms(r["date"])
        for m in re.finditer(r"dissented,? ", text):
            window = text[max(0, m.start() - 45):m.start()].lower()
            hits = [t for t in members if surname(t["name"]) in window]
            if not hits:
                continue
            who = max(hits, key=lambda t: window.rfind(surname(t["name"])))
            reason = text[m.end():m.end() + 600]
            reason = re.split(r"\[Note|dissented", reason)[0]
            tt, ll = re.search(TIGHTER, reason, re.I), re.search(LOOSER, reason, re.I)
            if r["date"] in HIKES and not tt:
                direction = -1               # voted against a rate increase the majority made: wanted looser policy
            else:
                direction = 1 if tt and not ll else (-1 if ll and not tt else None)
            out.append({"date": r["date"], "name": who["name"], "direction": direction, "reason": reason[:220]})
        # Proposals for a different policy rate (e.g. "Voting against the action: TAKATA Hajime and TAMURA Naoki.
        # ... They proposed that ... the uncollateralized overnight call rate to remain at around 0.75 percent").
        # These notes never say "dissented", so the search above missed them. Direction: proposed rate vs decided.
        decided = re.search(r"overnight call rate to remain at around (\d+(?:\. ?\d+)?) percent", text)
        for m in re.finditer(r"Voting against the action: ([^.]+?)\.(.{0,1500}?)proposed that the Bank set the guideline for money "
                             r"market operations as follows: .{0,120}?around (minus )?(\d+(?:\. ?\d+)?) percent", text):
            if not decided:
                continue
            rate = (-1 if m.group(3) else 1) * float(m.group(4).replace(" ", ""))
            base = float(decided.group(1).replace(" ", ""))
            if rate == base:
                continue
            names = m.group(1).lower().replace(" ", "")      # PDF text splits names: "NAKAGAW A Junko"
            for t in members:
                if surname(t["name"]) in names:
                    out.append({"date": r["date"], "name": t["name"], "direction": 1 if rate > base else -1,
                                "reason": f"proposed {rate} percent vs decided {base}: " + m.group(2)[:150]})
    # One vote per member per meeting: a member with several dissent notes in one statement counts once,
    # placed if any note places them (all placed notes agree in direction here; checked below).
    merged = {}
    for d in out:
        k = (d["date"], d["name"])
        if k not in merged or (merged[k]["direction"] is None and d["direction"] is not None):
            merged[k] = d
        elif d["direction"] is not None and merged[k]["direction"] not in (None, d["direction"]):
            merged[k]["direction"] = None          # conflicting notes: leave unplaced
    return list(merged.values())


def surname(name):
    """Board names are written "SURNAME Given" in the roster."""
    parts = [p for p in re.split(r"[\s.]+", name) if p]
    upper = [p for p in parts if p.isupper() and len(p) > 1]
    return (upper[0] if upper else parts[0]).lower()


def main():
    roster = voters.Roster()
    speeches = build_index.policy_speeches()
    start = min(build_index.build_daily(speeches, roster, dt.date.today())[0], key=lambda r: r["date"])["date"]
    HIKES.update(rate_increases())
    print("rate increases:", sorted(HIKES))
    found = [d for d in dissents(roster) if d["date"] >= start]
    print(f"{len(found)} dissents since {start}:")
    for d in found:
        print(f"  {d['date']} {d['name']:22s} {'tighter' if d['direction'] == 1 else 'looser ' if d['direction'] == -1 else 'UNPLACED'} | {d['reason'][:120]}")
    meetings = sorted({r["date"] for r in json.loads((config.DATA / "policy_context.json").read_text()).values() if r["date"] >= start})

    pid = lambda key: "|".join(key)
    voters_on = lambda d: [pid(voters.raw_key(t["name"])) for t in roster.active_terms(d)]
    v10_obs = stance_filter.prepare((s["available_date"], pid(s["key"]), s["value"], s["date"]) for s in speeches)
    combined = {r["id"]: r["combined"] for r in json.loads((HERE / "boj" / "scores.json").read_text())}
    clip = lambda v: min(max(v, 0.0), 100.0)
    comb_obs = stance_filter.prepare((s["available_date"], pid(s["key"]), clip(combined[s["id"]]), s["date"]) for s in speeches)
    v10_params = stance_filter.load_params(build_index.FILTER_PARAMS)
    comb_params, _ = stance_filter.fit(comb_obs)
    days = [(dt.date.fromisoformat(m) - dt.timedelta(days=1)).isoformat() for m in meetings]
    f_v10 = {r["date"]: r["members"] for r in stance_filter.daily(v10_obs, sorted(set(days)), voters_on, v10_params)}
    f_comb = {r["date"]: r["members"] for r in stance_filter.daily(comb_obs, sorted(set(days)), voters_on, comb_params)}
    stances = build_index.Stances(speeches)
    by_meeting = {}
    for d in found:
        if d["direction"] is not None:
            by_meeting.setdefault(d["date"], {})[d["name"]] = d["direction"]

    rows = []
    for m, day in zip(meetings, days):
        terms = {pid(voters.raw_key(t["name"])): t for t in roster.active_terms(day)}
        carry, carry_c = {}, {}
        for p, t in terms.items():
            sp = stances.latest_speech(voters.raw_key(t["name"]), day, t["start_date"])
            if sp:
                carry[p] = sp["value"]
                carry_c[p] = float(np.mean([clip(combined[i]) for i in sp["ids"].split("|")]))
        if len(carry) < 2:
            continue
        mean = lambda f: np.mean([f[day][q][0] for q in terms if q in f[day]])
        for p, t in terms.items():
            if p not in carry:
                continue
            vote = by_meeting.get(m, {}).get(t["name"], 0)
            if vote == 0 and any(d["date"] == m and d["name"] == t["name"] for d in found):
                continue                      # an unplaced dissent: neither with the majority nor placeable
            rows.append({"meeting": m, "member": t["name"], "vote": vote,
                         "old": carry[p] - np.mean(list(carry.values())),
                         "current": f_v10[day][p][0] - mean(f_v10), "proposed": f_comb[day][p][0] - mean(f_comb),
                         "combined_carry": carry_c[p] - np.mean(list(carry_c.values()))})
    y = np.array([r["vote"] for r in rows], float)
    out = {"meetings": len({r["meeting"] for r in rows}), "member_votes": len(rows), "tighter_dissents": int((y > 0).sum()),
           "looser_dissents": int((y < 0).sum()), "unplaced_dissents": sum(d["direction"] is None for d in found)}
    for k in ("old", "current", "proposed", "combined_carry"):
        x = np.array([r[k] for r in rows]); d = y != 0
        a_ = auc(x[d], y[d] > 0) if d.any() else None
        # Substitute for the BOJ, where every placed dissent is for easier policy (not pre-registered):
        # do looser dissenters sit below members who voted with the majority? (AUC: majority above looser.)
        loose = auc(x[(y == 0) | (y < 0)], y[(y == 0) | (y < 0)] == 0)
        out[k] = {"spearman_rel_vote": round(spearman(x, y), 3), "dissent_auc": round(a_, 3) if a_ is not None else None,
                  "looser_dissent_auc": round(loose, 3) if loose is not None else None}
    groups = {}
    for k, r in enumerate(rows):
        groups.setdefault(r["meeting"], []).append(k)
    groups = list(groups.values()); rng = random.Random(7)
    def boot(p_, q_):
        sd_, ad_ = [], []
        for _ in range(1000):
            sel = [k for _ in groups for k in groups[rng.randrange(len(groups))]]
            ys = y[sel]; xp = np.array([rows[k][p_] for k in sel]); xq = np.array([rows[k][q_] for k in sel]); d = ys != 0
            sd_.append(spearman(xp, ys) - spearman(xq, ys))
            k_ = (ys == 0) | (ys < 0)
            if (ys < 0).any():
                ad_.append(auc(xp[k_], ys[k_] == 0) - auc(xq[k_], ys[k_] == 0))
        ci = lambda v: [round(float(np.percentile(v, 2.5)), 3), round(float(np.percentile(v, 97.5)), 3)] if v else None
        return {"spearman_ci95": ci(sd_), "looser_dissent_auc_ci95": ci(ad_)}
    out["proposed_minus_current"] = boot("proposed", "current")
    out["exploratory"] = {"current_minus_old": boot("current", "old"), "combined_carry_minus_proposed": boot("combined_carry", "proposed")}
    out["pass_marks"] = {"P7_no_worse_on_votes": out["proposed"]["spearman_rel_vote"] >= out["current"]["spearman_rel_vote"] - 0.02,
                         "P8_no_worse_on_dissents": None if out["tighter_dissents"] == 0 else out["proposed"]["dissent_auc"] >= out["current"]["dissent_auc"] - 0.02,
                         "P8_substitute_no_worse_on_looser_dissents": out["proposed"]["looser_dissent_auc"] >= out["current"]["looser_dissent_auc"] - 0.02}
    out["filter_params_combined"] = comb_params
    (HERE / "boj" / "vote_test.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({k: v for k, v in out.items() if k != "filter_params_combined"}, indent=2))


if __name__ == "__main__":
    main()
