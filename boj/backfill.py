"""Backfill the complete source-supported membership interval (no arbitrary year cutoff)."""
import sys
import update
if __name__ == '__main__':
    sys.argv.append('--full')
    raise SystemExit(update.main())
