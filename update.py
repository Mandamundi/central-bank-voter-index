"""Daily update: refresh the target-rate history and the FOMC voter list, then pull new speeches
from the Board and the twelve Reserve Banks into the speech store. Run by the GitHub Action.

Each source's newest entries are checked (RSS where the site has a working feed, otherwise its
listing page); pages already checked are remembered in data/seen_urls.txt.

    python update.py
"""
from __future__ import annotations

import csv
import sys

import config
import sources
import store
import voters

SAVE_EVERY = 25


def refresh_fed_funds() -> None:
    try:
        history = sources.fetch_fed_funds_history()
    except Exception as e:  # keep the last good copy if federalreserve.gov is unreachable
        print(f"Fed funds history not refreshed ({e}); keeping existing file.")
        return
    if len(history) < 50:
        print("Fed funds history looks incomplete; keeping existing file.")
        return
    with config.FED_FUNDS.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "level"])
        w.writerows(history)
    print(f"Fed funds history: {len(history)} changes, latest {history[-1]}")


def pull(names: list[str], full: bool) -> int:
    """Read every unseen listed speech from the named sources. Returns the number of speeches added."""
    seen = store.load_seen_urls()
    total = 0
    for name in names:
        try:
            listed = [e for e in sources.SOURCES[name][1](full) if e["url"] not in seen]
        except Exception as e:
            print(f"{name}: listing failed ({e}); skipped this run")
            continue
        print(f"{name}: {len(listed)} new pages to check")
        new, skipped = [], 0
        for i, e in enumerate(listed, 1):
            try:
                record, why = sources.fetch(e)
            except Exception as ex:   # not marked seen, so it is retried next run
                print(f"  could not read {e['url']}: {str(ex)[:120]}")
                continue
            seen.add(e["url"])
            if record:
                new.append(record)
            else:
                skipped += 1
                if why.startswith("date or speaker"):
                    print(f"  skipped {e['url']}: {why}")
            if i % SAVE_EVERY == 0:      # save progress so a long backfill can be stopped and resumed
                total += store.add_speeches(new)
                new = []
                store.save_seen_urls(seen)
                print(f"  {i}/{len(listed)} checked")
        added = store.add_speeches(new)
        total += added
        store.save_seen_urls(seen)
        print(f"{name}: kept {added} speeches, skipped {skipped} pages (no speech text, not a speech, or already stored)")
    return total


def main() -> int:
    refresh_fed_funds()
    voters.refresh(full=False)
    added = pull(list(sources.SOURCES), full=False)
    print(f"Added {added} new speeches.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
