"""Check (not published): how many stored and scored policy speeches each FOMC voter has, by year.

Use it to see which voters have no text speeches in a year (for example, presidents whose
appearances are mostly Q&A or video) and to choose config.START_DATE.

    python coverage.py              # every year
    python coverage.py 2020 2026    # a range of years
"""
from __future__ import annotations

import sys

import store
import voters


def main() -> int:
    first, last = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) == 3 else (1996, 9999)
    rows = voters.load()
    scores = store.load_scores()
    speeches = [(s["date"][:4], voters.name_key(s["speaker"]), s["id"]) for s in store.load_speeches()]
    years = sorted({r["date"][:4] for r in rows if first <= int(r["date"][:4]) <= last})
    for year in years:
        names = []
        for r in rows:
            if r["date"][:4] == year:
                names += [n for n in r["names"] if voters.name_key(n) not in map(voters.name_key, names)]
        print(f"\n{year}  (voter: speeches stored / policy-relevant scored)")
        for n in names:
            k = voters.name_key(n)
            mine = [sid for y, key, sid in speeches if y == year and voters.same_person(key, k)]
            policy = sum(1 for sid in mine if sid in scores and scores[sid]["policy_relevant"])
            flag = "   <- none" if not mine else ""
            print(f"  {n:28s} {len(mine):3d} / {policy:3d}{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
