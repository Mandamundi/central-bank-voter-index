"""The Bank Rate on a date, from the official change history (data/policy_rates.csv).

The scorer sees it as one sentence, like the Fed's "federal funds target range". A rate decided after
the speech date is never used.
"""
from __future__ import annotations

import bisect
import csv

import config

UNKNOWN = 'unknown (no Bank Rate is recorded on or before this date).'


def records() -> list[dict]:
    if not config.POLICY_RATES.exists():
        return []
    with config.POLICY_RATES.open(encoding='utf-8', newline='') as file:
        return sorted(csv.DictReader(file), key=lambda r: r['date'])


def context_for(day: str) -> str:
    rows = records()
    index = bisect.bisect_right([r['date'] for r in rows], day) - 1
    return f"Bank Rate {rows[index]['level']}%." if index >= 0 else UNKNOWN
