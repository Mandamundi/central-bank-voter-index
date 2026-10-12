"""Extract speech metadata from page headers and PDF cover pages, and reject non-speech formats."""
from __future__ import annotations

import json
import html as html_std
import re
from urllib.parse import urlsplit
from lxml import html

ROLE_WORDS = {
    'assistant', 'associate', 'senior', 'executive', 'first', 'vice', 'president', 'chair',
    'chairman', 'chairwoman', 'governor', 'chief', 'officer', 'economist', 'director',
    'federal', 'reserve', 'bank', 'system', 'board', 'governors', 'remarks', 'speech',
    'statement', 'introduction', 'welcome', 'professor', 'conference', 'university',
}
ROLE = r'(?:(?:Assistant|Associate|Senior|Executive|First)\s+)?(?:Vice\s+)?(?:President|Chair(?:man|woman)?|Governor|Chief Executive Officer|Director)'
NAME = r"[A-ZÀ-ÖØ-Þ][\w.'’\-]*(?:[ \t]+[A-ZÀ-ÖØ-Þ][\w.'’\-]*){0,4}?"


def normalize_speaker(value: str | None) -> str | None:
    if not value:
        return None
    value = ' '.join(value.split()).strip(' ,;:')
    value = re.sub(r'^(?:Vice Chair(?:man)? for Supervision\s+)?and Chair of the Financial Stability Board\s+', '', value, flags=re.I)
    value = re.sub(r'^Vice Chair(?:man)? for Supervision(?: and Chair of the Financial Stability Board)?\s+', '', value, flags=re.I)
    value = re.sub(rf'^{ROLE}\s+(?:of\s+[^,]+,\s*)?', '', value, flags=re.I)
    value = re.sub(rf',\s*{ROLE}\b.*$', '', value, flags=re.I)
    # Some publisher metadata uses surname, given name.
    if ',' in value:
        parts = [part.strip() for part in value.split(',')]
        if len(parts) == 2 and parts[1].lower().strip('.') not in {'jr', 'sr'}:
            value = f'{parts[1]} {parts[0]}'
    if not re.fullmatch(NAME + r'(?:,?\s+(?:Jr\.?|Sr\.?|II|III|IV))?', value):
        return None
    tokens = [part.strip('.').lower() for part in value.split()]
    if not tokens or any(token in ROLE_WORDS for token in tokens):
        return None
    return value


def frontmatter(text: str) -> str:
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if re.search(r'the views expressed|views expressed here|its governors, officers', line, re.I):
            # PDFs sometimes put this disclaimer above the title instead of below it.
            continue
        if re.match(r'(?:Thank you|(?:I|We) [a-z]|Today[, ]|Let me|Good morning|Good afternoon)', line):
            break
        if len(line) > 240 and re.search(r'[.!?](?:\s|$)', line):
            break
        if line:
            lines.append(line)
    return '\n'.join(lines)


def byline(text: str) -> str | None:
    head = frontmatter(text)
    # Match a name on the line before a role, including assistant/senior vice presidents.
    lines = head.splitlines()
    for i, line in enumerate(lines):
        match = re.search(rf'(?:^|\bby\s+)({NAME}),?\s+{ROLE}\b', line, re.I)
        if match and (speaker := normalize_speaker(match.group(1))):
            return speaker
        if i and re.match(rf'^{ROLE}\b', line, re.I):
            candidate = re.sub(r'^(?:(?:Opening|Closing|Welcoming)\s+)?(?:Remarks|Speech|Address|Presented)\s+by\s+', '', lines[i-1], flags=re.I)
            if speaker := normalize_speaker(candidate):
                return speaker
        match = re.match(rf'^(?:(?:Opening|Closing|Welcoming)\s+)?(?:Remarks|Speech|Address|Presented)\s+by\s+({NAME})$', line, re.I)
        if match and (speaker := normalize_speaker(match.group(1))):
            return speaker
    return None


def document(page: str):
    try:
        return html.fromstring(page)
    except (ValueError, html.etree.ParserError):
        return html.fromstring('<html/>')


def node_text(node) -> str:
    return ' '.join(node.text_content().split())


def class_nodes(tree, name: str):
    return tree.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " ' + name + ' ")]')


def bank_metadata(source: str, page: str, text: str, url: str, parse_date, meta):
    tree = document(page)
    date = speaker = title = None
    if source == 'ny':
        match = re.search(r'/speeches/(\d{4})/[a-z]+(\d{6})(?:[a-z])?$', urlsplit(url).path, re.I)
        if match and match.group(1)[2:] == match.group(2)[:2]:
            date = parse_date(f'{match.group(1)}-{match.group(2)[2:4]}-{match.group(2)[4:]}')
    elif source == 'board':
        match = re.search(r'/speech/[a-z]+(\d{4})(\d{2})(\d{2})[a-z]?\.htm$', urlsplit(url).path, re.I)
        if match:
            date = parse_date('-'.join(match.groups()))
        for node in tree.xpath('//font'):
            heading = node_text(node)
            match = re.fullmatch(r'Remarks by (.+)', heading, re.I)
            if match and (person := normalize_speaker(match.group(1))):
                speaker = person
                date = date or parse_date(node_text(node.getparent().getparent()))
                break
        titles = tree.xpath('//title')
        if titles:
            match = re.match(r'FRB: Speech, .*? -- (.*?) -- ', node_text(titles[0]))
            if match:
                title = match.group(1)
    if source == 'minneapolis':
        date = parse_date(meta(page, 'ess:publishdatetime', 'sortDate'))
        scripts = tree.xpath('//script[@id="__NEXT_DATA__"]/text()')
        if scripts:
            try:
                route = json.loads(scripts[0])['props']['pageProps']['layoutData']['sitecore']['route']
                authors = []
                for component in route.get('placeholders', {}).get('author', []):
                    authors.extend(component.get('fields', {}).get('authors', []))
                names = {a['name'] for a in authors if a.get('name')}
                if len(names) == 1:
                    speaker = names.pop()
            except (ValueError, KeyError, TypeError):
                pass
    elif source == 'ny':
        contacts = class_nodes(tree, 'ts-contact-info')
        for node in contacts:
            date = date or parse_date(node_text(node))
            names = [node_text(a) for a in node.xpath('.//a')]
            if len(names) == 1:
                speaker = normalize_speaker(names[0]) or speaker
        titles = class_nodes(tree, 'ts-article-title')
        title = node_text(titles[0]) if titles else None
        # Old pages have an HTML center/byline block that the body extractor can omit.
        if not speaker:
            for center in tree.xpath('//center') + [n.getparent() for n in tree.xpath('//h3')] + tree.xpath('//b|//strong'):
                speaker = byline('\n'.join(center.itertext()))
                if speaker:
                    break
    elif source == 'philadelphia':
        authors = class_nodes(tree, 'authors')
        if authors:
            names = [node_text(a) for a in authors[0].xpath('./li')]
            if len(names) == 1:
                speaker = normalize_speaker(names[0])
    elif source == 'sanfrancisco':
        speaker = meta(page, 'parsely-author', 'citation_author') or None
        date = parse_date(meta(page, 'parsely-pub-date', 'article:published_time'))
    elif source == 'dallas':
        for node in class_nodes(tree, 'report-date') + class_nodes(tree, 'date'):
            date = parse_date(node_text(node))
            if date:
                break
        if not date:
            # Legacy filename dates also rescue a publisher's truncated header year.
            match = re.search(r'/(\d{4})/(?:fs|rsk|rm|rs|kr)(\d{2})(\d{2})(\d{2})[a-z_]*(?:\.aspx)?$', urlsplit(url).path, re.I)
            if match and match.group(1)[2:] == match.group(2):
                date = parse_date(f'{match.group(1)}-{match.group(3)}-{match.group(4)}')
        headings = class_nodes(tree, 'dal-headline')
        if headings:
            title = node_text(headings[0])
        for node in class_nodes(tree, 'dal-crouton'):
            author = re.sub(r'^(?:Speech|Essay) by\s+', '', node_text(node), flags=re.I)
            speaker = normalize_speaker(author)
            if speaker:
                break
    elif source == 'chicago':
        # URL prefixes encode the delivery date; "Last Updated" is not the speech date.
        match = re.search(r'/publications/speeches/(\d{4})/(\d{1,2})-(\d{1,2})(?:-\d{2})?(?:-|$)', urlsplit(url).path)
        if match:
            year, month, day = match.groups()
            date = parse_date(f'{year}-{int(month):02}-{int(day):02}')
        else:
            match = re.search(r'/publications/speeches/(\d{4})/([a-z]+)-(\d{1,2})(?:-|$)', urlsplit(url).path, re.I)
            if match:
                date = parse_date(f'{match.group(2).capitalize()} {match.group(3)}, {match.group(1)}')
        for node in class_nodes(tree, 'cfedDetail__introduction'):
            delivery = node_text(node)
            if re.search(r'\b(?:speech|remarks|comments) (?:delivered|presented)\b', delivery, re.I):
                date = parse_date(delivery) or date
            speaker = speaker or byline(delivery)
        # Some legacy pages omit a byline but carry the speaker's own biography
        # in the article sidebar. Avoid general navigation and body citations.
        names = set()
        for sidebar in tree.xpath('//*[contains(@class,"cfedSidebar")]'):
            for link in sidebar.xpath('.//a'):
                match = re.fullmatch(r"(.+?)(?:[’']\s*s?\s*|\s+)(?:Biography|Speech Schedule)", node_text(link), re.I)
                if match and (name := normalize_speaker(match.group(1))):
                    names.add(name)
        if len(names) == 1:
            speaker = names.pop()
        speaker = speaker or byline(meta(page, 'description'))
        if not speaker:
            for node in class_nodes(tree, 'cfedContent__text'):
                speaker = byline('\n'.join(node.itertext()))
                if speaker:
                    break
        if not speaker:
            for node in tree.xpath('//strong|//p'):
                match = re.match(r'Note: Opinions expressed in this article are those of (.+?) and do not necessarily reflect', node_text(node), re.I)
                if match and (person := normalize_speaker(match.group(1))):
                    speaker = person
                    break
        if not speaker:
            introduction = re.search(rf"\bI(?: am|'m|’m)\s+({NAME}),\s*(?:the\s+)?{ROLE}\b", text[:1500], re.I)
            if introduction:
                speaker = normalize_speaker(introduction.group(1))
    if not date:
        for name in ('article-date', 'article__time', 'speech-date'):
            for node in class_nodes(tree, name):
                date = parse_date(node.get('datetime', '') or node_text(node))
                if date:
                    break
            if date:
                break
    return date, normalize_speaker(speaker), title


def non_speech_reason(page: str, text: str, title: str) -> str | None:
    tree = document(page)
    header = text[:1500]
    if re.match(r'^\s*(?:\|\s*)?Paper by\b', header, re.I):
        return 'research paper rather than prepared remarks'
    contributors = set()
    for heading in tree.xpath('//h4'):
        match = re.match(rf'^(?:by\s+)?({NAME}),\s*(?:First Vice President|Deputy General Counsel|Counsel)\b', node_text(heading), re.I)
        if match and (person := normalize_speaker(match.group(1))):
            contributors.add(person)
    if len(contributors) > 1:
        return 'joint-author publication cannot be assigned to one speaker'
    if re.search(r'\bspeech cancel(?:led|ed)\b', header, re.I):
        return 'cancelled speech without prepared remarks'
    if re.search(r'\bsat down to reflect\b', text[:1800], re.I) and class_nodes(tree, 'cfedArticle__authorBlock'):
        return 'retrospective interview rather than prepared remarks'
    for raw in tree.xpath('//meta[@name="brightspot-dataLayer"]/@content'):
        try:
            if json.loads(raw).get('pageType') == 'radio-episode':
                return 'radio interview rather than prepared remarks'
        except (ValueError, TypeError):
            pass
    if any(re.match(r'Essay by\b', node_text(node), re.I) for node in class_nodes(tree, 'dal-crouton')):
        return 'essay rather than prepared remarks'
    if re.match(r'\s*For immediate release\b', header, re.I) or re.search(r'\bpress release\b', title, re.I):
        return 'press release'
    if re.search(r'\b(?:webinar|interview|Q\s*&\s*A|question.and.answer|fireside chat|conversation|town hall|podcast|testimony|written statement)\b', title, re.I):
        # Published opening remarks can be prepared text even at a conversation/panel.
        if not re.match(r'opening remarks\b', title, re.I):
            return 'discussion, interview, or statement'
    if re.search(r'^(?:View photos|Photos? from|Photo gallery)\b', title, re.I):
        return 'photo gallery'
    # Transcripts with multiple named turns are discussions, not prepared speech text.
    turns = re.findall(r'^([A-Z][\w.\-]+(?: (?:[A-Z][\w.\-]+|de|van|von)){1,5}):', text, re.M)
    if len(set(turns)) > 1:
        return 'multi-speaker transcript'
    if re.search(r'^(?:Statement of|Written statement|Testimony of)\b', header, re.M | re.I):
        return 'statement or testimony'
    if (re.search(r'^\s*(?:Dear\b|To:\s)', header, re.M | re.I)
            and re.search(r'^\s*(?:Sincerely|Yours truly|Respectfully)[,\s]', text, re.M | re.I)):
        return 'letter or memorandum'
    if len(text) < 1000 and not short_prepared_text(text):
        # Atlanta's article body sometimes consists solely of its publisher teaser.
        # This is an event listing, even if its taxonomy says "Speech".
        teasers = tree.xpath('//meta[@name="afo_teaser"]/@content')
        articles = class_nodes(tree, 'article-frame')
        if teasers and articles:
            teaser = node_text(document('<div>' + html_std.unescape(teasers[0]) + '</div>'))
            article = node_text(articles[0])
            if len(teaser) > 40 and article.endswith(teaser) and len(article) - len(teaser) < 300:
                return 'event summary without prepared text'
        if re.search(r'\b(?:Figures presented|These figures were presented|presentation figures)\b', header, re.I):
            return 'presentation figures without prepared speech text'
        if re.search(r'\b(?:Slides presented|These slides were presented|Presentation slides available)\b', header, re.I):
            return 'slides without prepared speech text'
        if re.search(r'\b(?:livestreamed|moderated Q\s*&\s*A)\b', header, re.I):
            return 'event without prepared text'
        if re.search(r'\b(?:A conversation between|Watch the Video)\b', header, re.I):
            return 'discussion or video without prepared text'
        if re.search(r'(?:youtube\.com/(?:watch|embed)|youtu\.be/|vimeo\.com/)', header, re.I):
            return 'video event without prepared text'
        if re.search(r'\b(?:fireside chat|moderated conversation|moderated discussion)\b', header, re.I):
            return 'discussion without prepared text'
        if re.match(r'(?:(?:In-person|Virtual) (?:keynote )?presentation\b|Presentation(?:,\s| on\b| with\b)|Recording\s+Panel with\b)', header, re.I):
            return 'presentation without prepared speech text'
        if re.search(r'\bcontributed a chapter\b|\bchapter by President\b', header, re.I):
            return 'publication announcement without prepared speech text'
        if re.search(r'\b(?:[A-Z][\w]+ (?:delivered|participated|spoke|speaks|discussed|discusses|provided|shared remarks)|President(?: \w+)? (?:participates|speaks|discusses))\b', header, re.I) and not short_prepared_text(text):
            return 'event summary without prepared text'
        if re.search(r'\b(?:president|chief executive officer)\b[^.!?]{0,100}\b(?:participated|delivered)\b', header, re.I):
            return 'event summary without prepared text'
        teaser = ' '.join(n.get('content', '') for n in tree.xpath('//meta[@name="afo_teaser"]'))
        if re.search(r'watch (?:the )?videos?|video of|watch .*speech', teaser, re.I):
            return 'video event without prepared text'
        description = ' '.join(tree.xpath('//meta[@name="twitter:description" or @name="description"]/@content'))
        if re.search(r'\bin a new video\b', description, re.I):
            return 'video event without prepared text'
        pdfs = [a.get('href', '') for a in tree.xpath('//a[@href]') if '.pdf' in a.get('href','').lower()]
        if pdfs and all(re.search(r'(?:paper|slides?|charts?|research)', link, re.I) for link in pdfs):
            return 'research paper or slides without prepared speech text'
        if re.search(r'(?:Download MP3|Download audio)', header, re.I):
            return 'audio event without prepared text'
        # Look within the article, excluding footer/social links common to all pages.
        for node in tree.xpath('//main | //article | //*[@class="ts-article-text"] | //*[@class="cfedDetail__content"] | //*[@class="cfedContent__text"] | //*[@id="main"]'):
            if node.xpath('.//iframe[contains(@src,"youtube") or contains(@src,"vimeo") or contains(@src,"brightcove") or contains(@src,"kaltura")]'):
                return 'video event without prepared text'
    return None


def looks_like_slides(pages: list[str]) -> bool:
    if len(pages) < 2:
        return False
    body = pages[1:]
    slide_pages = 0
    for page in body:
        lines = [line.strip() for line in page.splitlines() if line.strip()]
        if not lines:
            continue
        long_lines = sum(len(line) >= 65 for line in lines) / len(lines)
        # Chart axes commonly contain repeated "Sep."/"Oct." labels. Those are
        # not sentences; counting them can make a chart deck look like prose.
        sentences = len(re.findall(r'\b[a-z]{3,}[.!?](?:\s|$)', page))
        bullets = sum(bool(re.match(r'[•●▪–]|[-−]\s', line)) for line in lines)
        if (long_lines < .25 and sentences < 4) or bullets >= 3:
            slide_pages += 1
    return slide_pages / len(body) >= .7


def short_prepared_text(text: str) -> bool:
    """Short opening remarks are valid; an event summary is not a speech body."""
    if len(text.strip()) < 200 or len(text.split()) < 40:
        return False
    return bool(re.search(r'(?:^|\n)(?:Thank you|Good morning|Good afternoon|I (?:am|want|will|would|appreciate)|Let me)\b', text))
