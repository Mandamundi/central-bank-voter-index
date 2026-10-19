"""Check (not published): speeches stored and scored per voter per year, and every URL's outcome.

Writes data/coverage_audit.json and prints the current voters' counts.

    python coverage.py
"""
from __future__ import annotations

from collections import Counter
import datetime as dt

import config
import store
import voters


def member_years(roster, speeches, scores) -> list[dict]:
    rows = []
    for year in range(int(roster.coverage_start()[:4]), dt.date.today().year + 1):
        for term in roster.terms:
            if term['start_date'] > f'{year}-12-31' or (term['end_date'] and term['end_date'] < f'{year}-01-01'):
                continue
            mine = [s for s in speeches if s['speaker'] == term['name'] and s['date'].startswith(str(year))]
            rows.append({
                'year': year,
                'member': term['name'],
                'stored': len(mine),
                'scored': sum(s['id'] in scores for s in mine),
                'policy_relevant': sum(bool(scores.get(s['id'], {}).get('policy_relevant')) for s in mine),
                'publication_date_unknown': sum(not s.get('published_date') for s in mine),
            })
    return rows


def current_members(roster, speeches, scores) -> list[dict]:
    rows = []
    for term in roster.active_terms(dt.date.today().isoformat()):
        mine = [s for s in speeches if s['speaker'] == term['name']]
        rows.append({
            'member': term['name'],
            'stored_speeches': len(mine),
            'latest_delivery': max((s['date'] for s in mine), default=None),
            'policy_scores': sum(bool(scores.get(s['id'], {}).get('policy_relevant')) for s in mine),
        })
    return rows


def main() -> int:
    roster = voters.Roster()
    speeches = store.load_speeches()
    scores = store.load_scores()
    outcomes = store.load_processing()
    current = current_members(roster, speeches, scores)
    payload = {
        'bank': config.BANK,
        'membership_coverage_start': roster.coverage_start(),
        'membership_terms': len(roster.terms),
        'stored_speeches': len(speeches),
        'valid_scores': len(scores),
        'discovery_outcomes': dict(Counter(r['status'] for r in outcomes.values())),
        'exclusion_reasons': dict(Counter(r.get('reason', '') for r in outcomes.values() if r['status'] == 'excluded')),
        'unresolved': [{'url': url, 'reason': r.get('reason', 'not yet attempted')}
                       for url, r in outcomes.items() if r['status'] in ('failure', 'pending')],
        'current_members': current,
        'member_years': member_years(roster, speeches, scores),
    }
    store.atomic_json(config.DATA / 'coverage_audit.json', payload)
    print(f"{config.BANK}: {len(speeches)} speeches, {len(scores)} scores; {payload['discovery_outcomes']}")
    for row in current:
        print(f"  {row['member']:25s} {row['stored_speeches']:3d} texts; {row['policy_scores']:3d} policy scores; "
              f"latest {row['latest_delivery'] or 'none'}")
    if payload['unresolved']:
        print(f"  {len(payload['unresolved'])} URLs unresolved (see data/coverage_audit.json)")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
