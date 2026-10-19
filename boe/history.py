"""Audit the term ledger against every official MPC Bank Rate vote in its covered interval.

The workbook identifies actual meeting voters; first/last votes are not appointment dates.
Membership between meetings remains governed by sourced appointment/retirement terms.
"""
from __future__ import annotations
import datetime as dt
from io import BytesIO
from zipfile import ZipFile
from lxml import etree
import re
import config
import http_get
import store
import voters

URL = 'https://www.bankofengland.co.uk/-/media/boe/files/monetary-policy-summary-and-minutes/mpcvoting.xlsx'


def parse_votes(content):
    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with ZipFile(BytesIO(content)) as archive:
        strings = [''.join(node.itertext()) for node in etree.fromstring(archive.read('xl/sharedStrings.xml')).findall('s:si', ns)]
        rows = etree.fromstring(archive.read('xl/worksheets/sheet1.xml')).findall('.//s:row', ns)
        cells = []
        for row in rows:
            values = {}
            for cell in row:
                value = cell.findtext('s:v', namespaces=ns)
                if value is not None:
                    values[re.sub(r'\d', '', cell.get('r'))] = strings[int(value)] if cell.get('t') == 's' else value
            cells.append(values)
    headers = next((r for r in cells if r.get('C') == 'Current members'), None)
    if not headers:
        raise ValueError('Voting workbook member header changed')
    names = {column: ' '.join(name.split()) for column, name in headers.items()
             if column not in {'A','B','C','M'} and name not in {'Current members','Past members'}}
    result = []
    for row in cells:
        serial = row.get('B', '')
        if not re.fullmatch(r'\d+(?:\.0+)?', serial) or not 35000 <= float(serial) <= 90000:
            continue
        day = (dt.date(1899,12,30) + dt.timedelta(days=int(float(serial)))).isoformat()
        voting = [name for col,name in names.items() if col in row and re.fullmatch(r'-?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?', row[col])]
        if not 1 <= len(voting) <= 9:
            raise ValueError(f'Unexpected number of MPC votes on {day}: {voting}')
        result.append({'date': day, 'names': voting, 'source': URL})
    if len(result) < 200:
        raise ValueError(f'Official voting history unexpectedly short: {len(result)} meetings')
    return sorted(result, key=lambda r:r['date'])


def main():
    roster = voters.Roster()
    snapshots = parse_votes(http_get.get(URL).content)
    covered = [r for r in snapshots if roster.coverage_start() <= r['date'] <= dt.date.today().isoformat()]
    issues = []
    for row in covered:
        unknown = [name for name in row['names'] if not roster.is_voter(name, row['date'])]
        if unknown:
            issues.append({'date':row['date'], 'official_voters_missing_from_ledger':unknown})
    store.atomic_json(config.DATA / 'voter_snapshots.json', snapshots)
    store.atomic_json(config.DATA / 'membership_audit.json', {'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'source':URL, 'ledger_hash':store.content_hash(config.VOTERS.read_text()), 'coverage_start':roster.coverage_start(), 'meetings_checked':len(covered),
        'latest_meeting':covered[-1]['date'] if covered else None, 'issues':issues, 'success':bool(covered) and not issues})
    print(f'Audited {len(covered)} official MPC meetings; {len(issues)} membership mismatches.')
    return 2 if issues or not covered else 0


if __name__ == '__main__':
    raise SystemExit(main())
