"""Speech sources: the Board of Governors and the twelve Reserve Banks, read from their own sites.

Each source has a lister that yields entries (url plus whatever date/speaker/title the listing
gives). lister(full=False) returns only the newest entries, for the daily update; full=True walks
the whole archive the site publishes, for the backfill. fetch() then reads the page (or its PDF)
and fills in what the listing did not give.

Only prepared speech text is kept: pages whose title marks an interview, Q&A, podcast,
conversation or statement are skipped, and so are pages with less than MIN_TEXT_CHARS of text
(video-only event pages).

Also here: the Fed's target-rate history, used as prompt context.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import io
import json
import re
from typing import Callable, Iterator

import trafilatura
from pypdf import PdfReader

import config
import http_get

MONTHS = "January February March April May June July August September October November December".split()
MONTH_NUM = {m: i + 1 for i, m in enumerate(MONTHS)}
MONTH_NUM.update({m[:3]: i + 1 for i, m in enumerate(MONTHS)})
MONTH_NUM.update({"Sept": 9})

EXCLUDE_TITLE = re.compile(
    r"\b(?:Q\s*&\s*A|interview|podcast|fireside chat|conversation|statement (?:from|regarding|on|by)|"
    r"town hall|moderated discussion|video)\b",
    re.I,
)
BYLINE = re.compile(
    r"(?:by |^|\n)\s*([A-Z][a-zA-Z.'\-]+(?: [A-Z][a-zA-Z.'\-]+){1,3}),?\s+(?:the\s+)?(?:President|Chair|Governor|Vice Chair)",
)


# ---------------------------------------------------------------- helpers

def text_of(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_date(s: str) -> str | None:
    """ISO date from 'September 29, 2026', 'Sep 29, 2026', 'Sept. 29, 2026', '12/1/2023', '2026-09-29'."""
    s = s.strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"\b([A-Z][a-z]{2,8})\.? (\d{1,2}),? (\d{4})", s)
    if m and m.group(1) in MONTH_NUM:
        return dt.date(int(m.group(3)), MONTH_NUM[m.group(1)], int(m.group(2))).isoformat()
    m = re.search(r"\b(\d{1,2}) ([A-Z][a-z]{2,8})\.? (\d{4})", s)          # RSS: "Tue, 22 Sep 2026"
    if m and m.group(2) in MONTH_NUM:
        return dt.date(int(m.group(3)), MONTH_NUM[m.group(2)], int(m.group(1))).isoformat()
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", s)
    if m:
        return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()
    return None


def html_text(page: str) -> str:
    return trafilatura.extract(page, include_comments=False, include_tables=False, favor_recall=True) or ""


def pdf_text(url: str) -> str:
    reader = PdfReader(io.BytesIO(http_get.get(url).content))
    return "\n".join(p.extract_text() or "" for p in reader.pages)


def absolute(base: str, href: str) -> str:
    href = html.unescape(href)
    if href.startswith("http"):
        return href
    return base + ("" if href.startswith("/") else "/") + href


def meta(page: str, *names: str) -> str:
    for name in names:
        m = re.search(rf'<meta\s+(?:name|property|itemprop)="{re.escape(name)}"\s+content="([^"]*)"', page) or \
            re.search(rf'<meta\s+content="([^"]*)"\s+(?:name|property|itemprop)="{re.escape(name)}"', page)
        if m and m.group(1).strip():
            return html.unescape(m.group(1)).strip()
    return ""


def name_from_slug(slug: str) -> str:
    """'thomas_i_barkin' / 'mary-c-daly' -> 'Thomas I Barkin' / 'Mary C Daly'."""
    return " ".join(p.capitalize() for p in re.split(r"[_\-]+", slug) if p)


def entry(source, url, date=None, speaker=None, title=None, pdf=None) -> dict:
    return {"source": source, "url": url, "date": date, "speaker": speaker, "title": title, "pdf": pdf}


# ---------------------------------------------------------------- Board of Governors

FED = "https://www.federalreserve.gov"


def _board_year_page(year: int) -> str | None:
    for url in (f"{FED}/newsevents/{year}-speeches.htm", f"{FED}/newsevents/speech/{year}-speeches.htm",
                f"{FED}/newsevents/speech/{year}speech.htm"):
        try:
            return http_get.get(url).text
        except Exception:
            continue
    return None


def _strip_title(speaker: str) -> str:
    return re.sub(r"^(?:Vice Chair(?:man)? for Supervision|Vice Chair(?:man)?|Chair(?:man)?|Governor)\s+", "", speaker.strip())


def list_board(full: bool) -> Iterator[dict]:
    if not full:
        rss = http_get.get(f"{FED}/feeds/speeches.xml").text
        for item in re.findall(r"<item>(.*?)</item>", rss, re.S):
            link = re.search(r"<link>(?:<!\[CDATA\[)?\s*([^<\]\s]+)", item)
            if link:
                yield entry("board", link.group(1))       # speaker/date read from the page
        return
    for year in range(1996, dt.date.today().year + 1):
        page = _board_year_page(year)
        if not page:
            print(f"  board: no speech index for {year}")
            continue
        # current layout: <time>12/1/2023</time> ... <a href=...><em>Title</em></a> ... <p class="news__speaker">
        for m in re.finditer(r"<time>([\d/]+)</time>.*?<a href=\"([^\"]+)\"[^>]*>(.*?)</a>.*?news__speaker\">(.*?)</p>", page, re.S):
            yield entry("board", absolute(FED, m.group(2)), parse_date(m.group(1)), _strip_title(text_of(m.group(4))), text_of(m.group(3)))
        # older layout: <li> December 14, 2005 <div class="title"><a ...>Title</a></div><div class="speaker">...
        # (title and speaker come in either order)
        for li in re.findall(r"<li>\s*([A-Z][a-z]+ \d{1,2}, \d{4}\s*<div.*?)</li>", page, re.S):
            link = re.search(r'<div class="title">\s*<a href="([^"]+)"[^>]*>(.*?)</a>', li, re.S)
            speaker = re.search(r'<div class="speaker">(.*?)</div>', li, re.S)
            if link:
                yield entry("board", absolute(FED, link.group(1)), parse_date(li[:30]),
                            _strip_title(text_of(speaker.group(1))) if speaker else None, text_of(link.group(2)))


def board_page_meta(page: str, text: str) -> tuple[str | None, str | None, str | None]:
    speaker = re.search(r'<p class="speaker">(.*?)</p>', page, re.S)
    date = re.search(r"<p class=.article__time.>(.*?)</p>", page, re.S)
    title = re.search(r"<h3 class=.title.>(.*?)</h3>", page, re.S)
    return (parse_date(text_of(date.group(1))) if date else None,
            _strip_title(text_of(speaker.group(1))) if speaker else None,
            text_of(title.group(1)) if title else None)


# ---------------------------------------------------------------- New York

NY = "https://www.newyorkfed.org"


def list_ny(full: bool) -> Iterator[dict]:
    page = http_get.get(f"{NY}/newsevents/speeches/index").text
    n = 0
    for m in re.finditer(r"(\w{3} \d{1,2}, \d{4})\s*</div>\s*</td>.*?href=\"(/newsevents/speeches/\d{4}/[^\"]+)\"[^>]*>(.*?)</a>", page, re.S):
        title = text_of(m.group(3))
        who, _, rest = title.partition(":")         # titles are "Williams: Unwavering Dedication"
        yield entry("ny", NY + m.group(2), parse_date(m.group(1)), who.strip() if rest else None, rest.strip() or title)
        n += 1
        if not full and n >= 30:
            return


# ---------------------------------------------------------------- Boston

BOSTON = "https://www.bostonfed.org"


def list_boston(full: bool) -> Iterator[dict]:
    page = http_get.get(f"{BOSTON}/news-and-events/speeches.aspx").text
    n = 0
    # <h1 class="card-title"><a href=...>Title</a></h1> <ul class="speaker">..name..</ul> <p class="date-and-location"> date
    for m in re.finditer(r'<h1 class="card-title">\s*<a href="(/news-and-events/speeches/[^"]+)"[^>]*>(.*?)</a>.*?'
                         r'<ul class="list-inline speaker">(.*?)</ul>\s*<p class="date-and-location">(.*?)</p>', page, re.S):
        speakers = [text_of(s).split(",")[0] for s in re.findall(r"<small>(.*?)</small>", m.group(3), re.S)]
        yield entry("boston", BOSTON + m.group(1), parse_date(text_of(m.group(4))),
                    speakers[0] if len(speakers) == 1 else None, text_of(m.group(2)))
        n += 1
        if not full and n >= 30:
            return


# ---------------------------------------------------------------- Philadelphia

PHIL = "https://www.philadelphiafed.org"


def _sitemap(url: str) -> list[str]:
    return [html.unescape(u) for u in re.findall(r"<loc>\s*([^<\s]+)", http_get.get(url).text)]


def list_philadelphia(full: bool) -> Iterator[dict]:
    # The site's speech list is rendered client-side; its sitemap lists every page. Speeches are
    # pages under /the-economy/monetary-policy/ whose slug starts with the date (yymmdd-).
    urls = [u for u in _sitemap(f"{PHIL}/sitemap.xml") if re.search(r"/the-economy/monetary-policy/\d{6}-", u)]
    urls.sort(key=lambda u: re.search(r"/(\d{6})-", u).group(1), reverse=True)
    for u in urls if full else urls[:30]:
        y, mth, d = re.search(r"/(\d{2})(\d{2})(\d{2})-", u).groups()
        yield entry("philadelphia", u, f"20{y}-{mth}-{d}")


def philadelphia_meta(page: str, text: str) -> tuple[str | None, str | None, str | None]:
    pdf = re.search(r"/speeches/([a-z\-]+)/(\d{4})/", page)       # PDF path names the speaker
    return None, (name_from_slug(pdf.group(1)) if pdf else None), None


# ---------------------------------------------------------------- Cleveland

CLEVELAND = "https://www.clevelandfed.org"
_CLEVELAND_API = (f"{CLEVELAND}/frbc/sitesearch/results?o=field.sortDate.desc&e={{offset}}&p=10&bcu=true"
                  "&ppn=%2Fcollections%2Fspeeches&itemid=%7B7D253265-C90E-41F8-AD24-BBC7B68699B7%7D"
                  "&il=list&contentType=Speech&collection=Speeches")


def list_cleveland(full: bool) -> Iterator[dict]:
    offset = 0
    while True:
        data = http_get.get(_CLEVELAND_API.format(offset=offset)).json()
        for r in data.get("results") or []:
            authors = [a["label"] for a in r.get("authors") or []]
            yield entry("cleveland", CLEVELAND + r["url"], r["releaseDate"][:10],
                        authors[0] if len(authors) == 1 else None, r.get("title"), r.get("pdf_Url"))
        offset += 10
        if not full or not data["pagingInfo"].get("hasNextPage"):
            return


# ---------------------------------------------------------------- Richmond (RSS holds the whole archive)

RICHMOND = "https://www.richmondfed.org"


def list_richmond(full: bool) -> Iterator[dict]:
    rss = http_get.get(f"{RICHMOND}/press_room/speeches?cc_view=rss").text
    for n, item in enumerate(re.findall(r"<item>(.*?)</item>", rss, re.S)):
        if not full and n >= 30:
            return
        def tag(name):
            m = re.search(rf"<{name}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{name}>", item, re.S)
            return html.unescape(m.group(1)).strip() if m else ""
        link = tag("link")
        who = re.search(r"/speeches/([a-z_]+)/\d{4}/", link)
        yield entry("richmond", link, parse_date(tag("pubDate")), name_from_slug(who.group(1)) if who else None, tag("title"))


# ---------------------------------------------------------------- Atlanta

ATLANTA = "https://www.atlantafed.org"


def list_atlanta(full: bool) -> Iterator[dict]:
    # Only recent items are on the speeches page; the sitemap lists every speech page.
    # Speech URLs carry the date: /news(-and-events)/speeches/yyyy/mm/dd/slug
    urls = [u for u in _sitemap(f"{ATLANTA}/~/media/Project/Atlanta/FRBA/Documents/sitemap")
            if re.search(r"/speeches/\d{4}/\d{2}/\d{2}/", u)]
    urls.sort(reverse=True, key=lambda u: re.search(r"/(\d{4}/\d{2}/\d{2})/", u).group(1))
    for u in urls if full else urls[:30]:
        y, mth, d = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", u).groups()
        yield entry("atlanta", u, f"{y}-{mth}-{d}")


def atlanta_meta(page: str, text: str) -> tuple[str | None, str | None, str | None]:
    return parse_date(meta(page, "afo_contentDate")), re.sub(r"\s+", " ", meta(page, "afo_author")) or None, None


# ---------------------------------------------------------------- Chicago

CHICAGO = "https://www.chicagofed.org"


def list_chicago(full: bool) -> Iterator[dict]:
    pages = [f"{CHICAGO}/utilities/about-us/office-of-the-president/office-of-the-president-speaking"]
    if full:
        pages.append(f"{CHICAGO}/publications/speeches/speech-archive")
        pages += [f"{CHICAGO}/publications/speeches/{y}/index" for y in range(2015, dt.date.today().year + 1)]
    seen = set()
    for url in pages:
        try:
            page = http_get.get(url).text
        except Exception:
            continue
        for m in re.finditer(r'href="(/publications/speeches/\d{4}/[^"#?]+)"[^>]*>(.*?)</a>(.{0,600})', page, re.S):
            if m.group(1).endswith("/index") or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            # the listing gives the date right after the title ("... November 14, 2019 | Print Version")
            date = parse_date(text_of(m.group(3)).split("|")[0])
            yield entry("chicago", CHICAGO + m.group(1), date, title=text_of(m.group(2)) or None)


def chicago_meta(page: str, text: str) -> tuple[str | None, str | None, str | None]:
    # Pages carry the byline in the text ("A speech presented by Austan D. Goolsbee, president ... on April 11, 2023")
    # or only in the print-version PDF.
    head = text[:600]
    m = re.search(r"presented by ([A-Z][\w.\- ]+?), president", head, re.I)
    date = re.search(r"\bon ([A-Z][a-z]+ \d{1,2}, \d{4})", head)
    speaker = m.group(1) if m else None
    if not speaker:
        pdf = re.search(r'href="(/-/media/publications/speeches/[^"]+\.pdf[^"]*)"', page)
        if pdf:
            try:
                first = pdf_text(absolute(CHICAGO, pdf.group(1)))[:800]
                b = BYLINE.search(first)
                speaker = b.group(1) if b else None
            except Exception:
                pass
    return (parse_date(date.group(1)) if date else None), speaker, None


# ---------------------------------------------------------------- St. Louis

STL = "https://www.stlouisfed.org"
_STL_API = (STL + "//sxa/search/results/?s=%7BDA5A2E95-2DA8-48EF-8A05-C6C4DEE314B1%7D"
            "&itemid=%7B171B272E-9D98-430F-9F07-825319267544%7D&sig=srremarks&p={size}&e={offset}"
            "&o=Manually%20Updated%2CDescending&v=%7B073ED7F6-4F06-4C6D-9FF4-09BD85939FDC%7D")


def list_stlouis(full: bool) -> Iterator[dict]:
    offset, size = 0, 20
    while True:
        data = http_get.get(_STL_API.format(size=size, offset=offset)).json()
        for r in data.get("Results") or []:
            # each item is one listing line; only lines linking to a page on stlouisfed.org are the bank's own texts
            link = re.search(r'<a href="(/from-the-president/[^"]+)"[^>]*>(.*?)</a>', r.get("Html") or "", re.S)
            if not link:
                continue
            line = text_of(r["Html"])
            date = re.search(r"\(([A-Z][a-z]+\.? \d{1,2}, \d{4})\)\.?$", line)
            # the link text is the title ("How and Why"), or says what the item is ("Interview with ...")
            yield entry("stlouis", STL + link.group(1), parse_date(date.group(1)) if date else None, title=text_of(link.group(2)))
        offset += size
        if not full or offset >= data.get("Count", 0):
            return


def stlouis_meta(page: str, text: str) -> tuple[str | None, str | None, str | None]:
    # the speech PDF sits under the president's folder (.../stlouisfed/musalem/2026/...); titles are "Musalem: How and Why"
    pdf = re.search(r"/stlouisfed/([a-z]+)/\d{4}/[^\"]+\.pdf", page)
    who, _, rest = meta(page, "og:title").partition(":")
    if pdf:
        return None, pdf.group(1).capitalize(), None
    return None, (who if rest and re.fullmatch(r"[A-Z][a-z]+", who) else None), None


# ---------------------------------------------------------------- Minneapolis

MPLS = "https://www.minneapolisfed.org"


def list_minneapolis(full: bool) -> Iterator[dict]:
    if full:
        urls = [u for u in _sitemap(f"{MPLS}/sitemap.xml") if re.search(r"minneapolisfed\.org/speeches/\d{4}/", u)]
    else:
        page = http_get.get(f"{MPLS}/publications-archive/all-speeches").text
        urls = [MPLS + u for u in dict.fromkeys(re.findall(r'href="(/speeches/\d{4}/[^"#?]+)"', page))]
    for u in urls:
        yield entry("minneapolis", u.replace("://minneapolisfed.org", "://www.minneapolisfed.org"))


# ---------------------------------------------------------------- Kansas City

KC = "https://www.kansascityfed.org"


def list_kansascity(full: bool) -> Iterator[dict]:
    if full:
        urls = [u for u in _sitemap(f"{KC}/sitemap.xml") if re.search(r"kansascityfed\.org/speeches/[^/]+/$", u)
                and "speakers-bureau" not in u]
    else:
        page = http_get.get(f"{KC}/speeches/").text
        urls = [KC + u for u in dict.fromkeys(re.findall(r'href="(/speeches/[^"/]+/)"', page)) if "speakers-bureau" not in u]
    for u in urls:
        yield entry("kansascity", u)


def kansascity_meta(page: str, text: str) -> tuple[str | None, str | None, str | None]:
    date = re.search(r'class="article-date"><time datetime="([\d-]+)"', page)
    author = re.search(r'class="author-card-title-name">.*?>([^<]+)</mnt-link>', page, re.S)
    return (date.group(1) if date else None), (author.group(1).strip() if author else None), None


# ---------------------------------------------------------------- Dallas

DALLAS = "https://www.dallasfed.org"
DALLAS_PRESIDENT_PAGES = ["logan", "kaplan", "fisher", "mcteer"]   # one listing page per president on the site


def list_dallas(full: bool) -> Iterator[dict]:
    if full:
        pages = [http_get.get(f"{DALLAS}/news/speeches/{p}").text for p in DALLAS_PRESIDENT_PAGES]
        links = [l for page in pages for l in re.findall(r'href="(/news/speeches/[a-z]+/\d{4}/[^"#?]+)"', page)]
    else:
        rss = http_get.get(f"{DALLAS}/rss/speeches.xml").text
        links = [re.sub(r"^https://www\.dallasfed\.org", "", l) for l in re.findall(r"<link>\s*([^<\s]+/news/speeches/[a-z]+/\d{4}/[^<\s]+)", rss)]
    for link in dict.fromkeys(links):
        who = re.search(r"/news/speeches/([a-z]+)/", link).group(1)
        ymd = re.search(r"(\d{2})(\d{2})(\d{2})(?:[a-z])?$", link)          # .../logan/2026/lkl260716
        date = f"{link.split('/')[4]}-{ymd.group(2)}-{ymd.group(3)}" if ymd and link.split('/')[4][2:] == ymd.group(1) else None
        yield entry("dallas", DALLAS + link, date, speaker=None if who == "speeches-leaders" else who.capitalize())


# ---------------------------------------------------------------- San Francisco

SF = "https://www.frbsf.org"
SF_PRESIDENT_PAGES = ["mary-c-daly", "williams-speeches", "yellen-speeches", "parry-speeches"]


def list_sanfrancisco(full: bool) -> Iterator[dict]:
    for slug in SF_PRESIDENT_PAGES if full else SF_PRESIDENT_PAGES[:1]:
        page_no = 1
        while True:
            url = f"{SF}/news-and-media/speeches/{slug}/" + (f"page/{page_no}/" if page_no > 1 else "")
            try:
                page = http_get.get(url).text
            except Exception:
                break
            found = 0
            for m in re.finditer(r'<h2 class="wp-block-post-title[^"]*"><a href="([^"]+)"[^>]*>(.*?)</a></h2>\s*'
                                 r'<div[^>]*wp-block-post-date[^>]*><time datetime="([^"]+)"', page, re.S):
                yield entry("sanfrancisco", m.group(1), m.group(3)[:10], None, text_of(m.group(2)))
                found += 1
            if not found or not full or f"page/{page_no + 1}/" not in page:
                break
            page_no += 1


def sanfrancisco_meta(page: str, text: str) -> tuple[str | None, str | None, str | None]:
    who = re.search(r"/news-and-media/speeches/([a-z\-]+?)(?:-speeches)?/", page)
    m = re.search(r'speech-series-([a-z\-]+)', page)
    slug = m.group(1) if m else (who.group(1) if who else "")
    return None, (name_from_slug(slug.replace("-speeches", "")) if slug else None), None


# ---------------------------------------------------------------- registry

SOURCES: dict[str, tuple[str, Callable[[bool], Iterator[dict]], Callable | None]] = {
    # name: (role passed to the scorer, lister, page metadata reader)
    "board": ("Board of Governors of the Federal Reserve System", list_board, board_page_meta),
    "ny": ("Federal Reserve Bank of New York", list_ny, None),
    "boston": ("Federal Reserve Bank of Boston", list_boston, None),
    "philadelphia": ("Federal Reserve Bank of Philadelphia", list_philadelphia, philadelphia_meta),
    "cleveland": ("Federal Reserve Bank of Cleveland", list_cleveland, None),
    "richmond": ("Federal Reserve Bank of Richmond", list_richmond, None),
    "atlanta": ("Federal Reserve Bank of Atlanta", list_atlanta, atlanta_meta),
    "chicago": ("Federal Reserve Bank of Chicago", list_chicago, chicago_meta),
    "stlouis": ("Federal Reserve Bank of St. Louis", list_stlouis, stlouis_meta),
    "minneapolis": ("Federal Reserve Bank of Minneapolis", list_minneapolis, None),
    "kansascity": ("Federal Reserve Bank of Kansas City", list_kansascity, kansascity_meta),
    "dallas": ("Federal Reserve Bank of Dallas", list_dallas, None),
    "sanfrancisco": ("Federal Reserve Bank of San Francisco", list_sanfrancisco, sanfrancisco_meta),
}


# ---------------------------------------------------------------- reading one speech

def make_id(date: str, speaker: str, title: str) -> str:
    """Stable id, so the same speech posted twice (or on two sites) is stored once."""
    surname = speaker.split()[-1].lower() if speaker else ""
    t = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    return hashlib.sha1(f"{date}|{surname}|{t}".encode()).hexdigest()[:12]


def _generic_meta(page: str, text: str) -> tuple[str | None, str | None, str | None]:
    """(date, speaker, title) from common page metadata and the opening lines of the text."""
    date = parse_date(meta(page, "article:published_time", "publication_date", "citation_publication_date",
                           "citation_date", "DC.date", "date", "afo_contentDate"))
    if not date:
        date = parse_date(text[:400])
    b = BYLINE.search(text[:800])
    title = meta(page, "og:title", "citation_title", "twitter:title")
    if not title:
        t = re.search(r"<title>(.*?)</title>", page, re.S)
        title = text_of(t.group(1)) if t else ""
    title = re.split(r"\s+[|\-–]\s+(?:Federal Reserve|FRB|Dallas Fed|St\. Louis Fed|San Francisco Fed)", title)[0]
    return date, (b.group(1) if b else None), title or None


def _speech_pdf(page: str, url: str) -> str | None:
    """The speech text PDF linked from a page whose HTML holds only a summary (not slides or charts)."""
    for href in re.findall(r'href="([^"]+\.pdf)[^"]*"', page):
        if re.search(r"speech", href, re.I) and not re.search(r"slide|chart|exhibit|presentation", href, re.I):
            return absolute(re.match(r"https?://[^/]+", url).group(0), href)
    return None


def fetch(e: dict) -> tuple[dict | None, str]:
    """Read one listed speech. Returns (record, '') or (None, reason it was skipped)."""
    role, _, page_meta = SOURCES[e["source"]]
    if e.get("title") and EXCLUDE_TITLE.search(e["title"]):
        return None, "not a speech (title)"
    if e["url"].lower().split("?")[0].endswith(".pdf"):
        page, text = "", pdf_text(e["url"])
    else:
        page = http_get.get(e["url"]).text
        text = html_text(page)
        if len(text) < config.MIN_TEXT_CHARS:
            pdf = e.get("pdf") or _speech_pdf(page, e["url"])
            if pdf:
                text = pdf_text(pdf)
    date, speaker, title = e.get("date"), e.get("speaker"), e.get("title")
    if page_meta and page:
        d, s, t = page_meta(page, text)
        date, speaker, title = date or d, speaker or s, title or t
    if page and not (date and speaker and title):
        d, s, t = _generic_meta(page, text)
        date, speaker, title = date or d, speaker or s, title or t
    if title and EXCLUDE_TITLE.search(title):
        return None, "not a speech (title)"
    if len(text) < config.MIN_TEXT_CHARS:
        return None, f"no speech text ({len(text)} chars)"
    if not date or not speaker:
        return None, f"date or speaker not found (date={date}, speaker={speaker})"
    speaker = re.sub(r"\s+", " ", speaker).strip()
    return {
        "id": make_id(date, speaker, title or ""), "date": date, "speaker": speaker, "role": role,
        "title": title or "", "url": e["url"], "source": e["source"], "text": text.strip(),
    }, ""


# ---------------------------------------------------------------- Fed target rate history

RATE_PAGES = [
    "https://www.federalreserve.gov/monetarypolicy/openmarket.htm",
    "https://www.federalreserve.gov/monetarypolicy/openmarket_archive.htm",
]


def fetch_fed_funds_history() -> list[tuple[str, str]]:
    """[(effective_date, level)] for every FOMC target change, e.g. ('2020-03-16', '0-0.25')."""
    changes = {}
    for url in RATE_PAGES:
        text = re.sub(r"<[^>]+>", " ", http_get.get(url).text)
        text = re.sub(r"\s+", " ", html.unescape(text))
        for block in re.finditer(r"(\d{4}) Date Increase Decrease Level \(%\)(.*?)(?=\d{4} Date Increase|Back to|$)", text):
            year = int(block.group(1))
            for m in re.finditer(rf"({'|'.join(MONTHS)}) (\d{{1,2}})\s*\*?\s+(\S+)\s+(\S+)\s+(\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?)", block.group(2)):
                date = dt.date(year, MONTH_NUM[m.group(1)], int(m.group(2))).isoformat()
                changes[date] = m.group(5)
    return sorted(changes.items())
