"""Audit actual voting-member rosters in all published BOJ minutes for supported years."""
from __future__ import annotations
import datetime as dt
import json
import re
from urllib.parse import urljoin
import config
import documents as doc
import http_get
import store
import voters

BASE = 'https://www.boj.or.jp'


def parse_minutes(content, day):
    # Only this section votes. Government representatives and reporting staff follow it.
    match = re.search(r'Policy\s+Board\s+Members\s+Present(.*?)Government\s+Representatives\s+Present', content, re.S|re.I)
    if not match:
        raise ValueError('Official minutes voting-member section not found')
    names = re.findall(r'\b(?:Mr|Ms|Mrs)\.\s*([A-Z])\.\s*([A-Za-z]+)', match[1])
    if not names:
        # Newer minutes sometimes give full names instead of initials.
        names = [(given[0], family) for family,given in re.findall(r'\b([A-Z]{2,})\s+([A-Z][a-z]+)', match[1])]
    terms = voters.Roster().active_terms(day)
    mapped = []
    for initial,surname in names:
        candidates = [r['name'] for r in terms if r['name'].split()[0].casefold() == surname.casefold()
                      and r['name'].split()[1][0].casefold() == initial.casefold()]
        if len(candidates) != 1:
            raise ValueError(f'Cannot uniquely resolve actual voter {initial}. {surname} on {day}')
        mapped.append(candidates[0])
    if len(set(mapped)) != len(mapped) or not 7 <= len(mapped) <= 9:
        raise ValueError(f'Unexpected BOJ voting roster on {day}: {mapped}')
    return mapped


def main():
    roster = voters.Roster()
    start = roster.coverage_start()
    path = config.DATA / 'voter_snapshots.json'
    snapshots = {r['source']:r for r in json.loads(path.read_text())} if path.exists() else {}
    issues = []
    for year in range(int(start[:4]), dt.date.today().year+1):
        url = BASE + f'/en/mopo/mpmsche_minu/minu_{year}/index.htm'
        try:
            page = doc.tree(http_get.get(url).content)
            links = {}
            for anchor in page.xpath('//main//a[@href]'):
                link = urljoin(url,anchor.get('href'))
                match = re.search(r'/g(\d{2})(\d{2})(\d{2})\.(?:pdf|htm)$',link)
                if match:
                    day = f'20{match[1]}-{match[2]}-{match[3]}'
                    if start <= day <= dt.date.today().isoformat():
                        links[link]=day
            if not links:
                raise ValueError(f'No supported meeting minutes in {year} listing')
            for link,day in links.items():
                if link in snapshots:
                    # Recheck mapping on every audit in case the ledger was edited.
                    for name in snapshots[link]['names']:
                        if not roster.is_voter(name,day):
                            raise ValueError(f'Ledger no longer includes {name} on {day}')
                    continue
                response=http_get.get(link)
                content=doc.pdf_text(response,minimum_chars=100) if doc.is_pdf(response,link) else doc.text(doc.tree(response.content))
                snapshots[link]={'date':day,'names':parse_minutes(content,day),'source':link}
                store.atomic_json(path,sorted(snapshots.values(),key=lambda r:r['date']))
        except Exception as error:
            issues.append({'year':year,'error':str(error)})
    rows=sorted(snapshots.values(),key=lambda r:r['date'])
    store.atomic_json(config.DATA/'membership_audit.json',{'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'ledger_hash':store.content_hash(config.VOTERS.read_text()), 'coverage_start':start,'meetings_checked':len(rows),'latest_meeting':rows[-1]['date'] if rows else None,
        'issues':issues,'success':bool(rows) and not issues})
    print(f'Audited {len(rows)} official BOJ meeting rosters; {len(issues)} unresolved years.')
    return 2 if issues or not rows else 0


if __name__=='__main__':
    raise SystemExit(main())
