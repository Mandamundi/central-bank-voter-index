"""One-off: build the voter history from every FOMC minutes since 1996 and the speech store from
the full archive each source publishes. Run locally before deploying. Safe to stop and rerun:
stored speeches and confident exclusions are skipped; old failed pages are recoverable.

    python backfill.py                       # everything
    python backfill.py --sources ny,boston   # only these sources
    python backfill.py --skip-voters         # speeches only
"""
from __future__ import annotations

import argparse
import sys

import sources
import store
import update
import voters
import runlog
import http_get


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default=",".join(sources.SOURCES), help="comma-separated source names")
    ap.add_argument("--skip-voters", action="store_true")
    ap.add_argument("--skip-rates", action="store_true", help="reuse saved rate history")
    ap.add_argument("--recover", action="store_true", help="retry failed pages immediately, including legacy skips")
    ap.add_argument("--limit", type=int, help="maximum pages to check per source")
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
            update.pull(names, full=True, recover=args.recover, limit=args.limit)
        except KeyboardInterrupt:
            print("Stopped by user. Completed collection work is saved; see the log for the interrupted stage.", flush=True)
            return 130
        print(f"Speech store now holds {len(store.load_speeches())} speeches. Next: python score.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
