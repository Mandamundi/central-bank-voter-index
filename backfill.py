"""One-off: build the voter history from every FOMC minutes since 1996 and the speech store from
the full archive each source publishes. Run locally before deploying. Safe to stop and rerun:
pages already checked are skipped.

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default=",".join(sources.SOURCES), help="comma-separated source names")
    ap.add_argument("--skip-voters", action="store_true")
    args = ap.parse_args()

    update.refresh_fed_funds()
    if not args.skip_voters:
        voters.refresh(full=True)
    update.pull(args.sources.split(","), full=True)
    print(f"Speech store now holds {len(store.load_speeches())} speeches. Next: python score.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
