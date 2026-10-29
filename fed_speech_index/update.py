"""Daily update: refresh the target-rate history and the FOMC voter list, then pull new speeches
from the Board and the twelve Reserve Banks into the speech store. Run by the GitHub Action.

Each source's newest entries are checked (RSS where the site has a working feed, otherwise its
listing page); per-URL outcomes and retry dates are recorded in data/processing.json.

    python update.py
"""
from __future__ import annotations

import csv
import datetime as dt
from collections import Counter
from urllib.parse import urlsplit
import sys

import config
import http_get
import sources
import store
import voters
import runlog

SAVE_EVERY = 25
FRASER_COPY = 'FRASER holds this speech'      # a bank copy of a speech already stored from FRASER


def refresh_fed_funds() -> None:
    http_get.begin_source('rates')
    print("Refreshing Fed funds history...", flush=True)
    try:
        history = sources.fetch_fed_funds_history()
    except Exception as e:  # keep the last good copy if federalreserve.gov is unreachable
        print(f"Fed funds history not refreshed ({e}); keeping existing file.")
        return
    if len(history) < 50:
        print("Fed funds history looks incomplete; keeping existing file.")
        return
    config.DATA.mkdir(parents=True, exist_ok=True)
    temporary = config.FED_FUNDS.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "level"])
        w.writerows(history)
    temporary.replace(config.FED_FUNDS)
    print(f"Fed funds history: {len(history)} changes, latest {history[-1]}")


def _eligible(outcome: dict | None, now: dt.datetime, recover: bool) -> bool:
    if not outcome:
        return True
    if outcome["status"] == "stored" or outcome.get("reason") == FRASER_COPY:
        return False
    if recover and outcome["status"] != "excluded":
        return True
    if outcome.get("collector_version") != config.COLLECTOR_VERSION and outcome["status"] != "http_failure":
        return True
    if outcome["status"] == "excluded":
        return False
    return not outcome.get("retry_after") or now >= dt.datetime.fromisoformat(outcome["retry_after"])


def _outcome(source: str, status: str, reason: str, old: dict | None, **extra) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    row = {"source": source, "status": status, "reason": reason,
           "last_attempt": now.isoformat(), "attempts": (old or {}).get("attempts", 0) + 1,
           "collector_version": config.COLLECTOR_VERSION, **extra}
    if status not in {"stored", "excluded"}:
        # Back off: a page that keeps failing (often a duplicate address of a speech already stored)
        # is retried after 1, 2, 4 ... days (7, 14 ... for HTTP errors), at most every 30 days.
        days = min(30, (7 if status == "http_failure" else 1) * 2 ** (row["attempts"] - 1))
        row["retry_after"] = (now + dt.timedelta(days=days)).isoformat()
    return row


def _health_path():
    return config.DATA / 'source_health.json'


def _save_health(health):
    import json
    temporary = _health_path().with_suffix('.json.tmp')
    temporary.write_text(json.dumps(health, indent=2) + '\n', encoding='utf-8')
    temporary.replace(_health_path())


def pull(names: list[str], full: bool, recover: bool = False, limit: int | None = None) -> int:
    with runlog.session():
        return _pull(names, full, recover, limit)


def _pull(names: list[str], full: bool, recover: bool, limit: int | None) -> int:
    import json
    unknown = set(names) - sources.SOURCES.keys()
    if unknown:
        raise ValueError(f"Unknown sources: {', '.join(sorted(unknown))}")
    outcomes = store.load_processing()
    speeches = store.load_speeches()
    stored_urls = {r['url']: r['id'] for r in speeches}
    fraser_days = {(r['date'], voters.name_key(r['speaker'])) for r in speeches if r['source'] == 'fraser'}
    health = json.loads(_health_path().read_text()) if _health_path().exists() else {}
    report = {'started_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'status': 'running',
              'collector_version': config.COLLECTOR_VERSION, 'full_archive': full,
              'sources': {name: {'status':'not_started', 'stored':0} for name in names}}
    total = 0
    runlog.write_report(report)
    try:
        for name in names:
            now = dt.datetime.now(dt.timezone.utc)
            if not recover and health.get(name, {}).get('retry_after') and now < dt.datetime.fromisoformat(health[name]['retry_after']):
                report['sources'][name] = {'status':'paused', 'stored':0, **health[name]}
                print(f"{name}: paused until {health[name]['retry_after']}; continuing to the next bank.", flush=True)
                runlog.write_report(report)
                continue
            http_get.begin_source(name)
            report['sources'][name]['status'] = 'listing'
            runlog.write_report(report)
            print(f"{name}: gathering {'full archive' if full else 'recent'} listing...", flush=True)
            listed = {}
            listing_error = None
            try:
                for e in sources.SOURCES[name][1](full):
                    listed.setdefault(e['url'], e)
                    if len(listed) % 250 == 0:
                        print(f"  {name}: {len(listed)} archive URLs found", flush=True)
                if not listed:
                    listing_error = 'Listing returned no speech URLs; layout may have changed'
            except Exception as error:
                listing_error = str(error)
            if http_get.listing_problems():
                listing_error = '; '.join(http_get.listing_problems())
            if listing_error:
                print(f"{name}: listing incomplete: {listing_error}", flush=True)
            previous = {u for u, r in outcomes.items() if r['status'] != 'stored'}
            host = urlsplit(sources.SOURCE_ORIGINS[name]).netloc.removeprefix('www.')
            for url in sorted(previous):
                if urlsplit(url).netloc.removeprefix('www.') == host or outcomes.get(url, {}).get('source') == name:
                    listed.setdefault(url, outcomes.get(url, {}).get('entry') or sources.entry(name, url))
            counts = Counter()
            todo = []
            for url, e in listed.items():
                if url in stored_urls:
                    counts['already_stored'] += 1
                    if outcomes.get(url, {}).get('status') != 'stored':
                        outcomes[url] = _outcome(name, 'stored', 'existing speech', outcomes.get(url), speech_id=stored_urls[url])
                elif _eligible(outcomes.get(url), now, recover):
                    todo.append(e)
                else:
                    counts['remembered_exclusion' if outcomes[url]['status']=='excluded' else 'deferred_failure'] += 1
            eligible = len(todo)
            # Retrying the same two failed URLs first can pause a bank on every
            # recovery run and starve hundreds of unattempted archive pages.
            priority = {'metadata_failure': 1, 'extraction_failure': 1,
                        'excluded': 2, 'request_failure': 3, 'http_failure': 4}
            todo.sort(key=lambda e: priority.get(outcomes.get(e['url'], {}).get('status'), 0))
            if limit is not None:
                todo = todo[:limit]
            result = {'status':'running', 'listed':len(listed), 'eligible':eligible,
                      'selected':len(todo), 'listing_error':listing_error, 'counts':dict(counts)}
            report['sources'][name] = result
            print(f"{name}: {len(listed)} URLs; {len(todo)} selected; {counts['already_stored']} already stored.", flush=True)
            new, pending = [], {}
            interrupted = False

            def checkpoint():
                nonlocal total
                added = store.add_speeches(new)
                counts['stored'] += added
                counts['duplicate'] += len(new)-added
                total += added
                for record in new:
                    stored_urls[record['url']] = record['id']
                outcomes.update(pending)
                store.save_processing(outcomes)
                new.clear()
                pending.clear()
                counts['not_attempted'] = eligible-counts['attempted']
                result.update(stored=counts['stored'], counts=dict(counts),
                              remaining=counts['not_attempted'], current_url=result.get('current_url'))
                runlog.write_report(report)

            try:
                for e in todo:
                    if http_get.paused_reason():
                        break
                    url = e['url']
                    result['current_url'] = url
                    runlog.detail(f"  {name} {counts['attempted']+1}/{len(todo)}: {url}")
                    old = outcomes.get(url)
                    try:
                        record, reason = sources.fetch(e)
                    except Exception as error:
                        code = http_get.status_code(error)
                        status = ('request_failure' if http_get.temporary_failure(error) else
                                  'http_failure' if code is not None else 'extraction_failure')
                        pending[url] = _outcome(name, status, str(error), old, http_status=code, entry=e)
                        counts[status] += 1
                        runlog.logger.info('%s %s %s: %s', name, url, status, error)
                        if counts[status] <= 3 or runlog.verbose:
                            print(f"  {status}: {url}: {error}", flush=True)
                    else:
                        if record and any(d == record['date'] and voters.same_person(voters.name_key(record['speaker']), k)
                                          for d, k in fraser_days):
                            record, reason = None, FRASER_COPY
                        if record:
                            new.append(record)
                            pending[url] = _outcome(name, 'stored', 'speech extracted', old, speech_id=record['id'], entry=e)
                        else:
                            status = ('excluded' if reason.startswith('not a speech') or reason == FRASER_COPY else
                                      'metadata_failure' if reason.startswith('metadata missing') else 'extraction_failure')
                            pending[url] = _outcome(name, status, reason, old, entry=e)
                            counts[status] += 1
                            runlog.logger.info('%s %s %s: %s', name, url, status, reason)
                            if status != 'excluded' and (counts[status] <= 3 or runlog.verbose):
                                print(f"  {status}: {url}: {reason}", flush=True)
                    counts['attempted'] += 1
                    if counts['attempted'] % SAVE_EVERY == 0:
                        checkpoint()
                        failures = sum(counts[k] for k in ('metadata_failure','extraction_failure','request_failure','http_failure'))
                        print(f"  {name}: {counts['attempted']}/{len(todo)} checked; saved={counts['stored']}, excluded={counts['excluded']}, failures={failures}", flush=True)
            except KeyboardInterrupt:
                interrupted = True
                raise
            finally:
                if interrupted:
                    result['status'] = 'interrupted'
                elif http_get.paused_reason():
                    result['status'] = 'paused'
                    health[name] = {'reason':http_get.paused_reason(),
                                    'retry_after':(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=config.SOURCE_PAUSE_MINUTES)).isoformat()}
                    _save_health(health)
                    result.update(health[name])
                    print(f"{name}: paused after repeated failures; remaining URLs preserved; continuing to the next bank.", flush=True)
                elif listing_error:
                    result['status'] = 'listing_incomplete'
                elif eligible > counts['attempted']:
                    result['status'] = 'limited'
                elif any(counts[k] for k in ('metadata_failure','extraction_failure','request_failure','http_failure','deferred_failure')):
                    result['status'] = 'needs_recovery'
                else:
                    result['status'] = 'completed'
                if result['status'] != 'paused':
                    health.pop(name, None)
                    _save_health(health)
                checkpoint()
            print(f"{name}: stored={counts['stored']}, duplicate={counts['duplicate']}, excluded={counts['excluded']}, "
                  f"metadata_failure={counts['metadata_failure']}, extraction_failure={counts['extraction_failure']}, "
                  f"request_failure={counts['request_failure']}, http_failure={counts['http_failure']}, "
                  f"remaining={counts['not_attempted']}, deferred_failure={counts['deferred_failure']}; {result['status']}", flush=True)
        report['status'] = 'completed' if all(r['status']=='completed' for r in report['sources'].values()) else 'partial'
    except KeyboardInterrupt:
        report['status'] = 'interrupted'
        for result in report['sources'].values():
            if result['status'] in {'listing', 'running'}:
                result['status'] = 'interrupted'
        raise
    except Exception:
        report['status'] = 'failed'
        raise
    finally:
        report['finished_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        report['added'] = total
        report['speech_count'] = len(store.load_speeches())
        runlog.write_report(report)
        print(f"Run {report['status']}: {total} added; {report['speech_count']} speeches stored.", flush=True)
        print(f"Run report: {report['report']}", flush=True)
    return total


def main() -> int:
    with runlog.session():
        try:
            refresh_fed_funds()
            http_get.begin_source('voters')
            voters.refresh(full=False)
            added = pull(list(sources.SOURCES), full=False)
            print(f"Added {added} new speeches.")
        except KeyboardInterrupt:
            print('Stopped by user. Completed collection work is saved; see the log for the interrupted stage.', flush=True)
            return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
