"""Check the speech store: attribution to a voter, dates, text hashes and duplicate documents.

Writes data/integrity_audit.json and exits nonzero if any record fails a check.

    python audit.py
"""
from __future__ import annotations

import collections
import datetime as dt
import json

import config
import rubric
import store
import voters


def main() -> int:
    rows = store.load_speeches()
    roster = voters.Roster()
    ids = {r['id'] for r in rows}
    issues = []
    for r in rows:
        if not roster.is_voter(r['speaker'], r['date']):
            issues.append(['nonvoter', r['id']])
        if r['available_date'] < r['date']:
            issues.append(['availability', r['id']])
        if len(r['text']) > rubric.MAX_TEXT_CHARS:
            issues.append(['oversize', r['id'], len(r['text'])])
        if store.content_hash(r['text']) != r['content_hash']:
            issues.append(['content_hash', r['id']])
    for field in ('id', 'text_url'):
        for value, count in collections.Counter(r[field] for r in rows).items():
            if count > 1:
                issues.append(['duplicate_' + field, value, count])
    for url, outcome in store.load_processing().items():
        if outcome['status'] in ('stored', 'duplicate') and outcome.get('speech_id') not in ids:
            issues.append(['dangling', url, outcome])
    payload = {
        'bank': config.BANK,
        'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'stored_speeches': len(rows),
        'earliest_delivery': min((r['date'] for r in rows), default=None),
        'latest_delivery': max((r['date'] for r in rows), default=None),
        'issues': issues,
        'success': not issues,
        'corpus_hash': store.corpus_hash(),
        'languages': dict(collections.Counter(r['language'] for r in rows)),
        'formats': dict(collections.Counter(r['format'] for r in rows)),
        'maximum_text_chars': max((len(r['text']) for r in rows), default=0),
    }
    store.atomic_json(config.DATA / 'integrity_audit.json', payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload['success'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
