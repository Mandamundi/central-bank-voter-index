"""Each voter's stance on every day, estimated with a Kalman filter from the scored texts.

The same file is used by all three banks (fed_speech_index/, boe/, boj/); keep the copies identical.

Why: carrying a voter's last score forward unchanged treats a year-old speech as if it were said today.
Speeches are occasional, noisy readings of views that keep evolving, and voters' views move with the
committee. This filter estimates each voter's stance on each day from everything said up to that day.

Model, in index points (0-100):
  stance of voter i on day t = c_t + d_i,t
  c_t    the committee's shared view: drifts day to day, reverting slowly toward 50 (hold).
  d_i,t  voter i's own deviation from it: drifts, reverting slowly toward 0 (voters lean with the group).
  a scored text on day t gives y = c_t + d_i,t + noise (scoring noise and day-to-day wording).

Its five numbers (the spread and half-life of c, the spread and half-life of d, the noise) are not chosen:
they are fitted by maximum likelihood to the bank's own speech history (fit_filter.py) and stored in
data/filter_params.json. Everything else follows from them:
  - a fresh text dominates that voter's estimate; a single text is still treated as a noisy reading;
  - a silent voter's estimate drifts toward the committee's current view as the gap grows, at the speed
    the data imply, and its uncertainty widens;
  - a voter with no text yet is counted at the committee's view, with wide uncertainty;
  - only texts available on or before a day are used, so the index never looks ahead.

Tested against carry-forward on texts the parameters were not fitted on (pilot: stance_filter_pilot/):
better at the Fed and the BOE, no worse at the BOJ. A rate-change term was tested and rejected by a
likelihood-ratio test at all three banks, so the model has none.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import numpy as np

PARAMS = ("sd_c", "halflife_c", "sd_d", "halflife_d", "noise")
START_GUESS = {"sd_c": 12.0, "halflife_c": 180.0, "sd_d": 10.0, "halflife_d": 120.0, "noise": 8.0}
HOLD = 50.0


def load_params(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))["params"]


def _day(date: str) -> int:
    return dt.date.fromisoformat(date).toordinal()


def prepare(observations):
    """observations: iterable of (available_date, person, value, delivered_date).

    Texts by one person delivered on the same day are averaged into one reading, entering when the last of
    them became available. A text published after a newer one by the same person is dropped: it cannot
    replace the more recent stance (the same rule as the published member scores)."""
    groups = {}
    for available, person, value, delivered in observations:
        g = groups.setdefault((person, delivered), {"values": [], "available": available})
        g["values"].append(float(value))
        g["available"] = max(g["available"], available)
    rows = sorted((g["available"], delivered, person, sum(g["values"]) / len(g["values"]))
                  for (person, delivered), g in groups.items())
    kept, latest = [], {}
    for available, delivered, person, value in rows:
        if delivered < latest.get(person, ""):
            continue
        latest[person] = delivered
        kept.append((available, person, value))
    return kept


class Filter:
    def __init__(self, params: dict, people):
        self.p = params
        self.idx = {p: k + 1 for k, p in enumerate(people)}
        n = len(self.idx) + 1
        self.x = np.zeros(n)
        self.x[0] = HOLD
        self.P = np.diag(np.array([params["sd_c"] ** 2] + [params["sd_d"] ** 2] * (n - 1), dtype=float))
        self.phi_c = 0.5 ** (1 / params["halflife_c"])
        self.phi_d = 0.5 ** (1 / params["halflife_d"])

    def advance(self, days: int):
        if days <= 0:
            return
        fc, fd = self.phi_c ** days, self.phi_d ** days
        f = np.full(len(self.x), fd)
        f[0] = fc
        self.x[0] = HOLD + fc * (self.x[0] - HOLD)
        self.x[1:] *= fd
        self.P *= np.outer(f, f)
        q = np.full(len(self.x), self.p["sd_d"] ** 2 * (1 - fd ** 2))
        q[0] = self.p["sd_c"] ** 2 * (1 - fc ** 2)
        self.P[np.diag_indices_from(self.P)] += q

    def observe(self, person, y: float) -> tuple[float, float]:
        """Update with one reading; returns the prediction made just before it (mean, variance)."""
        i = self.idx[person]
        mean = self.x[0] + self.x[i]
        var = max(self.P[0, 0] + 2 * self.P[0, i] + self.P[i, i] + self.p["noise"] ** 2, 1e-9)
        ph = self.P[:, 0] + self.P[:, i]
        k = ph / var
        self.x += k * (y - mean)
        self.P -= np.outer(k, ph)
        return mean, var

    def stance(self, person) -> tuple[float, float]:
        i = self.idx[person]
        return float(self.x[0] + self.x[i]), float(math.sqrt(max(self.P[0, 0] + 2 * self.P[0, i] + self.P[i, i], 0.0)))

    def mean_of(self, people) -> tuple[float, float]:
        a = np.zeros(len(self.x))
        a[0] = 1.0
        for p in people:
            a[self.idx[p]] += 1 / len(people)
        return float(a @ self.x), float(math.sqrt(max(a @ self.P @ a, 0.0)))


def daily(observations, days, voters_on, params: dict):
    """One row per day in `days` (sorted ISO dates) on which anyone votes:
    {'date', 'index', 'index_sd', 'disagreement', 'members': {person: (estimate, sd)}}.
    `observations` come from prepare(); `voters_on(day)` lists that day's voters."""
    on = {d: list(dict.fromkeys(voters_on(d))) for d in days}
    people = sorted({p for _, p, _ in observations} | {p for v in on.values() for p in v})
    f = Filter(params, people)
    events = sorted([(_day(d), 0, d, p, y) for d, p, y in observations] + [(_day(d), 1, d, None, None) for d in days],
                    key=lambda e: (e[0], e[1]))
    t = events[0][0] if events else 0
    rows = []
    for n, kind, d, p, y in events:
        f.advance(n - t)
        t = n
        if kind == 0:
            f.observe(p, y)
        elif on[d]:
            members = {v: f.stance(v) for v in on[d]}
            index, index_sd = f.mean_of(on[d])
            estimates = [m for m, _ in members.values()]
            mean = sum(estimates) / len(estimates)
            rows.append({"date": d, "index": index, "index_sd": index_sd, "members": members,
                         "disagreement": math.sqrt(sum((e - mean) ** 2 for e in estimates) / len(estimates))})
    return rows


# ---------------------------------------------------------------- fitting (fit_filter.py)

def log_likelihood(observations, params: dict, until: str | None = None, on_reading=None) -> float:
    """Log-likelihood of the readings (before `until`), each predicted from the readings before it."""
    people = sorted({p for _, p, _ in observations})
    f = Filter(params, people)
    t = _day(observations[0][0])
    ll = 0.0
    for d, p, y in observations:
        n = _day(d)
        f.advance(n - t)
        t = n
        mean, var = f.observe(p, y)
        if until is None or d < until:
            ll += -0.5 * (math.log(2 * math.pi * var) + (y - mean) ** 2 / var)
        if on_reading:
            on_reading(d, p, y, mean)
    return ll


def _nelder_mead(fun, x0, step=0.4, iters=800, tol=1e-5):
    n = len(x0)
    pts = [np.array(x0, float)] + [np.array(x0, float) + step * np.eye(n)[i] for i in range(n)]
    vals = [fun(p) for p in pts]
    for _ in range(iters):
        order = np.argsort(vals)
        pts, vals = [pts[i] for i in order], [vals[i] for i in order]
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


def fit(observations, until: str | None = None) -> tuple[dict, float]:
    """Maximum-likelihood parameters (fitted in logs, so all stay positive)."""
    as_params = lambda z: dict(zip(PARAMS, (math.exp(v) for v in z)))
    nll = lambda z: -log_likelihood(observations, as_params(z), until=until)
    z, v = _nelder_mead(nll, [math.log(START_GUESS[k]) for k in PARAMS])
    z, v = _nelder_mead(nll, z, step=0.15)        # restart at the optimum so it has settled
    return as_params(z), -v


def prediction_test(observations, params: dict, start: str) -> dict:
    """Each reading from `start` on, predicted before it is seen: filter vs carry-forward (the person's
    previous reading). Only readings that have a previous one count, so carry-forward always has a guess."""
    last, rows = {}, []
    def on_reading(d, p, y, mean):
        if d >= start and p in last:
            rows.append((y - mean, y - last[p]))
        last[p] = y
    log_likelihood(observations, params, on_reading=on_reading)
    if not rows:
        return {"n": 0}
    e_f = np.array([r[0] for r in rows]); e_c = np.array([r[1] for r in rows])
    return {"n": len(rows),
            "mae_filter": round(float(np.mean(abs(e_f))), 2), "mae_carry_forward": round(float(np.mean(abs(e_c))), 2),
            "rmse_filter": round(float(np.sqrt(np.mean(e_f ** 2))), 2),
            "rmse_carry_forward": round(float(np.sqrt(np.mean(e_c ** 2))), 2)}
