"""Who voted on the FOMC on each date, from the Fed's own pages.

History: the vote lines in each meeting's minutes ("Voting for this action: ...", or
"Votes for this action: Messrs. Greenspan, ..." in older minutes). Every name in a for/against
line is a voter at that meeting.
Daily: the "Committee Members" list on federalreserve.gov/monetarypolicy/fomc.htm.

data/voters.csv has one row per roster change: date, source, names (";"-separated).
The voters on any date are those of the latest row on or before it.
"""
from __future__ import annotations

import bisect
import csv
import datetime as dt
import html
import re

import config
import http_get

FED = "https://www.federalreserve.gov"
FOMC_PAGE = f"{FED}/monetarypolicy/fomc.htm"
CALENDAR_PAGE = f"{FED}/monetarypolicy/fomccalendars.htm"          # roughly the last five years
HISTORICAL_PAGE = f"{FED}/monetarypolicy/fomchistorical{{year}}.htm"  # older years

_TITLES = r"(?:Messrs|Mses|Mmes|Mr|Ms|Mrs|Dr)\.?"
_SUFFIX = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}


def _text(page: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


# ---------------------------------------------------------------- names

def name_key(name: str) -> tuple[str, str]:
    """(surname, first initial or '') used to match a speaker to a voter."""
    name = re.sub(rf"\b{_TITLES}\s*", " ", name)
    name = re.sub(r"\b(?:Vice[ -])?Chair(?:man|woman)?\b|\bGovernor\b|\bPresident\b", " ", name)
    parts = [p for p in re.split(r"[\s_]+", name.replace(",", " ").strip()) if p]
    while len(parts) > 1 and parts[-1].lower() in _SUFFIX:
        parts.pop()
    if not parts:
        return "", ""
    surname = re.sub(r"[^a-z\-']", "", parts[-1].lower())
    first = parts[0][0].lower() if len(parts) > 1 else ""
    return surname, first


def same_person(a: tuple[str, str], b: tuple[str, str]) -> bool:
    """Surnames match, and first initials match when both are known (old minutes give surnames only)."""
    return a[0] == b[0] and (not a[1] or not b[1] or a[1] == b[1])


# ---------------------------------------------------------------- minutes

# A person's name as the minutes print it: "Hoenig", "Beth M. Hammack", "Sarah Bloom Raskin".
_NAME = r"(?:[A-Z][a-z]+\s+(?:[A-Z]\.\s+)?(?:[A-Z][a-z]+\s+)?)?(?:Mc|Mac)?[A-Z][A-Za-z'\-]+(?:,?\s+Jr\.)?"
# A dissent reason ("who preferred ...") runs until the next dissenter's name, which follows
# "and" and is itself followed by a comma, "who", another "and" ("...; and Austan D. Goolsbee and Jeffrey R.
# Schmid, who ...", December 2025), or the end of the line.
_REASON = re.compile(rf",?\s*\bwho\b.*?(?=(?:,\s*|\s+)and\s+{_NAME}(?:,|\s+who\b|\s+and\s|\s*$)|$)")


def parse_vote_names(segment: str) -> list[str]:
    """'Greenspan, Geithner, Bies, and Stern' -> ['Greenspan', 'Geithner', 'Bies', 'Stern'] (titles already removed)."""
    segment = re.sub(r"\([^)]*\)?", " ", segment)       # notes such as "(Stewart voted as an alternate member)"
    segment = _REASON.sub("", segment)
    segment = re.sub(r"[,;]?\s+and\s+", ", ", segment)
    names = []
    for part in re.split(r"[,;]", segment):
        # A name ends at the first lowercase word or role title: an explanation can follow a
        # dissenter's name without a period ("Loretta J. Mester President Mester was fully supportive ...").
        part = re.split(r"\s+(?=[a-z])|\s+(?:President|Governor|Chair(?:man|woman)?|Vice)\b", part.strip())[0]
        part = part.strip(" .;:")
        # drop role labels that follow a name ("Jerome H. Powell, Chair") and anything not shaped like a name
        if (not re.fullmatch(r"[A-Z][\w.'\-]*(?:\s+[A-Z][\w.'\-]*){0,4}", part)
                or re.fullmatch(r"Vice(?: Chair(?:man|woman)?)?|Chair(?:man|woman)?|None", part)):
            continue
        names.append(part)
    return names


_ACTION = r"th(?:is|e)(?: FOMC)?(?: monetary policy)? action\s*:?\s*"
# A 'for' list runs to the 'against' line that follows it. An 'against' list ends at the first period
# that closes a word of two or more lowercase letters (a surname, "None", the end of a dissent
# reason), so middle initials ("John C. Williams") do not end it; or after "None" without a period;
# or at "Absent and not voting".
_FOR_LINE = re.compile(rf"(?:Votes?|Voting) for {_ACTION}(.{{0,900}}?)(?=(?:Votes?|Voting) against)")
# Unanimous votes are sometimes printed without an 'against' line; the 'for' list then ends at its sentence.
_FOR_ONLY_LINE = re.compile(rf"(?:Votes?|Voting) for {_ACTION}(.{{0,900}}?(?<=[a-z]{{2}}))\s?\.(?:\s|$)")
_AGAINST_LINE = re.compile(
    rf"(?:Votes?|Voting) against {_ACTION}(.*?)(?:(?<=[a-z]{{2}})\s?\.(?:\s|$)|(?<=None)\b|\s+Absent and not voting)"
)


def voters_in_minutes(page: str) -> list[str]:
    """Names in the meeting's policy vote: the first 'for' line and the 'against' line after it.
    (Minutes that also cover an earlier conference call carry later vote lines, sometimes with the
    previous year's roster.)"""
    text = re.sub(rf"\b{_TITLES}\s*", "", _text(page))
    m_for = _FOR_LINE.search(text) or _FOR_ONLY_LINE.search(text)
    if not m_for:
        return []
    m_against = _AGAINST_LINE.search(text, m_for.end())
    # a stray period inside the 'for' list ("Greenspan, McDonough. Boehne, ...") separates names
    segment = re.sub(r"(?<=[a-z]{2})\s?\.\s*", ", ", m_for.group(1))
    names: list[str] = []
    for part in (segment, m_against.group(1) if m_against else ""):
        for n in parse_vote_names(part):
            if n not in names and len(n) < 40:
                names.append(n)
    return names


def minutes_urls() -> list[tuple[str, str]]:
    """[(meeting date, minutes URL)] for every meeting from 1996 on (HTML minutes only)."""
    pages = [CALENDAR_PAGE] + [HISTORICAL_PAGE.format(year=y) for y in range(1996, dt.date.today().year)]
    found = {}
    for url in pages:
        try:
            page = http_get.get(url).text
        except Exception:
            continue          # recent years are not on a historical page yet
        for path in re.findall(r'href="(/(?:monetarypolicy/fomcminutes|fomc/minutes/)(\d{8})\.htm)"', page):
            href, ymd = path
            found[f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"] = FED + href
        # A few meetings link their HTML minutes under another name (June 2008: /monetarypolicy/fomc20080625.htm);
        # used only where no minutes link exists for that date.
        for href, ymd in re.findall(r'href="(/monetarypolicy/fomc(\d{8})\.htm)"', page):
            found.setdefault(f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}", FED + href)
    return sorted(found.items())


# ---------------------------------------------------------------- current roster

def current_members() -> list[str]:
    """Names in the list under '<year> Committee Members' on fomc.htm (one <li> per member)."""
    page = http_get.get(FOMC_PAGE).text
    m = re.search(r"\d{4} Committee Members</h\d>\s*<ul>(.*?)</ul>", page, re.S)
    if not m:
        raise ValueError("Committee Members list not found on fomc.htm")
    return [_text(n).strip() for n in re.findall(r"<li>\s*<a [^>]*>(.*?)</a>", m.group(1), re.S)]


# ---------------------------------------------------------------- store

def load() -> list[dict]:
    if not config.VOTERS.exists():
        return []
    with config.VOTERS.open(encoding="utf-8", newline="") as f:
        return [{"date": r["date"], "source": r["source"], "names": r["names"].split(";")} for r in csv.DictReader(f)]


def save(rows: list[dict]) -> None:
    rows = sorted(rows, key=lambda r: r["date"])
    with config.VOTERS.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "source", "names"])
        for r in rows:
            w.writerow([r["date"], r["source"], ";".join(r["names"])])


def _one_edit(a: str, b: str) -> bool:
    """True if a and b differ by exactly one inserted, deleted or substituted letter."""
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long = sorted((a, b), key=len)
    return len(long) - len(short) == 1 and any(long[:i] + long[i + 1:] == short for i in range(len(long)))


def clean(rows: list[dict]) -> list[dict]:
    """Repair the minutes' own typesetting slips, using only the neighbouring rosters:
    - a missing comma in a surname-only roster ("McDonough Ferguson") is split into its two surnames;
    - a surname one letter away from a more frequent surname that never sits on the same roster
      and appears within a year of it ("Meyers" for four meetings in 1999, "Meyer" for years
      around them) is that person."""
    rows = sorted(rows, key=lambda r: r["date"])
    for r in rows:
        if sum(" " not in n for n in r["names"]) > len(r["names"]) / 2:       # surname-only roster
            names = []
            for n in r["names"]:
                parts = n.split()
                names += parts if len(parts) == 2 and all(re.fullmatch(r"(?:Mc|Mac)?[A-Z][a-z]+", p) for p in parts) else [n]
            r["names"] = names
    dates: dict[str, list[dt.date]] = {}
    for r in rows:
        for n in r["names"]:
            dates.setdefault(n, []).append(dt.date.fromisoformat(r["date"]))
    fix = {}
    for n, seen in dates.items():
        for m, other in dates.items():
            if (" " not in n and " " not in m and len(other) > len(seen) and _one_edit(n, m)
                    and not set(seen) & set(other)
                    and min(abs((a - b).days) for a in seen for b in other) <= 366):
                fix[n] = m
    for r in rows:
        r["names"] = list(dict.fromkeys(fix.get(n, n) for n in r["names"]))
    return rows


def refresh(full: bool = False) -> None:
    """Add minutes not yet stored (all of them if full) and today's roster if it changed."""
    rows = load()
    have = {r["source"] for r in rows}
    urls = minutes_urls()
    for date, url in urls if full else urls[-3:]:
        if url in have:
            continue
        try:
            names = voters_in_minutes(http_get.get(url).text)
        except Exception as e:
            print(f"  minutes {date}: not read ({e})")
            continue
        if len(names) < 8:          # every meeting has at least this many voters; fewer means a parse failure
            print(f"  minutes {date}: only {len(names)} voter names found; skipped {url}")
            continue
        rows.append({"date": date, "source": url, "names": names})
        print(f"  minutes {date}: {len(names)} voters")
    try:
        members = current_members()
        latest = sorted(rows, key=lambda r: r["date"])[-1] if rows else None
        if len(members) == 12 and (latest is None or sorted(map(name_key, members)) != sorted(map(name_key, latest["names"]))):
            rows.append({"date": dt.date.today().isoformat(), "source": FOMC_PAGE, "names": members})
            print(f"  fomc.htm roster changed: {', '.join(members)}")
        elif len(members) != 12:
            print(f"  fomc.htm: found {len(members)} members, expected 12; roster not updated")
    except Exception as e:
        print(f"  fomc.htm not read ({e})")
    save(clean(rows))


class Roster:
    """Voters on any date, as name keys."""

    def __init__(self, rows: list[dict] | None = None):
        rows = sorted(rows if rows is not None else load(), key=lambda r: r["date"])
        self.dates = [r["date"] for r in rows]
        self.keys = [[name_key(n) for n in r["names"]] for r in rows]

    def on(self, date: str) -> list[tuple[str, str]]:
        i = bisect.bisect_right(self.dates, date) - 1
        return self.keys[i] if i >= 0 else []

    def ever(self) -> list[tuple[str, str]]:
        return [k for keys in self.keys for k in keys]
