"""Atomic speech upserts and content-addressed scoring vintages, local to this compiler."""
from __future__ import annotations
import csv
import hashlib
import json
import os
import sys
import config

FIELDS = ['id', 'date', 'published_date', 'available_date', 'availability_basis', 'speaker', 'role', 'title', 'url',
          'source', 'language', 'format', 'document_urls', 'text_url', 'retrieved_at', 'content_hash', 'collector_version', 'text']
csv.field_size_limit(sys.maxsize)


def content_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def load_speeches():
    rows = []
    for path in sorted(config.SPEECH_DIR.glob('*.csv')):
        with path.open(encoding='utf-8', newline='') as file:
            rows.extend(csv.DictReader(file))
    return rows


def add_speeches(new):
    existing = {r['id']: r for r in load_speeches()}
    changed = {r['date'][:4] for r in new}
    added = sum(r['id'] not in existing for r in new)
    for row in new:
        if row['id'] in existing:
            changed.add(existing[row['id']]['date'][:4])
        existing[row['id']] = row
    config.SPEECH_DIR.mkdir(parents=True, exist_ok=True)
    for year in changed:
        path = config.SPEECH_DIR / f'{year}.csv'
        temporary = path.with_suffix('.csv.tmp')
        with temporary.open('w', encoding='utf-8', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(sorted((r for r in existing.values() if r['date'][:4] == year),
                                   key=lambda r: (r['date'], r['id'])))
        os.replace(temporary, path)
    return added


def score_input_hash(speech):
    # Metadata corrections change the prompt just as revised text does.
    fields = ('date', 'published_date', 'available_date', 'availability_basis', 'speaker', 'role', 'title', 'language', 'content_hash')
    import policy
    payload = {k: speech.get(k, '') for k in fields}
    payload['policy_context'] = policy.context_for(speech['date'])
    return content_hash(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def load_scores(scorer_version=None):
    version = scorer_version or config.SCORER_VERSION
    speeches = {r['id']: r for r in load_speeches()}
    scores = {}
    if config.SCORES.exists():
        for line in config.SCORES.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            speech = speeches.get(row['id'])
            if (row.get('scorer_version') == version and speech
                    and row.get('input_hash') == score_input_hash(speech)):
                scores[row['id']] = row
    return scores


def append_score(score):
    config.DATA.mkdir(parents=True, exist_ok=True)
    with config.SCORES.open('a', encoding='utf-8') as file:
        file.write(json.dumps(score, ensure_ascii=False) + '\n')
        file.flush()
        os.fsync(file.fileno())


def load_processing():
    return json.loads(config.PROCESSING.read_text()) if config.PROCESSING.exists() else {}


def save_processing(outcomes):
    atomic_json(config.PROCESSING, outcomes)


def quarantine(speech_id, reason):
    rows = load_speeches()
    record = next((r for r in rows if r['id'] == speech_id), None)
    if not record:
        return
    atomic_json(config.DATA / 'quarantine' / f'{speech_id}.json', {'reason': reason, 'record': record})
    path = config.SPEECH_DIR / (record['date'][:4] + '.csv')
    temporary = path.with_suffix('.csv.tmp')
    with temporary.open('w', encoding='utf-8', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(r for r in rows if r['date'][:4] == record['date'][:4] and r['id'] != speech_id)
    temporary.replace(path)


def reconcile_duplicates():
    """Repair legacy duplicate IDs and redirect URL checkpoints to one canonical record.

    Only identical author/date/document identities can merge. Conflicting attribution stays
    visible as an error rather than silently discarding another member's communication.
    """
    import documents
    groups = {}
    for row in load_speeches():
        key = documents.canonical_url(row.get('text_url') or row['url'])
        groups.setdefault(key, []).append(row)
    outcomes = load_processing()
    removed = 0
    for key, rows in groups.items():
        if len(rows) < 2:
            continue
        if len({(row['speaker'], row['date']) for row in rows}) != 1:
            raise ValueError('Conflicting authors/dates for shared source document: '+key)
        # Prefer a landing-page record with its deterministic URL ID over direct assets.
        rows.sort(key=lambda r: (r['url'].endswith('.pdf'),
                                r['id'] != hashlib.sha256(r['url'].encode()).hexdigest()[:20],
                                r['id']))
        keep = rows[0]
        for row in rows[1:]:
            quarantine(row['id'], 'Duplicate source document; retained '+keep['id'])
            for state in outcomes.values():
                if state.get('speech_id') == row['id']:
                    state['speech_id'] = keep['id']
            removed += 1
    if removed:
        save_processing(outcomes)
    return removed


def corpus_hash():
    return content_hash(json.dumps(sorted(load_speeches(), key=lambda r: r['id']),
                                   sort_keys=True, ensure_ascii=False))
