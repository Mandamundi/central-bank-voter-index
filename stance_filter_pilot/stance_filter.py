"""Prototype: estimate every voter's stance on every day with a Kalman filter, instead of carrying each
voter's last score forward unchanged. Reads a bank project's stored scores; writes only inside
stance_filter_pilot/<bank>/. Run from the bank's folder with its own environment:

    cd fed_speech_index && .venv/bin/python ../stance_filter_pilot/stance_filter.py fed
    cd boe              && .venv/bin/python ../stance_filter_pilot/stance_filter.py boe
    cd boj              && .venv/bin/python ../stance_filter_pilot/stance_filter.py boj

Model (all in index points, 0-100):
  stance of voter i on day t = c_t + d_i,t
  c_t   the committee's shared view. It drifts day to day and reverts slowly toward 50 (hold).
  d_i,t voter i's own deviation from it. It drifts and reverts toward 0: voters lean with the group.
  A policy-rate change adds a one-off jump in variance to both, because the scale is relative to the setting.
  Each scored speech day: y = c_t + d_i,t + noise (scoring noise plus day-to-day wording).

Seven parameters (sd and half-life of c, sd and half-life of d, the two jump sizes, the noise sd) are
estimated by maximum likelihood from that bank's own speeches. No weight is chosen by hand, and no bank
borrows another's numbers.

The filter only uses data up to each day (publication day for the BOE and BOJ), so the index never looks
ahead, the same as carry-forward.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import os
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.getcwd())
import build_index  # noqa: E402  (the bank project's own loaders, so stances match the published index)
import config       # noqa: E402
import voters       # noqa: E402

BANK = sys.argv[1] if len(sys.argv) > 1 else "fed"
OUT = Path(__file__).resolve().parent / BANK
TODAY = dt.datetime.now(dt.timezone.utc).date()
PARAMS = ["sd_c", "halflife_c", "sd_d", "halflife_d", "jump_c", "jump_d", "noise"]


# ---------------------------------------------------------------- data, per bank

def fed_person_ids(keys):
    """Map (surname, initial) keys to one id per person. Old Fed minutes give surnames only."""
    initials = {}
    for s, i in keys:
        if i:
            initials.setdefault(s, set()).add(i)
    def pid(key):
        s, i = key
        if not i and len(initials.get(s, ())) == 1:
            i = next(iter(initials[s]))
        return f"{s}|{i}"
    return pid


def load_fed():
    roster = voters.Roster()
    speeches = build_index.policy_speeches()
    pid = fed_person_ids(set(roster.ever()) | {s["key"] for s in speeches})
    obs = {}
    for s in speeches:
        obs.setdefault((s["date"], pid(s["key"])), []).append(s["value"])
    moves = [r["date"] for r in csv.DictReader(open(config.FED_FUNDS))]
    names = {}
    for k, n in build_index.member_names().items():
        if len(n) > len(names.get(pid(k), "")):
            names[pid(k)] = n
    on = lambda day: list(dict.fromkeys(pid(k) for k in roster.on(day)))
    people = {pid(k) for k in roster.ever()}
    return obs, moves, on, names, people, "2017-01-01"


def boj_rate_changes():
    import policy
    changes, prev = [], None
    for date, sentences, has_rate in policy._statements():
        rate = None
        for s in sentences:
            # The short-term guideline only; "10-year ... 1.0 percent" is the yield-control cap, not a rate.
            if re.search(r"call rate|negative interest rate", s, re.I) and not re.search(r"yield", s, re.I):
                m = re.search(r"(minus\s+)?(\d+(?:\.\d+)?)\s*percent", s, re.I)
                if m:
                    rate = ("-" if m.group(1) else "") + m.group(2)
                    break
        if rate is not None and rate != prev:
            changes.append(date)
            prev = rate
    return changes


def load_ledger_bank():
    roster = voters.Roster()
    speeches = build_index.policy_speeches()
    pid = lambda key: "|".join(key)
    obs = {}
    for s in speeches:
        # A stance enters on the day it was published, as in the live index.
        obs.setdefault((s["available_date"], pid(s["key"])), []).append(s["value"])
    if BANK == "boe":
        moves = [r["date"] for r in csv.DictReader(open(config.POLICY_RATES))]
    else:
        moves = boj_rate_changes()
    names = {pid(voters.raw_key(t["name"])): t["name"] for t in roster.terms}
    on = lambda day: [pid(voters.raw_key(t["name"])) for t in roster.active_terms(day)]
    people = set(names)
    dates = sorted(d for d, _ in obs)
    fit_end = dates[int(0.6 * len(dates))]       # fit on the first 60% of speeches, test on the rest
    return obs, moves, on, names, people, fit_end


def load():
    obs, moves, on, names, people, fit_end = load_fed() if BANK == "fed" else load_ledger_bank()
    obs = sorted((d, p, float(np.mean(v))) for (d, p), v in obs.items())
    people = sorted(people | {p for _, p, _ in obs})
    live = {r["date"]: float(r["index"]) for r in csv.DictReader(open(config.OUTPUT / f"{BANK}_speech_index.csv")) if r["index"]}
    member_rows = list(csv.DictReader(open(config.OUTPUT / f"{BANK}_member_scores.csv")))
    last = max(r["date"] for r in member_rows)
    live_members = {r["member"]: r for r in member_rows if r["date"] == last}
    return obs, moves, on, names, people, fit_end, live, live_members


def day_number(date):
    return dt.date.fromisoformat(date).toordinal()


# ---------------------------------------------------------------- filter

class Filter:
    def __init__(self, theta, people):
        self.sd_c, self.hl_c, self.sd_d, self.hl_d, self.jump_c, self.jump_d, self.noise = theta
        self.idx = {p: k + 1 for k, p in enumerate(people)}
        n = len(people) + 1
        self.x = np.zeros(n); self.x[0] = 50.0
        self.P = np.diag(np.array([self.sd_c ** 2] + [self.sd_d ** 2] * (n - 1), dtype=float))
        self.phi_c = 0.5 ** (1 / self.hl_c)
        self.phi_d = 0.5 ** (1 / self.hl_d)

    def propagate(self, days, n_moves):
        if days <= 0 and n_moves == 0:
            return
        fc, fd = self.phi_c ** days, self.phi_d ** days
        f = np.full(len(self.x), fd); f[0] = fc
        self.x[0] = 50 + fc * (self.x[0] - 50)
        self.x[1:] *= fd
        self.P *= np.outer(f, f)
        q = np.full(len(self.x), self.sd_d ** 2 * (1 - fd ** 2) + n_moves * self.jump_d ** 2)
        q[0] = self.sd_c ** 2 * (1 - fc ** 2) + n_moves * self.jump_c ** 2
        self.P[np.diag_indices_from(self.P)] += q

    def predict(self, person):
        i = self.idx[person]
        return self.x[0] + self.x[i], self.P[0, 0] + 2 * self.P[0, i] + self.P[i, i] + self.noise ** 2

    def update(self, person, y):
        i = self.idx[person]
        mean, var = self.predict(person)
        ph = self.P[:, 0] + self.P[:, i]
        k = ph / var
        self.x += k * (y - mean)
        self.P -= np.outer(k, ph)
        return mean, var

    def stance(self, person):
        i = self.idx[person]
        return self.x[0] + self.x[i], self.P[0, 0] + 2 * self.P[0, i] + self.P[i, i]

    def index(self, people):
        a = np.zeros(len(self.x)); a[0] = 1.0
        for p in people:
            a[self.idx[p]] += 1 / len(people)
        return float(a @ self.x), float(a @ self.P @ a)


def run(theta, obs, moves, people, until=None, on_obs=None, on_day=None, days=None):
    """Walk through the observations (and any extra calendar days). Returns the log-likelihood of the
    observations before `until` (all of them if None)."""
    f = Filter(theta, people)
    move_days = sorted(day_number(m) for m in moves)
    t = day_number(obs[0][0])
    ll = 0.0
    events = [(day_number(d), 0, d, p, y) for d, p, y in obs]
    if days:
        events += [(day_number(d), 1, d, None, None) for d in days]
    events.sort(key=lambda e: (e[0], e[1]))
    for n, kind, d, p, y in events:
        crossed = sum(t < m <= n for m in move_days) if n > t else 0
        f.propagate(n - t, crossed)
        t = n
        if kind == 0:
            mean, var = f.update(p, y)
            if until is None or d < until:
                ll += -0.5 * (math.log(2 * math.pi * var) + (y - mean) ** 2 / var)
            if on_obs:
                on_obs(d, p, y, mean, var)
        elif on_day:
            on_day(d, f)
    return ll


# ---------------------------------------------------------------- fitting

def to_theta(z):
    return [math.exp(v) for v in z]


def nelder_mead(fun, x0, step=0.4, iters=800, tol=1e-5):
    n = len(x0)
    pts = [np.array(x0, float)] + [np.array(x0, float) + step * np.eye(n)[i] for i in range(n)]
    vals = [fun(p) for p in pts]
    for _ in range(iters):
        order = np.argsort(vals); pts = [pts[i] for i in order]; vals = [vals[i] for i in order]
        if abs(vals[-1] - vals[0]) < tol * (1 + abs(vals[0])):
            break
        c = np.mean(pts[:-1], axis=0)
        xr = c + (c - pts[-1]); fr = fun(xr)
        if fr < vals[0]:
            xe = c + 2 * (c - pts[-1]); fe = fun(xe)
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = c + 0.5 * (pts[-1] - c); fc = fun(xc)
            if fc < vals[-1]:
                pts[-1], vals[-1] = xc, fc
            else:
                pts = [pts[0] + 0.5 * (p - pts[0]) for p in pts]
                vals = [vals[0]] + [fun(p) for p in pts[1:]]
    i = int(np.argmin(vals))
    return pts[i], vals[i]


def jumps_kept():
    """identify.py's likelihood-ratio verdict for this bank (kept until it has been run)."""
    path = OUT / "chosen_params.csv"
    if not path.exists():
        return True
    return {r["param"]: r["value"] for r in csv.DictReader(open(path))}["jumps_kept"] == "True"


def fit(obs, moves, people, until):
    """Maximum likelihood. Returns log-parameters (jumps at log(0) = -inf when the data do not support them)."""
    keep = jumps_kept()
    free = PARAMS if keep else [p for p in PARAMS if not p.startswith("jump")]
    init = dict(zip(PARAMS, [12, 180, 10, 120, 5, 5, 8]))
    def full(zf):
        z = dict(zip(free, zf))
        return np.array([z.get(p, -np.inf) for p in PARAMS])
    nll = lambda zf: -run(to_theta(full(zf)), obs, moves, people, until=until)
    best, val = nelder_mead(nll, [math.log(init[p]) for p in free])
    best, val = nelder_mead(nll, best, step=0.15)          # restart from the optimum to make sure it settled
    return full(best), -val


def confidence(z, obs, moves, people, h=0.05):
    """95% range of each parameter from the curvature of the log-likelihood (in log terms). A range that is
    very wide, or missing, means the data cannot pin that number down."""
    free = [k for k in range(len(z)) if np.isfinite(z[k])]   # dropped parameters (log 0) have no range
    def nll(v):
        full = np.array(z, float); full[free] = v
        return -run(to_theta(full), obs, moves, people)
    zf = np.array([z[k] for k in free])
    n = len(zf); H = np.zeros((n, n)); f0 = nll(zf)
    for i in range(n):
        for j in range(i, n):
            e_i, e_j = np.eye(n)[i] * h, np.eye(n)[j] * h
            if i == j:
                H[i, i] = (nll(zf + e_i) - 2 * f0 + nll(zf - e_i)) / h ** 2
            else:
                H[i, j] = H[j, i] = (nll(zf + e_i + e_j) - nll(zf + e_i - e_j) - nll(zf - e_i + e_j) + nll(zf - e_i - e_j)) / (4 * h * h)
    out = {name: "dropped" for name in PARAMS}
    try:
        cov = np.linalg.inv(H)
        for a, k in enumerate(free):
            se = math.sqrt(cov[a, a]) if cov[a, a] > 0 else float("nan")
            out[PARAMS[k]] = [math.exp(zf[a] - 1.96 * se), math.exp(zf[a] + 1.96 * se)] if se == se else None
    except np.linalg.LinAlgError:
        out.update({PARAMS[k]: None for k in free})
    return out


# ---------------------------------------------------------------- evaluation

def prediction_test(theta, obs, moves, people, start):
    """Predict each speech before seeing it: filter vs carry-forward (the live method)."""
    last, rows = {}, []
    def on_obs(d, p, y, mean, var):
        if d >= start and p in last:
            rows.append((y, mean, last[p][1], (dt.date.fromisoformat(d) - dt.date.fromisoformat(last[p][0])).days))
        last[p] = (d, y)
    run(theta, obs, moves, people, on_obs=on_obs)
    def summary(sub):
        if not sub:
            return None
        e_f = np.array([r[0] - r[1] for r in sub]); e_c = np.array([r[0] - r[2] for r in sub])
        # Paired bootstrap: 95% range of how much lower the filter's error is than carry-forward's.
        rng = np.random.default_rng(0)
        draws = [rng.integers(0, len(sub), len(sub)) for _ in range(2000)]
        mae_gain = sorted(float(np.mean(abs(e_c[i])) - np.mean(abs(e_f[i]))) for i in draws)
        rmse_gain = sorted(float(np.sqrt(np.mean(e_c[i] ** 2)) - np.sqrt(np.mean(e_f[i] ** 2))) for i in draws)
        ci = lambda v: [round(v[50], 2), round(v[1949], 2)]
        return {"n": len(sub), "mae_filter": round(float(np.mean(abs(e_f))), 2), "mae_carry_forward": round(float(np.mean(abs(e_c))), 2),
                "mae_gain_95": ci(mae_gain),
                "rmse_filter": round(float(np.sqrt(np.mean(e_f ** 2))), 2), "rmse_carry_forward": round(float(np.sqrt(np.mean(e_c ** 2))), 2),
                "rmse_gain_95": ci(rmse_gain),
                "filter_closer_share": round(float(np.mean(abs(e_f) < abs(e_c))), 3)}
    gaps = [(0, 30), (30, 60), (60, 120), (120, 240), (240, 100000)]
    return {"all": summary(rows), "by_days_since_last_speech": {f"{a}-{b}d": summary([r for r in rows if a <= r[3] < b]) for a, b in gaps}}


def daily_series(theta, obs, moves, people, on, start):
    days, d = [], dt.date.fromisoformat(start)
    while d <= TODAY:
        days.append(d.isoformat()); d += dt.timedelta(days=1)
    out, members = [], []
    def on_day(day, f):
        voters_today = [p for p in on(day) if p in f.idx]
        if not voters_today:
            return
        m, v = f.index(voters_today)
        out.append((day, m, math.sqrt(v)))
        if day == days[-1]:
            members.extend((p, *f.stance(p)) for p in voters_today)
    run(theta, obs, moves, people, on_day=on_day, days=days)
    return out, members


def main():
    obs, moves, on, names, people, fit_end, live, live_members = load()
    OUT.mkdir(exist_ok=True)
    print(f"{BANK}: {len(obs)} speaker-days from {obs[0][0]}, {len(people)} people, {len(moves)} rate changes; test from {fit_end}")

    z_train, ll_train = fit(obs, moves, people, until=fit_end)
    test = prediction_test(to_theta(z_train), obs, moves, people, start=fit_end)
    z_full, ll_full = fit(obs, moves, people, until=None)
    ranges = confidence(z_full, obs, moves, people)
    theta_train, theta_full = to_theta(z_train), to_theta(z_full)
    print("  fitted on first part:", {k: round(v, 1) for k, v in zip(PARAMS, theta_train)})
    print("  fitted on all       :", {k: round(v, 1) for k, v in zip(PARAMS, theta_full)})
    print("  95% ranges          :", {k: (r if not isinstance(r, list) else [round(r[0], 1), round(r[1], 1)]) for k, r in ranges.items()})
    print("  prediction test     :", json.dumps(test["all"]))

    series, members = daily_series(theta_full, obs, moves, people, on, min(live))
    with open(OUT / "filtered_index.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["date", "filtered", "band_low", "band_high", "carry_forward"])
        for d, m, s in series:
            w.writerow([d, f"{m:.2f}", f"{m - 1.96 * s:.2f}", f"{m + 1.96 * s:.2f}", f"{live[d]:.2f}" if d in live else ""])
    with open(OUT / "current_members.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["member", "filtered", "sd", "carry_forward", "last_speech"])
        for p, s, sv in sorted(members, key=lambda r: -r[1]):
            n = names.get(p, p); lm = live_members.get(n, {})
            w.writerow([n, f"{s:.1f}", f"{math.sqrt(sv):.1f}", lm.get("score", ""), lm.get("as_of", "")])
    json.dump({"bank": BANK, "fit_end": fit_end, "speaker_days": len(obs), "people": len(people), "rate_changes": len(moves),
               "params_first_part": dict(zip(PARAMS, theta_train)), "params_all": dict(zip(PARAMS, theta_full)),
               "ranges_95": ranges, "loglik_all": ll_full, "prediction_test": test},
              open(OUT / "results.json", "w"), indent=2)
    print("  wrote", OUT)


if __name__ == "__main__":
    main()
