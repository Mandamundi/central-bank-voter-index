"""Official BOJ Japanese catalogue: voting members, original texts, delivery dates and PDFs."""
from __future__ import annotations
import datetime as dt
import json
import re
import unicodedata
from urllib.parse import urljoin, urlsplit
import config
import documents as doc
import http_get
import voters

BASE = 'https://www.boj.or.jp'
SPEECH_ARCHIVE = BASE + '/about/press/koen_all/index.htm'
MEMBERS_URL = BASE + '/en/about/organization/policyboard/index.htm'
RATE_PAGE = BASE + '/en/mopo/mpmdeci/index.htm'
tree, text = doc.tree, doc.text


def parse_date(value):
    value = unicodedata.normalize('NFKC', value)
    match = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', value)
    if match:
        return dt.date(*map(int, match.groups())).isoformat()
    match = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', value)
    if match:
        return dt.date.fromisoformat(match[1]).isoformat()
    match = re.search(r'\b([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})', value)
    if match:
        month = match[1][:3].title()
        try:
            return dt.datetime.strptime(f'{month} {match[2]} {match[3]}', '%b %d %Y').date().isoformat()
        except ValueError:
            pass
    return None


def current_voters():
    page = tree(http_get.get(MEMBERS_URL).content)
    result = {}
    for anchor in page.xpath('//main//a[@href]'):
        url = urljoin(MEMBERS_URL, anchor.get('href'))
        match = re.search(r'/policyboard/(gv|dg|bm)_\w+\.htm$', url)
        if not match:
            continue
        name = text(anchor)
        name = re.sub(r'^(?:Member of the Policy Board|Deputy Governor|Governor)\s*', '', name)
        role = {'gv': 'Governor', 'dg': 'Deputy Governor', 'bm': 'Member of the Policy Board'}[match[1]]
        if len(name.split()) != 2:
            raise ValueError(f'Unexpected BOJ member name: {name!r}')
        result[name] = {'name': name, 'role': role, 'source': url}
    if len(result) != 9:
        raise ValueError(f'Official Policy Board cards yielded {len(result)} members; expected nine')
    return list(result.values())


def parse_catalogue(source, archive):
    page = tree(source)
    result = []
    for row in page.xpath('//table//tbody/tr'):
        cells = row.xpath('./th|./td')
        if len(cells) < 3:
            continue
        day = parse_date(text(cells[0]))
        if not day:
            raise ValueError(f'Unparsed BOJ catalogue date: {text(cells[0])!r}')
        label = text(cells[1])
        # 副総裁 must precede 総裁; executive directors are explicitly nonvoters.
        role = re.search(r'副総裁|総裁|審議委員', label)
        for anchor in cells[2].xpath('.//a[@href]'):
            url = doc.canonical_url(urljoin(archive, anchor.get('href')))
            if not re.search(r'/ko\d{6}', url):
                continue
            result.append({'url': url, 'date': day, 'speaker_label': label,
                'speaker': voters.resolve_speaker(label), 'title': text(anchor),
                'role': role[0] if role else 'nonvoter', 'source': archive})
    if not result:
        raise ValueError(f'BOJ archive has no parsed records: {archive}')
    return result


def list_speeches(since):
    start = dt.date.fromisoformat(since)
    archive_page = tree(http_get.get(SPEECH_ARCHIVE).content)
    archives = {int(match[1]): urljoin(SPEECH_ARCHIVE, anchor.get('href'))
                for anchor in archive_page.xpath('//main//a[@href]')
                if (match := re.search(r'/koen_(\d{4})/index.htm', anchor.get('href')))}
    required = set(range(start.year, dt.date.today().year + 1))
    if required - set(archives):
        raise ValueError(f'BOJ year catalogue missing years {sorted(required - set(archives))}')
    result = {}
    for year in sorted(required, reverse=True):
        url = archives[year]
        for entry in parse_catalogue(http_get.get(url).content, url):
            if start.isoformat() <= entry['date'] <= dt.date.today().isoformat():
                result[entry['url']] = entry
    return sorted(result.values(), key=lambda e: (e['date'], e['url']), reverse=True)


def body(page):
    nodes = page.xpath('//main/div[contains(@class,"outline")]')
    if not nodes:
        nodes = page.xpath('//main')
    return doc.paragraphs(nodes)


def fetch_speech(entry):
    if entry.get('role') == 'nonvoter':
        raise doc.Excluded('Executive director or other nonvoting speaker')
    speaker = voters.resolve_speaker(entry.get('speaker_label') or entry.get('speaker') or '')
    if not speaker:
        raise ValueError(f'Unmapped voting-member byline: {entry.get("speaker_label")!r}')
    doc.prepared_format(entry['title'])
    response = http_get.get(entry['url'])
    text_url = entry['url']
    title = entry['title']
    attached = []
    language = 'ja'
    published = None
    if doc.is_pdf(response, entry['url']):
        content = doc.pdf_text(response)
    else:
        page = tree(response.content)
        headings = page.xpath('//h1')
        title = text(headings[0]) if headings else title
        attached = doc.document_urls(page, entry['url'])
        # Japanese archive sometimes links an English-only essay or an abridged translation.
        # Follow its explicit official language link to the complete original.
        if re.search('英語のみ|抄訳', title) or re.search('英語のみ', text(page.xpath('//main')[0])[:500]):
            links = page.xpath('//main//a[@lang="en"]/@href')
            if len(links) != 1:
                raise ValueError('Full English original link missing or ambiguous')
            text_url = urljoin(entry['url'], links[0])
            original = http_get.get(text_url)
            page = tree(original.content)
            language = 'en'
            attached = doc.document_urls(page, text_url)
        header = page.xpath('//main//p[contains(@class,"txt-right")]')
        if header:
            published = parse_date(text(header[0]))
        content = body(page)
        if len(content) < config.MIN_HTML_TEXT_CHARS:
            text_url = doc.choose_pdf(page, text_url)
            content = doc.pdf_text(http_get.get(text_url))
    # The Japanese archive labels 開催日 (date held), not English translation release date.
    day = entry['date']
    # The document header may date a translation released after the 開催日 in the catalogue.
    # Preserve that delay instead of treating the release date as the delivery date.
    printed = published or parse_date(content[:1000])
    filename = re.search(r'/ko(\d{2})(\d{2})(\d{2})[a-z]', entry['url'])
    posted_proxy = f'20{filename[1]}-{filename[2]}-{filename[3]}' if filename else day
    published = max(printed or day, posted_proxy)
    # Delayed English-only originals can appear months after delivery; the URL date
    # remains an explicit availability proxy rather than an invented delivery date.
    term = voters.Roster().term_for(speaker, day)
    if not term:
        raise doc.Excluded('Author was not a voting Policy Board member on delivery date')
    if len(content) < config.MIN_TEXT_CHARS:
        raise ValueError(f'Insufficient prepared text: {len(content)} characters')
    return {'date': day, 'published_date': published or '', 'available_date': doc.available_day(day, published),
            'availability_basis': 'official_document_and_url_date_proxy', 'speaker': speaker,
            'role': term['role'], 'title': title, 'url': entry['url'], 'text_url': text_url,
            'source': 'Bank of Japan Japanese speech archive', 'language': language,
            'format': 'prepared_text', 'document_urls': json.dumps(attached), 'text': content}


def policy_documents(year):
    url = BASE + f'/en/mopo/mpmdeci/state_{year}/index.htm'
    page = tree(http_get.get(url).content)
    links = []
    for anchor in page.xpath('//main//a[@href]'):
        link = doc.canonical_url(urljoin(url, anchor.get('href')))
        # Statement filenames identify the meeting date; separate primary releases retain the same decision date.
        match = re.search(r'/k(\d{2})(\d{2})(\d{2})[a-z]?\.(?:pdf|htm)$', link)
        if re.search(r'^\(Reference\)', text(anchor), re.I):
            continue
        if match:
            day = f'20{match[1]}-{match[2]}-{match[3]}'
            if day <= dt.date.today().isoformat():
                links.append((day, link))
    if not links:
        raise ValueError(f'No policy statements in official year listing {url}')
    return sorted(set(links))


def extract_policy_context(content):
    """Preserve the actual multi-instrument guideline rather than guess a positive rate."""
    cleaned = re.sub(r'\s+', ' ', content).strip()
    start = re.search(r'(?:1\.\s*)?(?:Guideline for (?:Money Market|Market) Operations|The Bank (?:of Japan )?(?:will|decided)|At the Monetary Policy Meeting)', cleaned, re.I)
    excerpt = cleaned[start.start():start.start()+14000] if start else cleaned[:14000]
    if not re.search(r'monetary|funds.supplying|JGB|asset purchase|financing|interest rate', excerpt, re.I):
        raise ValueError('Official policy statement has no recognizable policy content')
    # Include the full decision portion, including YCC, purchases and forward guidance where present.
    return excerpt


def refresh_policy_rates(full=False):
    """Collect every missing policy statement in supported years; original PDFs are cached."""
    path = config.DATA / 'policy_context.json'
    old_contexts = json.loads(path.read_text()) if path.exists() else {}
    import hashlib
    contexts = {row['date']+'|'+hashlib.sha256(row['source'].encode()).hexdigest()[:12]: row for row in old_contexts.values()}
    start = dt.date.fromisoformat(voters.Roster().coverage_start()).year
    years_path = config.DATA / 'policy_years.json'
    completed_years = json.loads(years_path.read_text()) if years_path.exists() else {}
    years = [year for year in range(start - 1, dt.date.today().year + 1)
             if full or str(year) not in completed_years or year == dt.date.today().year]
    for year in years:
        for day, url in policy_documents(year):
            key = day+'|'+hashlib.sha256(url.encode()).hexdigest()[:12]
            if key in contexts:
                continue
            response = http_get.get(url)
            content = doc.pdf_text(response, minimum_chars=100) if doc.is_pdf(response, url) else text(tree(response.content))
            excerpt = extract_policy_context(content)
            contexts[key] = {'date': day, 'source': url, 'context': excerpt}
            import store
            store.atomic_json(path, contexts)
        completed_years[str(year)] = {'checked_at': dt.datetime.now(dt.timezone.utc).isoformat()}
        import store
        store.atomic_json(years_path, completed_years)
    if not contexts:
        raise ValueError('No dated BOJ policy statements collected')
    import policy
    policy._statements.cache_clear()
    return True
