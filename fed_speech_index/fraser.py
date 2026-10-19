"""FOMC voters' speech archive from FRASER, the St. Louis Fed's digital library.

FRASER keeps one collection per Fed official ("Statements and Speeches of Esther L. George"),
built from the Board's and Reserve Banks' own archives and extended once a year with the previous
year's speeches. It is the archive of record here; the Board and Reserve Bank sites (update.py)
supply everything newer, and the people FRASER has no collection for.

Everything is read from FRASER's public pages; no API key is needed:
  series listings (?listRecords=true)    the collections in each "Statements and Speeches of ..." series,
                                         with the holder and date range. Starting from the FOMC
                                         participants series and following every related speech
                                         series reaches the Board, each Reserve Bank's presidents, etc.
  collection pages (?browse=1990s, ...)  each collection's items
  metadata.php?type=item&id=<id>&json=1  an item's date, title, Reserve Bank and text-file URL
  the item's .txt file (its PDF when no text file exists)

Which collections: those whose holder's name matches a name in data/voters.csv (surname, plus
first initial when the minutes give one) and whose date range comes within a year of a roster
listing that name. The date check tells apart people who share a surname: old minutes give
surnames only, and both Richard W. Fisher (Dallas president 2005-15) and Peter R. Fisher (New York
Fed staff 1996-99) have collections.

A Reserve Bank copy of a speech FRASER also has (same person, same date) is removed from the
store, so each speech is scored once, from FRASER's text.

    python fraser.py              # harvest new FRASER items (run monthly; FRASER adds a year each January)
"""
from __future__ import annotations

import datetime as dt
import html
import re
import sys

import config
import http_get
import runlog
import sources
import speech_metadata
import store
import update
import voters

FRASER = "https://fraser.stlouisfed.org"
ROOT_SERIES = "3761"            # Statements and Speeches of Federal Open Market Committee Participants
PREFIX = "Statements and Speeches of "
SAVE_EVERY = 25


def person_name(author: str) -> str:
    """FRASER's 'Fisher, Richard W., 1949-' / 'Stewart, Jamie B., Jr.' -> 'Richard W. Fisher' / 'Jamie B. Stewart Jr.'"""
    author = re.sub(r"\s*\([^)]*\)", "", html.unescape(author))        # "John C. (John Carroll)" -> "John C."
    parts = [p.strip() for p in author.split(",")]
    parts = [p for p in parts if p and not re.fullmatch(r"[\d\s\-–?]+", p)]
    if len(parts) < 2:
        return " ".join(parts)
    suffix = [p for p in parts[2:] if re.fullmatch(r"(?:Jr|Sr|II|III|IV)\.?", p)]
    return " ".join([parts[1], parts[0], *suffix])


def _page(path: str) -> tuple[str, str]:
    """(final URL without query, page text) for a FRASER path such as '/title/3761'."""
    response = http_get.get(FRASER + path)
    return response.url.split("?")[0].split("#")[0], response.text


def collections() -> list[dict]:
    """Every personal speech collection reachable from the FOMC participants series:
    [{'id', 'name', 'start', 'end'}]."""
    found, seen, todo = {}, set(), [ROOT_SERIES]
    while todo:
        series = todo.pop()
        if series in seen:
            continue
        seen.add(series)
        base, _ = _page(f"/title/{series}")
        for record in http_get.get(f"{base}?listRecords=true").json()["records"]:
            meta = record["metadata"]
            if not record["title"].startswith(PREFIX):
                continue
            todo += [r["recordInfo"]["recordIdentifier"][0] for r in meta.get("relatedItem", [])
                     if r.get("@type") == "series" and r["titleInfo"][0]["title"].startswith("Statements and Speeches")]
            creators = [n["namePart"][0] for n in meta.get("name", []) if n.get("role") == "creator"]
            dates = {d.get("@point"): d.get("$") for d in (meta.get("originInfo") or {}).get("dateIssued", [])
                     if isinstance(d, dict)}
            if len(creators) == 1 and dates.get("start") and dates.get("end"):
                found[record["id"]] = {"id": record["id"], "name": person_name(creators[0]),
                                       "start": dates["start"], "end": dates["end"]}
    return list(found.values())


def voter_collections(rows: list[dict]) -> list[dict]:
    """The collections of FOMC voters (see module doc)."""
    on_roster: dict[tuple[str, str], list[dt.date]] = {}
    for r in rows:
        for n in r["names"]:
            on_roster.setdefault(voters.name_key(n), []).append(dt.date.fromisoformat(r["date"]))
    year = dt.timedelta(days=366)
    matched = []
    for c in collections():
        start, end = dt.date.fromisoformat(c["start"]) - year, dt.date.fromisoformat(c["end"]) + year
        key = voters.name_key(c["name"])
        if any(voters.same_person(key, k) and any(start <= d <= end for d in ds) for k, ds in on_roster.items()):
            matched.append(c)
    return sorted(matched, key=lambda c: c["name"])


def collection_items(collection: dict) -> list[str]:
    """Item page URLs in one collection, read from its decade listings."""
    base, first = _page(f"/title/{collection['id']}")
    urls = set()
    for decade in sorted(set(re.findall(r"browse=(\d{4}s)", first))):
        _, listing = _page(f"{base.removeprefix(FRASER)}?browse={decade}")
        urls.update(re.findall(rf'href="(/title/[a-z0-9-]+-{collection["id"]}/[a-z0-9-]+-\d+)"', listing))
    return sorted(FRASER + u for u in urls)


def read_item(url: str, speaker: str) -> tuple[dict | None, str]:
    """The stored speech for one FRASER item page, or (None, reason it is not stored)."""
    record = http_get.get(f"{FRASER}/metadata.php?type=item&id={url.rsplit('-', 1)[1]}&json=1").json()
    date = sources.parse_date((record.get("originInfo") or {}).get("sortDate", ""))
    names = (record.get("titleInfo") or [{}])[0]
    title = " ".join((names.get("title") or "").split())
    subtitle = " ".join((names.get("subTitle") or "").split())
    if not date:
        return None, "metadata missing: date"
    if sources.EXCLUDE_TITLE.search(f"{title} {subtitle}"):
        return None, "not a speech (title)"
    location = record.get("location", {})
    text_url = (location.get("textUrl") or [None])[0]
    pdf_url = (location.get("pdfUrl") or [None])[0]
    if text_url:
        text = http_get.get(text_url).text
    elif pdf_url and pdf_url.lower().endswith(".pdf"):
        text = sources.pdf_text(pdf_url)
    else:
        return None, "no text or PDF file"
    text = "\n".join(line.rstrip() for line in text.replace("\r", "").splitlines()).strip()
    if len(text) < config.MIN_TEXT_CHARS and not speech_metadata.short_prepared_text(text):
        return None, f"no prepared speech text ({len(text)} chars)"
    banks = [n["namePart"][0] for parent in record.get("relatedItem", []) if parent.get("@type") == "parent"
             for n in parent.get("name", []) if n.get("role") == "contributor" and n.get("namePart")]
    role = re.sub(r"\s*\(U\.S\.\)$", "", banks[0]) if banks else "Federal Reserve System"
    title = title or subtitle
    return {"id": sources.make_id(date, speaker, title), "date": date, "speaker": speaker, "role": role,
            "title": title, "url": url, "source": "fraser", "text": text}, ""


def _save(records: list[dict], outcomes: dict) -> int:
    """Add FRASER speeches. A bank copy can carry the same id (same date, surname and title); FRASER's replaces it."""
    ids = {r["id"] for r in records}
    copies = [s for s in store.load_speeches() if s["id"] in ids and s["source"] != "fraser"]
    store.remove_speeches(copies)
    for s in copies:
        if s["url"] in outcomes:
            outcomes[s["url"]].update(status="excluded", reason=update.FRASER_COPY)
    return store.add_speeches(records)


def remove_bank_copies() -> int:
    """Drop Reserve Bank copies of speeches FRASER also holds (same person, same date)."""
    speeches = store.load_speeches()
    fraser_days = {(s["date"], voters.name_key(s["speaker"])) for s in speeches if s["source"] == "fraser"}
    copies = [s for s in speeches if s["source"] != "fraser"
              and any(d == s["date"] and voters.same_person(voters.name_key(s["speaker"]), k) for d, k in fraser_days)]
    store.remove_speeches(copies)
    outcomes = store.load_processing()
    for s in copies:
        if s["url"] in outcomes:
            outcomes[s["url"]].update(status="excluded", reason=update.FRASER_COPY)
    store.save_processing(outcomes)
    return len(copies)


def harvest() -> int:
    http_get.begin_source("fraser")
    print("FRASER: finding voters' collections...", flush=True)
    matched = voter_collections(voters.load())
    outcomes = store.load_processing()
    stored = {s["url"] for s in store.load_speeches()}
    excluded = {u for u, o in outcomes.items() if o.get("source") == "fraser" and o["status"] == "excluded"}
    todo = []
    for c in matched:
        urls = collection_items(c)
        new = [u for u in urls if u not in stored and u not in excluded]
        print(f"  {c['name']}: {len(urls)} items ({c['start'][:4]}-{c['end'][:4]}), {len(new)} new", flush=True)
        todo += [(c["name"], u) for u in new]
    print(f"FRASER: {len(matched)} voters' collections; {len(todo)} items to read.", flush=True)
    new, added = [], 0
    for n, (name, url) in enumerate(todo, 1):
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            record, reason = read_item(url, name)
        except http_get.SourcePaused:
            print("FRASER is not responding; stopping. Rerun to continue where this left off.", flush=True)
            break
        except Exception as error:
            outcomes[url] = {"source": "fraser", "status": "request_failure", "reason": str(error), "last_attempt": now}
            runlog.logger.info("fraser %s failed: %s", url, error)
        else:
            if record:
                new.append(record)
            outcomes[url] = {"source": "fraser", "status": "stored" if record else "excluded",
                             "reason": reason or "speech text read", "last_attempt": now}
        if n % SAVE_EVERY == 0:
            added += _save(new, outcomes)
            new.clear()
            store.save_processing(outcomes)
            print(f"  {n}/{len(todo)} read; {added} stored", flush=True)
    added += _save(new, outcomes)
    store.save_processing(outcomes)
    removed = remove_bank_copies()
    print(f"FRASER: {added} speeches added; {removed} Reserve Bank copies of FRASER speeches removed.", flush=True)
    return added


def main() -> int:
    with runlog.session():
        try:
            harvest()
        except KeyboardInterrupt:
            print("Stopped by user. Speeches read so far are saved; rerun to continue.", flush=True)
            return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
