"""Rebuild the daily index CSVs in output/ from the stored speeches, scores and voter history.

On each day: take the FOMC voters on that day (data/voters.csv) and estimate each one's stance from every
policy-relevant speech up to that day with the Kalman filter in stance_filter.py (its parameters are
fitted to this bank's speech history and stored in data/filter_params.json; refit with fit_filter.py).
The index is the equal-weight mean of those estimates; disagreement is their standard deviation. A
voter with no speech yet counts at the committee's estimated view. The series starts on
config.START_DATE.

  fed_speech_index.csv          mean of voters' estimated stances (50 = hold; below 50 = pressure to ease, above 50 = pressure to tighten)
  fed_speech_index_band.csv     95% band around the index (date, low, high)
  fed_speech_disagreement.csv   standard deviation of the voters' estimated stances
  fed_member_scores.csv         each voter on each day, for member charts and the current-score widget

The index and disagreement files have two columns, date and index, one row per calendar day through today.
fed_member_scores.csv has one row per voter per day: date, member, score, level, as_of, estimate,
estimate_sd. score is the voter's latest policy-relevant speech on or before that day and as_of its date,
so a stale stance shows; estimate (and its standard deviation) is the filtered stance that enters the
index. The current values of each member are the rows for the last date.
"""
from __future__ import annotations

import bisect
import csv
import datetime as dt
import statistics
import sys

import config
import prompt
import stance_filter
import store
import voters

FILTER_PARAMS = config.DATA / "filter_params.json"


def policy_speeches() -> list[dict]:
    scores = store.load_scores()
    rows = []
    for s in store.load_speeches():
        sc = scores.get(s["id"])
        if sc and sc["policy_relevant"] and sc["level"] is not None:
            rows.append({"date": s["date"], "id": s["id"], "key": voters.name_key(s["speaker"]),
                         "value": prompt.level_to_value(sc["level"]), "level": sc["level"]})
    rows.sort(key=lambda r: (r["date"], r["id"]))
    return rows


def same_day_mean(speeches: list[dict]) -> list[dict]:
    """One stance per delivery day: several policy texts on the same day are averaged (level kept only if they agree)."""
    days: dict[str, list[dict]] = {}
    for s in speeches:
        days.setdefault(s["date"], []).append(s)
    merged = []
    for date, group in sorted(days.items()):
        levels = {s.get("level") for s in group}
        merged.append({**group[-1], "value": statistics.fmean(s["value"] for s in group),
                       "level": levels.pop() if len(levels) == 1 else ""})
    return merged


class Stances:
    """Each voter's most recent score on a given date."""

    def __init__(self, speeches: list[dict]):
        self.speeches = speeches
        self.by_voter: dict[tuple[str, str], tuple[list[str], list[float]]] = {}

    def latest_speech(self, voter: tuple[str, str], date: str) -> dict | None:
        if voter not in self.by_voter:
            mine = same_day_mean([s for s in self.speeches if voters.same_person(s["key"], voter)])
            self.by_voter[voter] = ([s["date"] for s in mine], mine)
        dates, mine = self.by_voter[voter]
        i = bisect.bisect_right(dates, date) - 1
        return mine[i] if i >= 0 else None

    def latest(self, voter: tuple[str, str], date: str) -> float | None:
        speech = self.latest_speech(voter, date)
        return speech["value"] if speech else None


def start_day(stances: Stances, roster: voters.Roster, first: str, today: dt.date) -> dt.date | None:
    """The first day on which every voter has a scored stance (config.START_DATE overrides it). After that
    day every day is published, with voters who have not spoken yet left out."""
    if config.START_DATE:
        return dt.date.fromisoformat(config.START_DATE)
    day = dt.date.fromisoformat(first)
    while day <= today:
        d = day.isoformat()
        on = roster.on(d)
        if on and all(stances.latest(k, d) is not None for k in on):
            return day
        day += dt.timedelta(days=1)
    return None


def person_ids(keys):
    """One id per person: old minutes give surnames only, so a surname-only key takes the person's initial
    when exactly one person with that surname is known."""
    initials: dict[str, set[str]] = {}
    for surname, initial in keys:
        if initial:
            initials.setdefault(surname, set()).add(initial)
    def pid(key):
        surname, initial = key
        if not initial and len(initials.get(surname, ())) == 1:
            initial = next(iter(initials[surname]))
        return f"{surname}|{initial}"
    return pid


def filter_observations(speeches: list[dict], roster: voters.Roster):
    """(pid, readings for stance_filter): one reading per person per delivery day."""
    pid = person_ids(set(roster.ever()) | {s["key"] for s in speeches})
    return pid, stance_filter.prepare((s["date"], pid(s["key"]), s["value"], s["date"]) for s in speeches)


def filtered_days(speeches: list[dict], roster: voters.Roster, today: dt.date, params: dict | None = None):
    """(pid, stance_filter.daily rows) from the start day through today."""
    if not speeches:
        return None, []
    day = start_day(Stances(speeches), roster, speeches[0]["date"], today)
    days = []
    while day and day <= today:
        days.append(day.isoformat())
        day += dt.timedelta(days=1)
    pid, observations = filter_observations(speeches, roster)
    params = params or stance_filter.load_params(FILTER_PARAMS)
    return pid, stance_filter.daily(observations, days, lambda d: [pid(k) for k in roster.on(d)], params)


def build(speeches: list[dict], roster: voters.Roster, today: dt.date, params: dict | None = None) -> dict[str, list[tuple[str, float]]]:
    _, rows = filtered_days(speeches, roster, today, params)
    return {"fed_speech_index": [(r["date"], r["index"]) for r in rows],
            "fed_speech_disagreement": [(r["date"], r["disagreement"]) for r in rows],
            "fed_speech_index_band": [(r["date"], r["index"] - 1.96 * r["index_sd"], r["index"] + 1.96 * r["index_sd"]) for r in rows]}


def member_names() -> dict[tuple[str, str], str]:
    """Name to show for each voter key: the fullest spelling any roster uses ("Neel Kashkari", not "Kashkari")."""
    best: dict[tuple[str, str], str] = {}
    for row in voters.load():
        for name in row["names"]:
            key = voters.name_key(name)
            if len(name) > len(best.get(key, "")):
                best[key] = name
    return best


def build_members(speeches: list[dict], roster: voters.Roster, today: dt.date, params: dict | None = None) -> list[tuple]:
    """(date, member, score, level, as_of, estimate, estimate_sd) for every voter on every day. score,
    level and as_of are blank until the voter's first scored speech; the estimate is always there."""
    pid, days = filtered_days(speeches, roster, today, params)
    rows = []
    if not days:
        return rows
    stances = Stances(speeches)
    names = member_names()
    for row in days:
        d = row["date"]
        for key in dict.fromkeys(roster.on(d)):
            speech = stances.latest_speech(key, d)
            estimate, sd = row["members"][pid(key)]
            rows.append((d, names.get(key, " ".join(key).strip()), speech["value"] if speech else "",
                         speech["level"] if speech else "", speech["date"] if speech else "", estimate, sd))
    return rows


def main() -> int:
    speeches = policy_speeches()
    series = build(speeches, voters.Roster(), dt.datetime.now(dt.timezone.utc).date())
    config.OUTPUT.mkdir(exist_ok=True)
    for name in ["fed_speech_index", "fed_speech_disagreement"]:
        with (config.OUTPUT / f"{name}.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "index"])
            w.writerows((d, f"{v:.2f}") for d, v in series[name])
    with (config.OUTPUT / "fed_speech_index_band.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "low", "high"])
        w.writerows((d, f"{lo:.2f}", f"{hi:.2f}") for d, lo, hi in series["fed_speech_index_band"])
    today = dt.datetime.now(dt.timezone.utc).date()
    members = build_members(speeches, voters.Roster(), today)
    with (config.OUTPUT / "fed_member_scores.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "member", "score", "level", "as_of", "estimate", "estimate_sd"])
        w.writerows((d, m, f"{v:.2f}" if v != "" else "", lv, a, f"{e:.2f}", f"{sd:.2f}") for d, m, v, lv, a, e, sd in members)
    span = f"{series['fed_speech_index'][0][0]} to {series['fed_speech_index'][-1][0]}" if series["fed_speech_index"] else "no data yet"
    print(f"Built indexes from {len(speeches)} policy speeches ({span}; " + ("starts on config.START_DATE)." if config.START_DATE else "starts on the first day every voter has a score)."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
