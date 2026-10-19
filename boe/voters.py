"""Source-backed, inclusive membership terms. Never infer appointment dates from speeches."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import unicodedata

import config

FIELDS = ['start_date', 'end_date', 'name', 'role', 'source', 'aliases']


def normalize(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', value)).strip()


def raw_key(name):
    value = normalize(name)
    value = re.sub(r'\b(?:Professor|Prof|Dr|Sir|Dame)\.?\s*', '', value, flags=re.I)
    parts = value.replace(',', ' ').split()
    if not parts:
        raise ValueError('Empty member name')
    if len(parts) > 1 and parts[0].isupper():
        parts = parts[1:] + parts[:1]
    clean = lambda s: re.sub(r"[^a-z'-]", '', s.casefold())
    return clean(parts[-1]), clean(' '.join(parts[:-1]))


def load_terms():
    with config.VOTERS.open(encoding='utf-8', newline='') as file:
        return sorted(csv.DictReader(file), key=lambda r: (r['start_date'], r['name']))


def resolve_speaker(label, rows=None):
    """Match a whole byline, never a surname occurring in someone else's text.

    BOJ proxy delivery: the author before （...代読） owns the speech.
    A one-character surname (増) is accepted only as an exact role-stripped byline.
    """
    rows = load_terms() if rows is None else rows
    label = normalize(label)
    label = re.split(r'[（(]', label, maxsplit=1)[0].strip()
    label = re.sub(r'(?:副総裁|総裁|審議委員|理事|様|氏)$', '', label).strip()
    matches = set()
    for row in rows:
        for alias in [row['name']] + row.get('aliases', '').split('|'):
            if not alias:
                continue
            if normalize(alias).casefold() == label.casefold():
                matches.add(row['name'])
            elif re.search('[a-zA-Z]', alias) and re.search('[a-zA-Z]', label) and raw_key(alias) == raw_key(label):
                matches.add(row['name'])
    if len(matches) > 1:
        raise ValueError(f'Ambiguous member byline: {label!r}: {sorted(matches)}')
    return next(iter(matches), None)


def name_key(name):
    return raw_key(resolve_speaker(name) or name)


def same_person(a, b):
    return a == b


class Roster:
    def __init__(self, rows=None):
        self.terms = load_terms() if rows is None else list(rows)
        self.validate()

    def validate(self):
        for row in self.terms:
            dt.date.fromisoformat(row['start_date'])
            if row['end_date']:
                dt.date.fromisoformat(row['end_date'])
                if row['end_date'] < row['start_date']:
                    raise ValueError(f'Reversed membership term: {row}')
            if not row['source']:
                raise ValueError(f'Membership has no source: {row["name"]}')
        for day in self.events():
            active = self.active_terms(day)
            if len(active) != len({raw_key(r['name']) for r in active}):
                raise ValueError(f'Overlapping membership terms on {day}')
            if len(active) > 9:
                raise ValueError(f'More than nine voters on {day}')

    def events(self):
        days = {r['start_date'] for r in self.terms}
        days.update((dt.date.fromisoformat(r['end_date']) + dt.timedelta(days=1)).isoformat()
                    for r in self.terms if r['end_date'])
        return sorted(days)

    def coverage_start(self):
        """First complete committee in this ledger; not the archive's first available year."""
        for day in self.events():
            if len(self.active_terms(day)) == 9:
                return day
        raise ValueError('No complete nine-member committee in membership history')

    def active_terms(self, day):
        return [r for r in self.terms if r['start_date'] <= day and (not r['end_date'] or day <= r['end_date'])]

    def on(self, day):
        return [raw_key(r['name']) for r in self.active_terms(day)]

    def ever(self):
        return list(dict.fromkeys(raw_key(r['name']) for r in self.terms))

    def term_for(self, name, day):
        canonical = resolve_speaker(name, self.terms)
        return next((r for r in self.active_terms(day) if r['name'] == canonical), None)

    def is_voter(self, name, day):
        return self.term_for(name, day) is not None


def load():
    roster = Roster()
    return [{'date': day, 'source': 'verified membership terms',
             'names': [r['name'] for r in roster.active_terms(day)]} for day in roster.events()]


def check_current():
    import sources
    official = sources.current_voters()
    current = {raw_key(resolve_speaker(r['name']) or r['name']) for r in official}
    known = set(Roster().on(dt.date.today().isoformat()))
    if len(current) != 9:
        raise ValueError(f'Official roster returned {len(current)} members; expected nine')
    config.DATA.mkdir(parents=True, exist_ok=True)
    pending = config.DATA / 'pending_roster.json'
    if current != known:
        pending.write_text(json.dumps({'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'source': sources.MEMBERS_URL, 'official_members': official,
            'reason': 'Research each arrival/departure effective date and aliases before updating voters.csv.'},
            ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return False
    pending.unlink(missing_ok=True)
    (config.DATA / 'roster_check.json').write_text(json.dumps({
        'checked_on': dt.date.today().isoformat(), 'source': sources.MEMBERS_URL,
        'members': sorted([list(k) for k in known])}, indent=2) + '\n')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit-history', action='store_true')
    args = parser.parse_args()
    if not check_current():
        print('Roster mismatch: see data/pending_roster.json. Publication blocked; collection can continue.')
        return 2
    roster = Roster()
    print(f'Current nine voters verified; complete historical ledger begins {roster.coverage_start()}.')
    if args.audit_history:
        import history
        return history.main()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
