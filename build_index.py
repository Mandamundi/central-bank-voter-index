"""Rebuild the two daily index CSVs in output/ from the stored speeches, scores and voter history.

On each day: take the FOMC voters on that day (data/voters.csv), give each voter the score of their
most recent policy-relevant speech delivered on or before that day, and average those scores
with equal weight per voter. Voters with no scored speech yet are left out. Disagreement is the
standard deviation of the same voters' scores.

  fed_speech_index.csv          mean of voters' latest scores (0 = emergency easing, 100 = aggressive tightening)
  fed_speech_disagreement.csv   standard deviation of those scores

Each file has two columns, date and index, one row per calendar day through today.
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
                         "value": prompt.level_to_value(sc["level"])})
    rows.sort(key=lambda r: (r["date"], r["id"]))
    return rows


class Stances:
    """Each voter's most recent score on a given date."""

    def __init__(self, speeches: list[dict]):
        self.speeches = speeches
        self.by_voter: dict[tuple[str, str], tuple[list[str], list[float]]] = {}

    def latest(self, voter: tuple[str, str], date: str) -> float | None:
        if voter not in self.by_voter:
            mine = [s for s in self.speeches if voters.same_person(s["key"], voter)]
            self.by_voter[voter] = ([s["date"] for s in mine], [s["value"] for s in mine])
        dates, values = self.by_voter[voter]
        i = bisect.bisect_right(dates, date) - 1
        return values[i] if i >= 0 else None


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


def main() -> int:
    speeches = policy_speeches()
    series = build(speeches, voters.Roster(), dt.datetime.now(dt.timezone.utc).date())
    config.OUTPUT.mkdir(exist_ok=True)
    for name, rows in series.items():
        with (config.OUTPUT / f"{name}.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "index"])
            w.writerows((d, f"{v:.2f}") for d, v in rows)
    span = f"{series['fed_speech_index'][0][0]} to {series['fed_speech_index'][-1][0]}" if series["fed_speech_index"] else "no data yet"
    print(f"Built indexes from {len(speeches)} policy speeches ({span}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
