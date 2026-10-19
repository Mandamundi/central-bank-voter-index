"""One-off: build the voter history from every FOMC minutes since 1996, the speech archive from
FRASER, then the Board and Reserve Bank archives for what FRASER does not hold (its current year,
and people with no FRASER collection, such as Kashkari's essays). Run locally before deploying.
Safe to stop and rerun: stored speeches and confident exclusions are skipped.

    python backfill.py                       # everything
    python backfill.py --sources ny,boston   # only these bank sources (FRASER still runs unless --skip-fraser)
    python backfill.py --skip-voters         # speeches only
"""
from __future__ import annotations

import argparse
import sys

import fraser
import http_get
import runlog
import sources
import store
import update
import voters


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default=",".join(sources.SOURCES), help="comma-separated bank source names")
    ap.add_argument("--skip-voters", action="store_true")
    ap.add_argument("--skip-rates", action="store_true", help="reuse saved rate history")
    ap.add_argument("--skip-fraser", action="store_true")
    ap.add_argument("--skip-banks", action="store_true")
    ap.add_argument("--recover", action="store_true", help="retry failed pages immediately")
    ap.add_argument("--limit", type=int, help="maximum pages to check per bank source")
    ap.add_argument("--verbose", action="store_true", help="show every request and page outcome in the terminal")
    args = ap.parse_args()
    names = [name.strip() for name in args.sources.split(",") if name.strip()]
    if not names or set(names) - sources.SOURCES.keys():
        ap.error("--sources must name known sources: " + ",".join(sources.SOURCES))
    if args.limit is not None and args.limit < 1:
        ap.error("--limit must be positive")

    with runlog.session(args.verbose):
        try:
            if not args.skip_rates:
                update.refresh_fed_funds()
            if not args.skip_voters:
                http_get.begin_source('voters')
                voters.refresh(full=True)
            if not args.skip_fraser:
                fraser.harvest()
            if not args.skip_banks:
                update.pull(names, full=True, recover=args.recover, limit=args.limit)
        except KeyboardInterrupt:
            print("Stopped by user. Completed collection work is saved; see the log for the interrupted stage.", flush=True)
            return 130
        print(f"Speech store now holds {len(store.load_speeches())} speeches. Next: python score.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
