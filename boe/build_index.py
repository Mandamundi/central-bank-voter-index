"""Rebuild the daily index CSVs in output/ from the stored speeches, scores and membership terms.

Same method as the Fed project. On each day, each voter's stance is estimated from every
policy-relevant speech available by that day with the Kalman filter in stance_filter.py (parameters
fitted to this bank's speech history, stored in data/filter_params.json; refit with fit_filter.py).
The index is the equal-weight mean of those estimates over the day's voters, with a 95% band;
disagreement is their standard deviation. The series starts on the first day every voter has a scored
speech; after that, a voter with no scored speech yet counts at the committee's estimated view and is
listed in the coverage file. A later publication of an older speech cannot replace a newer stance.

The member file keeps each voter's latest speech score (its date and age shown, so a stale stance
shows) next to the filtered estimate that enters the index. A new membership term does not inherit a
previous term's latest score there; in the filter, an earlier term's speeches still count, faded by
the time since.

Checks on the data (roster, collection, scoring backlog) are reported as warnings in
output/status.json; they never blank the index.
"""
from __future__ import annotations
import argparse
import bisect
import csv
import datetime as dt
import json
import statistics
from zoneinfo import ZoneInfo
import config
import prompt
import stance_filter
import store
import voters

FILTER_PARAMS = config.DATA / 'filter_params.json'


def policy_speeches():
    scores = store.load_scores()
    roster = voters.Roster()
    result = []
    for speech in store.load_speeches():
        score = scores.get(speech['id'])
        if not score or not roster.is_voter(speech['speaker'], speech['date']):
            continue
        if score['policy_relevant'] and score['level'] in prompt.LEVELS:
            result.append({'id': speech['id'], 'date': speech['date'],
                'available_date': speech.get('available_date') or speech['date'],
                'key': voters.name_key(speech['speaker']), 'value': prompt.level_to_value(score['level']),
                'level': score['level'],
                'availability_basis': speech.get('availability_basis', 'unknown')})
    return sorted(result, key=lambda r: (r['available_date'], r['date'], r['id']))


class Stances:
    def __init__(self, speeches):
        self.by_member = {}
        for row in speeches:
            self.by_member.setdefault(row['key'], []).append(row)
        for rows in self.by_member.values():
            rows.sort(key=lambda r: (r.get('available_date', r['date']), r['date'], r['id']))

    def latest_speech(self, voter, day, term_start='0001-01-01'):
        mine = [r for r in self.by_member.get(voter, [])
                if term_start <= r['date'] <= day and r.get('available_date', r['date']) <= day]
        if not mine:
            return None
        # Late publication of an older speech must not overwrite a more recent delivered stance.
        latest_delivery = max(r['date'] for r in mine)
        selected = [r for r in mine if r['date'] == latest_delivery]
        values = [r['value'] for r in selected]
        levels = {r['level'] for r in selected}
        return {'value': statistics.fmean(values), 'level': next(iter(levels)) if len(levels) == 1 else '',
                'date': latest_delivery, 'ids': '|'.join(r['id'] for r in selected),
                'available_date': max(r.get('available_date', r['date']) for r in selected),
                'availability_basis': '|'.join(sorted({r.get('availability_basis', 'unknown') for r in selected}))}

    def latest(self, voter, date):
        row = self.latest_speech(voter, date)
        return row['value'] if row else None


def start_day(stances, roster, today):
    """The first day on which every voter has a scored stance (config.START_DATE overrides it). After that
    day every day is published, with voters who have not spoken yet left out."""
    if config.START_DATE:
        if config.START_DATE < roster.coverage_start():
            raise ValueError('Index start predates complete verified membership coverage')
        return dt.date.fromisoformat(config.START_DATE)
    day = dt.date.fromisoformat(roster.coverage_start())
    while day <= today:
        date = day.isoformat()
        terms = roster.active_terms(date)
        if terms and all(stances.latest_speech(voters.raw_key(t['name']), date, t['start_date']) for t in terms):
            return day
        day += dt.timedelta(days=1)
    return None


def filter_observations(speeches, roster=None):
    """(pid, readings for stance_filter): a speech enters on the day it became available."""
    pid = lambda key: '|'.join(key)
    return pid, stance_filter.prepare((s['available_date'], pid(s['key']), s['value'], s['date']) for s in speeches)


def build_daily(speeches, roster, today, params=None):
    stances = Stances(speeches)
    day = start_day(stances, roster, today)
    days = []
    while day and day <= today:
        days.append(day.isoformat())
        day += dt.timedelta(days=1)
    pid, observations = filter_observations(speeches)
    filtered = {}
    if days:
        params = params or stance_filter.load_params(FILTER_PARAMS)
        voters_on = lambda d: [pid(voters.raw_key(t['name'])) for t in roster.active_terms(d)]
        filtered = {r['date']: r for r in stance_filter.daily(observations, days, voters_on, params)}
    coverage, members = [], []
    for date in days:
        day = dt.date.fromisoformat(date)
        terms = roster.active_terms(date)
        row = filtered.get(date)
        values, missing, stale = [], [], []
        for term in terms:
            speech = stances.latest_speech(voters.raw_key(term['name']), date, term['start_date'])
            estimate, estimate_sd = row['members'][pid(voters.raw_key(term['name']))] if row else ('', '')
            member = {'date': date, 'member': term['name'], 'score': '', 'level': '', 'as_of': '',
                      'available_date': '', 'age_days': '', 'speech_ids': '', 'availability_basis': '',
                      'estimate': round(estimate, 6) if row else '', 'estimate_sd': round(estimate_sd, 6) if row else ''}
            if speech:
                age = (day - dt.date.fromisoformat(speech['date'])).days
                values.append(speech['value'])
                if age > config.STALE_STANCE_WARNING_DAYS:
                    stale.append(term['name'])
                member.update(score=round(speech['value'], 6), level=speech['level'], as_of=speech['date'],
                    available_date=speech['available_date'], age_days=age, speech_ids=speech['ids'],
                    availability_basis=speech['availability_basis'])
            else:
                missing.append(term['name'])
            members.append(member)
        coverage.append({'date': date, 'index': row['index'] if row else '',
            'disagreement': row['disagreement'] if row else '',
            'index_low': row['index'] - 1.96 * row['index_sd'] if row else '',
            'index_high': row['index'] + 1.96 * row['index_sd'] if row else '',
            'active_voters': len(terms),
            'scored_voters': len(values), 'coverage': len(values)/len(terms) if terms else 0,
            'missing_members': '|'.join(missing), 'stale_members': '|'.join(stale)})
    return coverage, members


def build(speeches, roster, today):
    rows, _ = build_daily(speeches, roster, today)
    return {f'{config.BANK}_speech_index': [(r['date'], r['index']) for r in rows],
            f'{config.BANK}_speech_disagreement': [(r['date'], r['disagreement']) for r in rows]}


def build_members(speeches, roster, today):
    return build_daily(speeches, roster, today)[1]


def warnings(today):
    problems = []
    for name, label in [('roster_check.json','Official roster'), ('policy_check.json','Policy context')]:
        path = config.DATA / name
        if not path.exists() or json.loads(path.read_text()).get('checked_on') != today:
            problems.append(f'{label} has not been verified today')
    audit_path = config.DATA / 'membership_audit.json'
    audit = json.loads(audit_path.read_text()) if audit_path.exists() else {}
    if not audit.get('success') or audit.get('ledger_hash') != store.content_hash(config.VOTERS.read_text()):
        problems.append('Membership ledger has not passed its official historical-vote audit')
    path = config.DATA / 'last_run.json'
    if not path.exists() or not json.loads(path.read_text()).get('success'):
        problems.append('Collector has unresolved failures or has never completed')
    elif json.loads(path.read_text()).get('completed_at', '')[:10] != today:
        problems.append('Collector has not completed today')
    integrity_path = config.DATA / 'integrity_audit.json'
    integrity = json.loads(integrity_path.read_text()) if integrity_path.exists() else {}
    if not integrity.get('success') or integrity.get('corpus_hash') != store.corpus_hash():
        problems.append('Speech store has not passed its current integrity audit')
    scores = store.load_scores()
    unscored = [s['id'] for s in store.load_speeches() if s['id'] not in scores]
    if unscored:
        problems.append(f'{len(unscored)} speeches remain unscored for this content/prompt vintage')
    return problems


def write_csv(path, rows, fields):
    temporary = path.with_suffix('.csv.tmp')
    with temporary.open('w', encoding='utf-8', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    today = dt.datetime.now(ZoneInfo(config.BANK_TIMEZONE)).date()
    rows, members = build_daily(policy_speeches(), voters.Roster(), today)
    problems = warnings(dt.datetime.now(dt.timezone.utc).date().isoformat())
    if rows and rows[-1]['missing_members']:
        problems.append(f"Voters without a scored speech, counted at the committee's estimated view: {rows[-1]['missing_members'].replace('|', ', ')}")
    config.OUTPUT.mkdir(exist_ok=True)
    write_csv(config.OUTPUT / f'{config.BANK}_coverage.csv', rows,
              ['date','index','disagreement','index_low','index_high','active_voters','scored_voters','coverage',
               'missing_members','stale_members'])
    write_csv(config.OUTPUT / f'{config.BANK}_member_scores.csv', members,
              ['date','member','score','level','as_of','available_date','age_days','speech_ids','availability_basis',
               'estimate','estimate_sd'])
    write_csv(config.OUTPUT / f'{config.BANK}_speech_index_band.csv',
              [{'date': r['date'], 'low': f"{r['index_low']:.2f}", 'high': f"{r['index_high']:.2f}"} for r in rows if r['index'] != ''],
              ['date','low','high'])
    for suffix, key in [('speech_index','index'), ('speech_disagreement','disagreement')]:
        write_csv(config.OUTPUT / f'{config.BANK}_{suffix}.csv',
                  [{'date': r['date'], 'index': f'{r[key]:.2f}'} for r in rows if r[key] != ''],
                  ['date','index'])
    store.atomic_json(config.OUTPUT / 'status.json', {'built_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'bank': config.BANK, 'warnings': problems,
        'membership_coverage_start': voters.Roster().coverage_start(),
        'latest': rows[-1] if rows else None, 'scorer_version': config.SCORER_VERSION,
        'historical_availability': 'Official publication date when known; otherwise labelled delivery-date proxy. Not a vintage-perfect trading backtest.'})
    print(f'{config.BANK}: {len(rows)} daily rows' + (f' from {rows[0]["date"]} (first day every voter has a score)' if rows else '')
          + ''.join(f'\n  warning: {p}' for p in problems))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
