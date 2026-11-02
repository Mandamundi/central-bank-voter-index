"""Discover, checkpoint, retry and audit official speeches. Any unresolved failure exits nonzero."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import re
from zoneinfo import ZoneInfo
import config
import documents
import http_get
import runlog
import sources
import store
import voters


WITHDRAWN = 'Withdrawn: official page is gone and no longer listed in the catalogue'


def now():
    return dt.datetime.now(dt.timezone.utc)


def process(entries, since, limit=None, force=False):
    store.reconcile_duplicates()
    outcomes = store.load_processing()
    stored = {s['id']: s for s in store.load_speeches()}
    existing_documents = {}
    for speech in sorted(stored.values(), key=lambda r: r["url"].lower().endswith(".pdf"), reverse=True):
        for document_url in [speech['url'], speech.get('text_url') or speech['url']]:
            existing_documents[documents.canonical_url(document_url)] = speech['id']
    today = dt.datetime.now(ZoneInfo(config.BANK_TIMEZONE)).date().isoformat()
    counts = {'discovered': len(entries), 'stored': 0, 'excluded': 0, 'duplicate': 0, 'failed': 0, 'scheduled': 0, 'deferred': 0}
    # Retain old failures even after their listing scrolls outside the live lookback.
    candidates = {e['url']: e for e in entries}
    live = set(candidates)
    for url, prior in outcomes.items():
        if prior.get('status') in {'failure','scheduled','pending'} and prior.get('entry'):
            candidates.setdefault(url, prior['entry'])
    # Persist discovery before fetching any article: limits and interruptions cannot lose URLs.
    for url, entry in candidates.items():
        if url not in outcomes:
            outcomes[url] = {'status': 'pending', 'entry': entry, 'collector_version': config.COLLECTOR_VERSION}
    store.save_processing(outcomes)
    attempts = 0
    for url, entry in candidates.items():
        prior = outcomes[url]
        if (prior.get('status') == 'stored' and prior.get('speech_id') in stored
                and stored[prior['speech_id']]['url'] != url):
            prior = dict(prior, status='pending')
            prior.pop('speech_id', None)
        same_version = prior.get('collector_version') == config.COLLECTOR_VERSION
        if same_version and not force:
            # A withdrawn page that reappears in the catalogue is fetched again.
            if prior.get('status') in {'excluded', 'duplicate'} and not (
                    prior.get('reason', '').startswith('Withdrawn') and url in live):
                continue
            if prior.get('status') == 'stored':
                last = dt.datetime.fromisoformat(prior['checked_at'])
                # Recheck recent articles for corrections, and --force revisits the whole corpus.
                if entry.get('date') and entry['date'] < (now().date() - dt.timedelta(days=90)).isoformat():
                    continue
                if (now() - last).days < config.RECHECK_DAYS:
                    continue
            if prior.get('retry_after') and now() < dt.datetime.fromisoformat(prior['retry_after']):
                counts['deferred'] += 1
                continue
        if limit is not None and attempts >= limit:
            counts['deferred'] += 1
            continue
        attempts += 1
        state = {'entry': entry, 'checked_at': now().isoformat(), 'collector_version': config.COLLECTOR_VERSION,
                 'attempts': prior.get('attempts', 0) + 1}
        try:
            if 'chinese-translation' in url:
                raise documents.Excluded('Translation of a separately collected English original')
            if re.search(r'\b(?:slides|charts|annex|appendix|references)\s+(?:presented by|by|from|for)|^(?:Slides|Charts|Annex|Appendix)\b|slides - fireside', entry['title'], re.I) or re.search(r'[-/](?:slides|charts|annex|appendix|references)(?:[./-]|$)', url, re.I):
                raise documents.Excluded('Supplementary slides/charts rather than prepared speech text')
            # Recognizable nonmembers are explicit exclusions, not invisible discovery omissions.
            if entry.get('role') == 'nonvoter':
                raise documents.Excluded('Official catalogue labels a nonvoting official')
            if entry.get('speaker') and not voters.resolve_speaker(entry['speaker']):
                raise documents.Excluded('Named speaker is outside the verified voting-member ledger')
            if url in existing_documents and prior.get('status') != 'stored':
                state.update(status='duplicate', speech_id=existing_documents[url], reason='PDF already stored through canonical HTML speech page')
            else:
                record = sources.fetch_speech(entry)
                if record['date'] < since:
                    raise documents.Excluded('Before supported/requested collection interval')
                if record['date'] > today or record['available_date'] > today:
                    raise ValueError('Speech is dated in the future; do not score before delivery')
                text_url = documents.canonical_url(record['text_url'])
                duplicate_id = existing_documents.get(text_url)
                if duplicate_id and prior.get('speech_id') != duplicate_id:
                    if prior.get('status') == 'stored' and prior.get('speech_id'):
                        store.quarantine(prior['speech_id'], 'Duplicate of canonical official document '+duplicate_id)
                    state.update(status='duplicate', speech_id=duplicate_id, reason='Same official full-text document')
                else:
                    record['id'] = (prior.get('speech_id') if prior.get('status') == 'stored' else None) or hashlib.sha256(url.encode()).hexdigest()[:20]
                    record.update(retrieved_at=now().isoformat(), content_hash=store.content_hash(record['text']),
                                  collector_version=config.COLLECTOR_VERSION)
                    store.add_speeches([record])
                    for document_url in [record['url'], text_url]:
                        existing_documents[documents.canonical_url(document_url)] = record['id']
                    state.update(status='stored', speech_id=record['id'], content_hash=record['content_hash'])
            counts[state['status']] += 1
        except documents.Scheduled as error:
            state.update(status='scheduled', reason=str(error), retry_after=error.day+'T00:00:00+00:00')
            counts['scheduled'] += 1
        except documents.Excluded as error:
            if prior.get('status') == 'stored' and prior.get('speech_id'):
                store.quarantine(prior['speech_id'], str(error))
            state.update(status='excluded', reason=str(error)); counts['excluded'] += 1
        except KeyboardInterrupt:
            store.save_processing(outcomes)
            raise
        except Exception as error:
            # Placeholders the bank later deletes would otherwise fail every run forever.
            # Stored speeches stay loud: losing corpus text needs a human look.
            if (http_get.status_code(error) in {404, 410} and url not in live
                    and prior.get('status') != 'stored'):
                state.update(status='excluded', reason=f'{WITHDRAWN} (HTTP {http_get.status_code(error)})')
                counts['excluded'] += 1
            else:
                state.update(status='failure', reason=f'{type(error).__name__}: {error}',
                             retry_after=(now() + dt.timedelta(hours=min(24, 2 ** min(state['attempts'], 5)))).isoformat())
                counts['failed'] += 1
                print(f'Failed {url}: {error}', flush=True)
        outcomes[url] = state
        store.save_processing(outcomes)
        if attempts % 20 == 0:
            print(f'Processed {attempts}: {counts}', flush=True)
        if http_get.paused_reason():
            print(f'Source paused: {http_get.paused_reason()}', flush=True)
            break
    unresolved = [u for u, r in outcomes.items() if r['status'] in {'failure', 'pending'}]
    return counts, unresolved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--since')
    parser.add_argument('--full', action='store_true')
    parser.add_argument('--force', action='store_true', help='Recheck stored and excluded URLs after repairs')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    roster = voters.Roster()
    earliest = roster.coverage_start()
    # First run always fills the supported interval, even when run from the daily workflow.
    since = args.since or (earliest if args.full or not store.load_speeches()
                          else (dt.datetime.now(ZoneInfo(config.BANK_TIMEZONE)).date() - dt.timedelta(days=120)).isoformat())
    if since < earliest:
        parser.error(f'Complete membership history begins {earliest}; extend and audit voters.csv before an earlier backfill')
    with runlog.session():
        http_get.begin_source(config.BANK)
        report = {'started_at': now().isoformat(), 'since': since, 'collector_version': config.COLLECTOR_VERSION}
        problems = []
        try:
            report['roster_matches'] = voters.check_current()
            if not report['roster_matches']:
                problems.append('Official roster differs: data/pending_roster.json')
        except Exception as error:
            problems.append(f'Roster verification failed: {error}')
        try:
            sources.refresh_policy_rates()
            store.atomic_json(config.DATA / 'policy_check.json', {'checked_on': now().date().isoformat(), 'source': sources.RATE_PAGE})
        except Exception as error:
            problems.append(f'Policy context refresh failed: {error}')
        try:
            entries = sources.list_speeches(since)
            store.atomic_json(config.DATA / 'catalogue.json', {'retrieved_at': now().isoformat(), 'since': since,
                             'source': sources.SPEECH_ARCHIVE, 'entries': entries})
            report['counts'], report['unresolved_urls'] = process(entries, earliest, args.limit, args.force)
            if report['unresolved_urls']:
                problems.append(f'{len(report["unresolved_urls"])} discovered URLs unresolved')
        except Exception as error:
            problems.append(f'Catalogue discovery failed: {type(error).__name__}: {error}')
        report.update(completed_at=now().isoformat(), problems=problems, success=not problems)
        runlog.write_report(report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
