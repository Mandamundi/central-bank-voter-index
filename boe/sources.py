"""BOE official sitemap: HTML speeches plus PDF-only records, exact dates and MPC bylines."""
from __future__ import annotations
import csv
import json
from io import BytesIO
from zipfile import ZipFile
import datetime as dt
import re
from urllib.parse import urljoin, urlsplit
from lxml import html
import config
import documents as doc
import http_get
import voters

BASE = 'https://www.bankofengland.co.uk'
SPEECH_ARCHIVE = BASE + '/sitemap/speeches'
MEMBERS_URL = BASE + '/about/people/monetary-policy-committee'
RATE_PAGE = BASE + '/boeapps/database/Bank-Rate.asp'
MONTHS = {name.lower(): i for i, name in enumerate(['January','February','March','April','May','June',
          'July','August','September','October','November','December'], 1)}
tree, text = doc.tree, doc.text


def today():
    return dt.date.today()


def parse_date(value):
    match = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', value)
    if match:
        return dt.date.fromisoformat(match[1]).isoformat()
    match = re.search(r'\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})\b', value)
    if match and match[2].lower() in MONTHS:
        try:
            return dt.date(int(match[3]), MONTHS[match[2].lower()], int(match[1])).isoformat()
        except ValueError:
            return None
    return None


def speaker_label(label):
    label = re.sub(r'\s*\(?pdf.*$', '', label, flags=re.I).strip()
    match = re.search(r'(?:speech|remarks|address|commentary|lecture)(?:\s+(?:given|delivered))?\s+by\s+(.+)', label, re.I)
    if match:
        candidate = re.split(r'\s+(?:at|to|for|on|given during)\s+|\s+[—–−-]\s+|\s+transcript\b', match[1], maxsplit=1, flags=re.I)[0]
        return candidate.strip(' .—–−-')
    return None


def title_member(title):
    found = set()
    for row in voters.load_terms():
        for alias in [row['name']] + row.get('aliases', '').split('|'):
            if alias and re.search(r'(?<![A-Za-z])' + re.escape(alias) + r'(?![A-Za-z])', title, re.I):
                found.add(row['name'])
    if len(found) > 1:
        raise ValueError(f'Ambiguous speech authors in title: {title}')
    return next(iter(found), None)


def parse_catalogue(source):
    page = tree(source)
    found = {}
    for anchor in page.xpath('//a[@href]'):
        url = doc.canonical_url(urljoin(SPEECH_ARCHIVE, anchor.get('href')))
        path = urlsplit(url).path
        match = re.search(r'/speech/(\d{4})(?:/|$)', path, re.I)
        if not match:
            continue
        title = text(anchor)
        # Catalogues contain generic non-voter speeches too. Persist these for the coverage audit.
        found[url] = {'url': url, 'year': int(match[1]), 'date': None,
                      'speaker': speaker_label(title) or title_member(title), 'title': title, 'source': 'BOE official speech sitemap'}
    if not found:
        raise ValueError('Official speech sitemap returned no speech URLs')
    return list(found.values())


def list_speeches(since):
    start = dt.date.fromisoformat(since)
    entries = parse_catalogue(http_get.get(SPEECH_ARCHIVE).content)
    return sorted([e for e in entries if e['year'] >= start.year],
                  key=lambda e: (-e['year'], e['url'].lower().endswith('.pdf'), e['url']))


def current_voters():
    page = tree(http_get.get(MEMBERS_URL).content)
    found = {}
    for anchor in page.xpath('//main//a[contains(@href,"/biography")]'):
        # Actual member cards put name and role in different child elements of the anchor.
        headings = anchor.xpath('.//h2|.//h3|.//h4|.//*[contains(@class,"person-name")]')
        label = text(headings[0]) if headings else ' '.join(anchor.itertext()).strip().split('\n')[0].strip()
        # A card's role starts after the canonical name; resolve by an exact known alias prefix.
        whole = text(anchor)
        person = None
        for term in voters.load_terms():
            for alias in [term['name']] + term.get('aliases', '').split('|'):
                if alias and re.match(r'^(?:(?:Dr|Sir|Professor)\s+)?' + re.escape(alias) + r'(?:\s|$)', whole, re.I):
                    person = term['name']
        # New members must remain visible to roster checking even without a ledger alias.
        if not person:
            person = re.split(r'\b(?:Deputy Governor|Governor|External member|Chief Economist)\b', whole, maxsplit=1)[0].strip()
        role = next((r for r in ['Deputy Governor', 'Chief Economist', 'External member', 'Governor'] if r.lower() in whole.lower()), None)
        if person and role:
            found[person] = {'name': person, 'role': role, 'source': urljoin(BASE, anchor.get('href'))}
    if len(found) != 9:
        raise ValueError(f'Official MPC member cards yielded {len(found)} members; expected nine')
    return list(found.values())


def exact_date(page, url=None):
    nodes = page.xpath('//main//*[contains(@class,"date")]|//time|//meta[@property="article:published_time"]')
    for node in nodes:
        value = node.get('datetime') or node.get('content') or text(node)
        if day := parse_date(value):
            return day
    main = page.xpath('//main')
    if main:
        match = re.search(r'Published on\s+(\d{1,2}\s+\w+\s+\d{4})', text(main[0]), re.I)
        if match:
            return parse_date(match[1])
    return None  # A URL month is NOT a delivery date.


def body(page):
    nodes = page.xpath('//main//div[contains(concat(" ",normalize-space(@class)," ")," content-block ")][not(ancestor::div[contains(concat(" ",normalize-space(@class)," ")," content-block ")])]')
    if not nodes:
        nodes = page.xpath('//article')
    return doc.paragraphs(nodes)


def fetch_speech(entry):
    doc.prepared_format(entry['title'])
    override = None
    extraction_format = 'prepared_text'
    response = http_get.get(entry['url'])
    if response.content.startswith(b'PK'):
        with ZipFile(BytesIO(response.content)) as archive:
            if any(name.startswith('ppt/') for name in archive.namelist()):
                raise doc.Excluded('PowerPoint slide deck without prepared speech prose')
        raise ValueError('Unsupported Office attachment requires explicit format extraction')
    direct_pdf = doc.is_pdf(response, entry['url'])
    title = entry['title']
    published = None
    attached = []
    text_url = entry['url']
    speaker = voters.resolve_speaker(entry.get('speaker') or '')
    if direct_pdf:
        content = doc.pdf_text(response)
        if re.search(r'supporting paper for a speech', content[:2500], re.I):
            raise doc.Excluded('Supporting research paper rather than prepared speech')
        if doc.looks_like_slides(content):
            raise doc.Excluded('Slide deck without prepared speech prose')
        delivered = parse_date(content[:2000])
        if not speaker:
            raw_speaker = speaker_label(title)
            if not raw_speaker:
                match = re.search(r'(?:Speech|Remarks)\s+(?:given\s+|delivered\s+)?by\s+([A-Z][A-Za-z. ’-]+)', content[:2500])
                raw_speaker = match[1].split(',')[0].strip() if match else None
            if raw_speaker:
                speaker = voters.resolve_speaker(raw_speaker)
                if not speaker:
                    raise doc.Excluded('PDF byline identifies a non-MPC speaker')
            else:
                for row in voters.load_terms():
                    if re.search(r'(?:^|\n)\s*' + re.escape(row['name']) + r'\s*(?:,|\n)', content[:2500]):
                        speaker = row['name']

    else:
        page = tree(response.content)
        headings = page.xpath('//h1')
        title = text(headings[0]) if headings else title
        raw_speaker = speaker_label(title) or entry.get('speaker') or title_member(title)
        if not raw_speaker:
            header = page.xpath('//main//*[contains(@class,"page-description")]')
            if header:
                match = re.search(r'(?:Given|Delivered) by ([A-Z][A-Za-z .]+?)(?: at | to |,|$)', text(header[0]))
                raw_speaker = match[1] if match else None
        speaker = voters.resolve_speaker(raw_speaker or '')
        if raw_speaker and not speaker:
            raise doc.Excluded('Named author is outside verified MPC voting-member history')
        attached = doc.document_urls(page, entry['url'])
        if re.search(r'\bslides (?:presented by|by|from)\b', title, re.I):
            raise doc.Excluded('Slides without prepared speech text')
        published = exact_date(page)
        if published and published > today().isoformat():
            raise doc.Scheduled(published)
        content = body(page)
        if config.MIN_HTML_TEXT_CHARS <= len(content) < config.SUMMARY_TEXT_CHARS:
            # A short page can be the website's summary of a speech whose full text is a PDF.
            try:
                pdf_url = doc.choose_pdf(page, entry['url'])
                pdf_content = doc.pdf_text(http_get.get(pdf_url))
            except http_get.SourcePaused:
                raise
            except Exception:
                pdf_url = None
            if pdf_url and len(pdf_content) > len(content) and not doc.looks_like_slides(pdf_content):
                text_url, content = pdf_url, pdf_content
        delivered = published
        # Delivery notices can differ from publication. Inspect the introductory header only.
        header = page.xpath('//main//*[contains(@class,"page-description")]')
        if header:
            match = re.search(r'(?:given|delivered|held)\b.{0,220}?\b(\d{1,2}\s+[A-Za-z]+\s+\d{4})', text(header[0]), re.I)
            if match:
                delivered = parse_date(match[1])
        if len(content) < config.MIN_HTML_TEXT_CHARS:
            try:
                text_url = doc.choose_pdf(page, entry['url'])
            except ValueError:
                path = config.DATA / 'source_overrides.json'
                overrides = json.loads(path.read_text()) if path.exists() else {}
                override = overrides.get(entry['url'])
                if override:
                    text_url = override['text_url']
                    attached.append(text_url)
                elif re.search(r'Play video|watch (?:the )?video', text(page.xpath('//main')[0]), re.I):
                    raise doc.Excluded('Official page offers video without a published prepared text')
                else:
                    raise
            downloaded = http_get.get(text_url)
            try:
                content = doc.pdf_text(downloaded)
            except ValueError:
                if not override or not override.get('recovered_text'):
                    raise
                import hashlib
                if hashlib.sha256(downloaded.content).hexdigest() != override['pdf_sha256']:
                    raise ValueError('Recovered-text PDF checksum changed; review replacement source')
                recovered = config.DATA / override['recovered_text']
                if hashlib.sha256(recovered.read_bytes()).hexdigest() != override['text_sha256']:
                    raise ValueError('Recovered-text checksum mismatch')
                content = recovered.read_text(encoding='utf-8')
                extraction_format = 'prepared_text_ocr'
                published = max(published or '', override['mirror_published_date'])
            if doc.looks_like_slides(content):
                raise doc.Excluded('Slide deck without prepared speech prose')
            delivered = parse_date(content[:2000]) or delivered
    doc.prepared_format(title)
    if re.search(r'views expressed in this paper are (?:solely )?those of the authors', ' '.join(content[:3000].split()), re.I):
        raise doc.Excluded('Joint research paper rather than individually attributable prepared remarks')
    if not speaker:
        raise ValueError(f'Unresolved BOE speech author: {title!r}')
    if not delivered:
        raise ValueError('No exact speech date in official header/PDF front matter')
    term = voters.Roster().term_for(speaker, delivered)
    if not term:
        raise doc.Excluded('Author was not an MPC voter on delivery date')
    if len(content) < config.MIN_TEXT_CHARS:
        raise ValueError(f'Insufficient prepared text: {len(content)} characters')
    return {'date': delivered, 'published_date': published or '',
            'available_date': doc.available_day(delivered, published),
            'availability_basis': 'mirror_publication_date_proxy_ocr' if extraction_format.endswith('_ocr') else ('official_publication_date' if published else 'printed_delivery_date_proxy'), 'speaker': speaker,
            'role': term['role'], 'title': title, 'url': entry['url'], 'text_url': text_url,
            'source': 'Bank of England; BIS-hosted original; OCR transcription' if extraction_format.endswith('_ocr') else 'Bank of England', 'language': 'en', 'format': extraction_format, 'document_urls': json.dumps(attached), 'text': content}


def decision_date(value):
    match = re.search(r'Published on\s+(\d{1,2}\s+\w+\s+\d{4})', value, re.I)
    return parse_date(match[1]) if match else None


def refresh_policy_rates():
    """Read the entire official change history, not only the latest rate decision."""
    page = tree(http_get.get(RATE_PAGE).content)
    rows = []
    for row in page.xpath('//table//tr'):
        cells = row.xpath('./td')
        if len(cells) != 2:
            continue
        try:
            day = dt.datetime.strptime(text(cells[0]), '%d %b %y').date().isoformat()
            value = float(text(cells[1]))
        except ValueError:
            continue
        if day <= dt.date.today().isoformat():
            rows.append({'date': day, 'level': f'{value:g}', 'source': RATE_PAGE})
    if len(rows) < 50:
        raise ValueError(f'Official Bank Rate table unexpectedly short: {len(rows)} rows')
    rows.sort(key=lambda r: r['date'])
    temporary = config.POLICY_RATES.with_suffix('.csv.tmp')
    with temporary.open('w', encoding='utf-8', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=['date','level','source'])
        writer.writeheader(); writer.writerows(rows)
    temporary.replace(config.POLICY_RATES)
    return True
