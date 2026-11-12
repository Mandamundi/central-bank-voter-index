"""Bounded official document extraction, with explicit unavailable/ambiguous outcomes."""
from __future__ import annotations
from io import BytesIO
from urllib.parse import urljoin, urlsplit, urlunsplit
import datetime as dt
import re
from lxml import html
from pypdf import PdfReader
import config
import http_get


class Excluded(ValueError):
    """A deliberate exclusion, persisted with its reason (not an extraction failure)."""


class Scheduled(ValueError):
    def __init__(self, day):
        self.day = day
        super().__init__(f'Prepared text scheduled for {day}')


class Embargoed(Scheduled):
    """The bank lists the speech but holds its text until later in the day ('to be published 12:30pm')."""
    def __init__(self, day):
        super().__init__(day)
        self.args = (f'Prepared text under same-day embargo on {day}',)


EMBARGO = re.compile(r'\bto be (?:published|released)\b', re.I)


def canonical_url(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, '', ''))


def tree(source):
    return html.fromstring(source)


def text(node):
    return ' '.join(node.text_content().split())


def pdf_text(response, minimum_chars=None):
    reader = PdfReader(BytesIO(response.content))
    if reader.is_encrypted and not reader.decrypt(''):
        raise ValueError('Encrypted PDF cannot be extracted')
    content = '\n\n'.join(page.extract_text() or '' for page in reader.pages).replace('\x00', '')
    if len(content.strip()) < (config.MIN_TEXT_CHARS if minimum_chars is None else minimum_chars):
        raise ValueError('PDF contains insufficient extractable text; OCR/manual recovery needed')
    return content


def is_pdf(response, url):
    return response.content.lstrip().startswith(b'%PDF-') or urlsplit(url).path.lower().endswith('.pdf')


def paragraphs(nodes):
    result = []
    for node in nodes:
        for bad in node.xpath('.//script|.//style|.//nav|.//aside|.//form|.//*[contains(@class,"related")]|.//*[contains(@class,"visually-hidden")]|.//*[contains(@class,"modal__close")]'):
            bad.drop_tree()
        result.append('\n'.join(s.strip() for s in node.itertext() if s.strip()))
    return '\n\n'.join(result).replace('\x00', '')


def choose_pdf(page, url):
    """Prefer explicitly labelled full/prepared text. Never blindly take a charts appendix."""
    candidates = []
    for anchor in page.xpath('//main//div[contains(@class,"content-block")]//a[@href]|//*[@id="contents"]//a[@href]'):
        link = urljoin(url, anchor.get('href'))
        label = text(anchor)
        if not urlsplit(link).path.lower().endswith('.pdf'):
            continue
        if re.search(r'annex|appendix|slides|charts|translation|chinese|qa-transcript|q-and-a|図表|参考資料', label + ' ' + link, re.I):
            continue
        priority = 0 if re.search(r'全文|full text|speech|remarks|挨拶|講演', label, re.I) else 1
        if re.search(r'/files/(?:speech|news)/', link, re.I):
            priority -= 1
        if '/files/speech/' in link:
            priority -= 1
        candidates.append((priority, canonical_url(link)))
    if not candidates:
        raise ValueError('No suitable full-text PDF linked from speech page')
    best = min(p for p, _ in candidates)
    urls = set(u for p, u in candidates if p == best)
    if len(urls) != 1:
        raise ValueError(f'Ambiguous full-text PDFs: {sorted(urls)}')
    return next(iter(urls))


def prepared_format(title):
    if re.search(r'accompanying paper|supporting paper|paper accompanying', title, re.I):
        raise Excluded('Supporting research paper rather than individually authored prepared speech')
    if re.search(r'\b(?:slides|charts) (?:presented by|by|from)\b|slides -|slide deck|presentation slides', title, re.I):
        raise Excluded('Presentation slides without prepared speech prose')
    if re.search(r'\b(?:interview|podcast|Q\s*&\s*A|question.and.answer|press conference|conversation with)\b|記者会見|インタビュー|質疑応答|【対談】', title, re.I):
        raise Excluded('Interview/Q&A/conversation rather than authored prepared text')
    return 'prepared_text'


def available_day(delivered, published):
    # Publication can lag delivery. Never backdate an English translation to its delivery date.
    return max(delivered, published) if published else delivered


def document_urls(page, url):
    return sorted({canonical_url(urljoin(url, a.get('href'))) for a in page.xpath('//main//a[@href]')
                   if '.pdf' in a.get('href', '').lower()
                   and not re.search(r'annex|appendix|slides|charts|translation|chinese|qa-transcript|q-and-a|図表|参考資料', text(a) + ' ' + a.get('href'), re.I)})


def looks_like_slides(content):
    # Conservative detection for English PDF decks with no date/byline metadata.
    pages = content.split('\n\n')
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    long_lines = sum(len(line) >= 65 for line in lines)
    sentences = len(re.findall(r'\b[a-z]{3,}[.!?](?:\s|$)', content))
    return len(lines) >= 20 and long_lines / len(lines) < .15 and sentences < 5
