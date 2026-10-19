"""Rebuild the two daily index CSVs in output/ from the stored speeches, scores and voter history.

On each day: take the FOMC voters on that day (data/voters.csv), give each voter the score of their
most recent policy-relevant speech delivered on or before that day (several on that same day are
averaged), and average those scores
with equal weight per voter. Voters with no scored speech yet are left out. Disagreement is the
standard deviation of the same voters' scores.

  fed_speech_index.csv          mean of voters' latest scores (0 = emergency easing, 100 = aggressive tightening)
  fed_speech_disagreement.csv   standard deviation of those scores
  fed_member_scores.csv         each voter's score on each day, for member charts and the current-score widget

The two index files have two columns, date and index, one row per calendar day through today.
fed_member_scores.csv has one row per voter per day: date, member, score, level, as_of. Only voters
on that day appear, with the score of their latest policy-relevant speech on or before it (the same
value that enters the average); as_of is that speech's date, so a stale stance shows. The current
score of each member is the rows for the last date.
"""
from __future__ import annotations

import bisect
import csv
import datetime as dt
import statistics
import sys

import config
import prompt
import store
import voters


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


def build(speeches: list[dict], roster: voters.Roster, today: dt.date) -> dict[str, list[tuple[str, float]]]:
    series = {"fed_speech_index": [], "fed_speech_disagreement": []}
    if not speeches:
        return series
    stances = Stances(speeches)
    day = dt.date.fromisoformat(config.START_DATE or speeches[0]["date"])
    while day <= today:
        d = day.isoformat()
        values = [v for v in (stances.latest(k, d) for k in roster.on(d)) if v is not None]
        if values:
            series["fed_speech_index"].append((d, statistics.fmean(values)))
            series["fed_speech_disagreement"].append((d, statistics.pstdev(values)))
        day += dt.timedelta(days=1)
    return series


def member_names() -> dict[tuple[str, str], str]:
    """Name to show for each voter key: the fullest spelling any roster uses ("Neel Kashkari", not "Kashkari")."""
    best: dict[tuple[str, str], str] = {}
    for row in voters.load():
        for name in row["names"]:
            key = voters.name_key(name)
            if len(name) > len(best.get(key, "")):
                best[key] = name
    return best


def build_members(speeches: list[dict], roster: voters.Roster, today: dt.date) -> list[tuple]:
    """(date, member, score, level, as_of) for every voter on every day that voter has a scored speech."""
    rows = []
    if not speeches:
        return rows
    stances = Stances(speeches)
    names = member_names()
    day = dt.date.fromisoformat(config.START_DATE or speeches[0]["date"])
    while day <= today:
        d = day.isoformat()
        for key in dict.fromkeys(roster.on(d)):
            speech = stances.latest_speech(key, d)
            if speech:
                rows.append((d, names.get(key, " ".join(key).strip()), speech["value"], speech["level"], speech["date"]))
        day += dt.timedelta(days=1)
    return rows


def main() -> int:
    speeches = policy_speeches()
    series = build(speeches, voters.Roster(), dt.datetime.now(dt.timezone.utc).date())
    config.OUTPUT.mkdir(exist_ok=True)
    for name, rows in series.items():
        with (config.OUTPUT / f"{name}.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "index"])
            w.writerows((d, f"{v:.2f}") for d, v in rows)
    today = dt.datetime.now(dt.timezone.utc).date()
    members = build_members(speeches, voters.Roster(), today)
    with (config.OUTPUT / "fed_member_scores.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "member", "score", "level", "as_of"])
        w.writerows((d, m, f"{v:.2f}", lv, a) for d, m, v, lv, a in members)
    span = f"{series['fed_speech_index'][0][0]} to {series['fed_speech_index'][-1][0]}" if series["fed_speech_index"] else "no data yet"
    print(f"Built indexes from {len(speeches)} policy speeches ({span}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
