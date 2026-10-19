"""The BOJ policy setting on a date, read from its policy statements (data/policy_context.json).

Only the sentences that state the setting are kept: the short-term rate guideline, the 10-year yield
target and the asset-purchase amounts. Votes, dissents, members' proposals and the economic assessment
are dropped, so the scorer sees the same kind of input as the Fed's "federal funds target range". A
statement without a short-term rate guideline (a JGB purchase plan, for example) is a supplement: it is
shown after the latest guideline. Settings decided after the speech date are never used.
"""
from __future__ import annotations

import bisect
import functools
import json
import re

import config

UNKNOWN = 'unknown (no Bank of Japan policy guideline is recorded on or before this date).'

# Matched against the sentence with all spaces removed: extracted PDFs split words ("o vernight").
RATE = ('thebankwillapplyanegativeinterestrate', 'continueapplyinganegativeinterestrate',
        'thebankwillencouragetheuncollateralizedovernightcallrate')
SETTING = RATE + (
    'monetarybasewillincrease', '10-yearjgbyieldswillremain', 'targetlevelof10-year', 'upperboundof',
    'yieldsmaymoveupwardanddownward', 'fluctuateintherange',
    'thebankwillpurchasejapanesegovernmentbonds', 'thebankwillpurchasejgbs', 'thebankwillpurchaseanecessaryamountofjgbs',
    'thebankwillconductpurchases', 'continueitsjgbpurchases', 'monthlyoutrightpurchasesofjgbs',
    'thebankwillpurchaseexchange-tradedfunds', 'thebankwillpurchaseetfs', 'discontinuepurchasesofexchange-tradedfunds',
)
VOTE = re.compile(r',?\s*by an? (?:\d+\s*-\s*\d+ majority|unanimous) vote,?|\s*\(?(?:an? )?(?:\d+\s*-\s*\d+ majority|unanimous) vote\)?', re.I)
OTHER_VOICES = re.compile(r'\b[Vv]\s?oting\b|[Dd]issent|\b[Pp]ropos|\bopposed\b|\b(?:Mr|Ms|Mrs)\.|\b[A-Z]{3,} [A-Z][a-z]+')
LEAD_IN = re.compile(r'^.*?(?=(?:The Bank|the Bank) (?:will|decided to continue)|(?:While|With regard to)\b)')


def _sentences(context: str) -> list[str]:
    text = re.sub(r'\s+', ' ', context)
    text = re.sub(r'\[Note\s*\d*\]', ' ', text)
    text = re.sub(r'(?<=\d)\. (?=\d)', '.', text)                  # "0. 75 percent"
    text = re.sub(r'(?<=[a-z%)]\.)\s?\d{1,2}(?=\s|$)', '', text)    # footnote markers: "percent.1"
    return [s.strip() for s in re.split(r'(?<=\.)\s+(?=[A-Z(])', text) if s.strip()]


def setting_sentences(context: str) -> tuple[list[str], bool]:
    """(sentences stating the setting, whether a short-term rate guideline is among them)."""
    kept, has_rate = [], False
    for sentence in _sentences(context):
        squeezed = re.sub(r'\s+', '', sentence).lower()
        if not any(p in squeezed for p in SETTING):
            continue
        sentence = LEAD_IN.sub('', VOTE.sub('', sentence)).strip()
        sentence = re.sub(r'\s+\d{1,2}\.$', '', sentence)              # trailing section number: "percent. 2."
        if OTHER_VOICES.search(sentence) or len(sentence) > 700:
            continue
        sentence = sentence[0].upper() + sentence[1:]
        if sentence not in kept:
            kept.append(sentence)
            has_rate = has_rate or any(p in squeezed for p in RATE)
    return kept, has_rate


@functools.lru_cache(maxsize=1)
def _statements() -> tuple[tuple[str, tuple[str, ...], bool], ...]:
    path = config.DATA / 'policy_context.json'
    if not path.exists():
        return ()
    rows = sorted(json.loads(path.read_text(encoding='utf-8')).values(), key=lambda r: (r['date'], r['source']))
    parsed = []
    for row in rows:
        sentences, has_rate = setting_sentences(row['context'])
        if sentences:
            parsed.append((row['date'], tuple(sentences), has_rate))
    return tuple(parsed)


def context_for(day: str) -> str:
    statements = _statements()
    eligible = statements[:bisect.bisect_right([s[0] for s in statements], day)]
    guideline = max((i for i, s in enumerate(eligible) if s[2]), default=None)
    if guideline is None:
        return UNKNOWN
    date, sentences, _ = eligible[guideline]
    parts = [f"guideline decided {date}: {' '.join(sentences)}"]
    parts += [f"also decided {d}: {' '.join(s)}" for d, s, _ in eligible[guideline + 1:]]
    return '\n'.join(parts)
