"""Check (not published): does the language of the text change the score?

Scores recent Japanese speeches twice, once from the Japanese original and once from the Bank's own
English version, and reports how often the levels agree. Results go to data/language_check.json;
nothing is added to data/scores.jsonl. Uses 2 x --limit Gemini requests.

    python calibrate_language.py --limit 20
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

from dotenv import load_dotenv

import config
import documents as doc
import http_get
import policy
import score
import sources
import store


def english_url(url: str) -> str:
    return url.replace('https://www.boj.or.jp/about/', 'https://www.boj.or.jp/en/about/', 1)


def english_text(url: str) -> str:
    response = http_get.get(url)
    if doc.is_pdf(response, url):
        return doc.pdf_text(response)
    page = doc.tree(response.content)
    content = sources.body(page)
    if len(content) < config.MIN_HTML_TEXT_CHARS:
        content = doc.pdf_text(http_get.get(doc.choose_pdf(page, url)))
    return content


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=20)
    args = parser.parse_args()
    load_dotenv(config.ROOT / '.env')
    key = os.environ.get('GEMINI_API_KEY')
    if not key:
        sys.exit('GEMINI_API_KEY is not set.')
    http_get.begin_source(config.BANK)
    speeches = sorted((s for s in store.load_speeches() if s['language'] == 'ja'),
                      key=lambda s: (s['date'], s['id']), reverse=True)
    limiter = score.RateLimiter(config.REQUESTS_PER_MINUTE)
    rows = []
    for speech in speeches:
        if len(rows) >= args.limit:
            break
        try:
            english = dict(speech, text=english_text(english_url(speech['url'])))
        except Exception as error:   # no English version published, or not extractable
            print(f"  {speech['date']} {speech['speaker']}: no English version ({str(error)[:80]})")
            continue
        setting = policy.context_for(speech['date'])
        stop: list[str] = []
        ja = score.score_one(key, limiter, stop, speech, setting)
        en = score.score_one(key, limiter, stop, english, setting)
        if ja is None or en is None:
            print(f"  {speech['date']} {speech['speaker']}: scoring failed; skipped")
            continue
        level = lambda r: r.level if r.policy_relevant else 0
        rows.append({'id': speech['id'], 'date': speech['date'], 'speaker': speech['speaker'],
                     'ja_level': level(ja), 'en_level': level(en)})
        print(f"  {speech['date']} {speech['speaker']:20s} ja={level(ja)} en={level(en)}", flush=True)
    same = sum(r['ja_level'] == r['en_level'] for r in rows)
    within_one = sum(abs(r['ja_level'] - r['en_level']) <= 1 for r in rows)
    store.atomic_json(config.DATA / 'language_check.json', {
        'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'scorer_version': config.SCORER_VERSION,
        'pairs': len(rows), 'same_level': same, 'within_one_level': within_one, 'rows': rows})
    if rows:
        print(f"{len(rows)} pairs: same level {same} ({same / len(rows):.0%}), within one level {within_one}.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
