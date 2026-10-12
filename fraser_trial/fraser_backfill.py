#!/usr/bin/env python3
"""Resume-safe FRASER author/creator lookup and download for FOMC voter speeches.

Enumerate FRASER's author directory once, match author entities against the project's
historical FOMC voter names, then use /author/{id}/records?role=creator. This makes the
creator relationship a server-side filter; speech genre, exact date, and date-specific
voting membership are checked locally.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

BASE = "https://fraser.stlouisfed.org/api"
PAGE_SIZE = 100
MIN_API_INTERVAL = 2.1  # below FRASER's documented 30 requests/minute ceiling
MAX_FILE_BYTES = 120 * 1024 * 1024
FIELDS = "titleInfo!name!originInfo!genre!identifier!location!recordInfo"
AUTHOR_FIELDS = "name!recordInfo"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRIAL_ROOT = Path(__file__).resolve().parent
VOTERS_CSV = PROJECT_ROOT / "data" / "voters.csv"
SPEECH_DIR = PROJECT_ROOT / "data" / "speeches"
STATE_DB = TRIAL_ROOT / "state.sqlite"
CACHE_DIR = TRIAL_ROOT / "cache" / "api"
DOWNLOAD_DIR = TRIAL_ROOT / "downloads"
LOG_DIR = TRIAL_ROOT / "logs"
MANIFEST = TRIAL_ROOT / "manifest.csv"
USER_AGENT = "FOMC-speech-coverage-audit/0.1 (local research client)"
SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}
TITLES = re.compile(r"\b(?:Messrs|Mses|Mmes|Mr|Ms|Mrs|Dr)\.?\s*", re.I)
ROLE_WORDS = re.compile(r"\b(?:Vice[ -])?Chair(?:man|woman)?\b|\bGovernor\b|\bPresident\b", re.I)
BAD_NAME_WORDS = {"member", "alternate", "voted", "as", "the", "flow", "credit", "households"}
CREATOR_ROLES = {"creator", "cre", "author", "aut", "speaker", "spk"}
SPEECH_GENRES = {"speech", "speeches"}
TEXT_EXTENSIONS = {".pdf", ".txt", ".html", ".htm", ".doc", ".docx", ".rtf"}


class RequestLimitReached(Exception):
    """Raised when the configured live API request budget is exhausted."""


def logger() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("fraser_trial")
    log.setLevel(logging.INFO)
    if not log.handlers:
        handler = logging.FileHandler(LOG_DIR / "fraser_backfill.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        log.addHandler(stream)
    return log


def load_key() -> str:
    if os.environ.get("FRASER_API_KEY", "").strip():
        return os.environ["FRASER_API_KEY"].strip()
    env_file = TRIAL_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("FRASER_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit("No key found. Request and save one with request_api_key.py first.")


def name_key(name: str) -> tuple[str, str]:
    """Mirror the local project's surname/first-initial voter matching."""
    name = TITLES.sub(" ", (name or "").replace("’", "'").strip())
    name = ROLE_WORDS.sub(" ", name)
    parts = [p for p in re.split(r"[\s_]+", name.replace(",", " ").strip()) if p]
    while len(parts) > 1 and parts[-1].lower() in SUFFIXES:
        parts.pop()
    if not parts:
        return "", ""
    surname = re.sub(r"[^a-z\-']", "", parts[-1].lower())
    first = parts[0][0].lower() if len(parts) > 1 else ""
    return surname, first


def fraser_name_key(name: str) -> tuple[str, str]:
    """Normalize MODS 'Last, First Middle' names before using project matching rules."""
    name = (name or "").strip()
    if "," in name:
        last, rest = name.split(",", 1)
        if last.strip() and rest.strip():
            name = f"{rest.strip()} {last.strip()}"
    return name_key(name)


def same_person(a: tuple[str, str], b: tuple[str, str]) -> bool:
    return a[0] == b[0] and (not a[1] or not b[1] or a[1] == b[1])


def name_like(value: str) -> bool:
    value = (value or "").strip()
    if not value or value.startswith("("):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z'’.-]*", value)
    if not words or any(w[0].islower() for w in words):
        return False
    if any(w.lower().strip(".") in BAD_NAME_WORDS for w in words):
        return False
    return bool(name_key(value)[0])


def load_rosters(start: str, end: str) -> tuple[list[str], list[list[str]]]:
    if not VOTERS_CSV.exists():
        raise SystemExit(f"Missing project voter history: {VOTERS_CSV}")
    with VOTERS_CSV.open(encoding="utf-8-sig", newline="") as fh:
        rows = sorted(csv.DictReader(fh), key=lambda r: r["date"])
    dates = [r["date"] for r in rows]
    names = [r["names"].split(";") for r in rows]
    if not dates or not any(d <= end for d in dates):
        raise SystemExit("No voter roster history overlaps the requested dates.")
    return dates, names


def roster_on(date: str, dates: list[str], names: list[list[str]]) -> list[str]:
    idx = bisect.bisect_right(dates, date) - 1
    return [n for n in names[idx] if name_like(n)] if idx >= 0 else []


def build_author_queries(start: str, end: str, dates: list[str], rosters: list[list[str]]) -> list[str]:
    aliases: Counter[str] = Counter()
    # Full voter names from roster snapshots are useful query anchors.
    for date, names in zip(dates, rosters):
        if start <= date <= end:
            for name in names:
                if name_like(name):
                    aliases[" ".join(name.split())] += 2
    # Existing corpus supplies full speaker spellings for older surname-only roster lines.
    for path in sorted(SPEECH_DIR.glob("*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for speech in csv.DictReader(fh):
                date = speech.get("date", "")
                speaker = " ".join((speech.get("speaker") or "").split())
                if not (start <= date <= end and name_like(speaker)):
                    continue
                if any(same_person(name_key(speaker), name_key(n)) for n in roster_on(date, dates, rosters)):
                    aliases[speaker] += 1
    # Retain best full-name query per person identity; keep distinct initials separate.
    by_person: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
    for alias, frequency in aliases.items():
        by_person[name_key(alias)].append((alias, frequency))
    queries: list[str] = []
    for identity, options in by_person.items():
        if not identity[0]:
            continue
        best = sorted(options, key=lambda x: (len(x[0].split()) > 1, x[1], len(x[0])), reverse=True)[0][0]
        queries.append(best)
    # Add surname-only roster entries only when no matching full-name query exists.
    full_surnames = {name_key(q)[0] for q in queries if name_key(q)[1]}
    for date, names in zip(dates, rosters):
        if start <= date <= end:
            for name in names:
                key = name_key(name)
                if name_like(name) and not key[1] and key[0] not in full_surnames:
                    queries.append(name.strip())
    dedup: dict[str, str] = {}
    for query in queries:
        dedup.setdefault(re.sub(r"\W+", " ", query).strip().casefold(), query)
    return sorted(dedup.values(), key=lambda n: (name_key(n)[0], name_key(n)[1], n.casefold()))


def init_db() -> sqlite3.Connection:
    TRIAL_ROOT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(STATE_DB)
    db.execute("""CREATE TABLE IF NOT EXISTS records (
        record_id TEXT PRIMARY KEY, record_type TEXT, title TEXT, speech_date TEXT,
        creators TEXT, genre TEXT, query_name TEXT, fraser_url TEXT,
        download_paths TEXT, sha256 TEXT, status TEXT, reason TEXT,
        raw_json TEXT, updated_at TEXT
    )""")
    db.commit()
    return db


class FraserClient:
    def __init__(self, api_key: str, refresh: bool, max_requests: int | None, log: logging.Logger):
        self.api_key = api_key
        self.refresh = refresh
        self.max_requests = max_requests
        self.log = log
        self.calls = 0
        self.last_api_call = 0.0
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def cache_path(url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return CACHE_DIR / f"{digest}.json"

    def get_json(self, path: str, params: dict[str, Any]) -> Any:
        query = urlencode(params, doseq=True)
        url = f"{BASE}{path}?{query}" if query else f"{BASE}{path}"
        cache = self.cache_path(url)
        if cache.exists() and not self.refresh:
            self.log.info("cache hit: %s", cache.name)
            return json.loads(cache.read_text(encoding="utf-8"))
        request = Request(url, headers={"X-API-Key": self.api_key, "Accept": "application/json", "User-Agent": USER_AGENT})
        for attempt in range(4):
            if self.max_requests is not None and self.calls >= self.max_requests:
                raise RequestLimitReached("max API request count reached")
            elapsed = time.monotonic() - self.last_api_call
            if self.last_api_call and elapsed < MIN_API_INTERVAL:
                time.sleep(MIN_API_INTERVAL - elapsed)
            self.calls += 1
            self.last_api_call = time.monotonic()
            try:
                with urlopen(request, timeout=60) as response:
                    if response.status != 200:
                        raise RuntimeError(f"FRASER API returned HTTP {response.status}")
                    raw = response.read()
                value = json.loads(raw.decode("utf-8"))
                temp = cache.with_suffix(".json.tmp")
                temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
                os.replace(temp, cache)
                return value
            except HTTPError as exc:
                if exc.code == 401:
                    raise SystemExit("FRASER rejected the API key (HTTP 401). Check the local key; it was not logged.") from None
                if exc.code == 429:
                    retry = exc.headers.get("Retry-After") if exc.headers else None
                    delay = float(retry) if retry and retry.isdigit() else min(60, 10 * (attempt + 1))
                elif exc.code >= 500:
                    delay = min(30, 3 * (2 ** attempt))
                else:
                    raise RuntimeError(f"FRASER API returned HTTP {exc.code}; response body suppressed") from None
                if attempt == 3:
                    raise RuntimeError(f"FRASER API failed after retries with HTTP {exc.code}") from None
                self.log.warning("API HTTP %s; retrying in %ss", exc.code, delay)
                time.sleep(delay)
            except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                if attempt == 3:
                    raise RuntimeError(f"API request failed after retries: {exc.__class__.__name__}") from None
                delay = min(30, 2 * (2 ** attempt))
                self.log.warning("API transport/JSON error; retrying in %ss", delay)
                time.sleep(delay)
        raise RuntimeError("unreachable API retry state")


def recursive_values(value: Any, keys: set[str]) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in keys:
                found.append(child)
            found.extend(recursive_values(child, keys))
    elif isinstance(value, list):
        for child in value:
            found.extend(recursive_values(child, keys))
    return found


def text_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return [text] if text else []
    if isinstance(value, list):
        return [s for child in value for s in text_values(child)]
    if isinstance(value, dict):
        preferred = [v for k, v in value.items() if str(k).lower() in {"value", "content", "text", "#text", "namepart", "titlepart", "roleterm"}]
        if preferred:
            return [s for child in preferred for s in text_values(child)]
        return [s for child in value.values() for s in text_values(child)]
    return []


def record_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        if all(isinstance(x, dict) for x in payload):
            return payload
        raise ValueError("FRASER response contains a non-record list")
    if isinstance(payload, dict):
        for key in ("results", "records", "items", "data", "docs", "documents"):
            child = payload.get(key)
            if isinstance(child, list) and all(isinstance(x, dict) for x in child):
                return child
            if isinstance(child, dict):
                try:
                    return record_list(child)
                except ValueError:
                    pass
        for child in payload.values():
            if isinstance(child, dict):
                try:
                    return record_list(child)
                except ValueError:
                    pass
    if isinstance(payload, dict) and not payload:
        return []
    raise ValueError(f"Unrecognized FRASER response shape; top-level keys: {list(payload)[:20] if isinstance(payload, dict) else type(payload).__name__}")


def has_next_page(payload: Any, rows: list[dict[str, Any]], page: int) -> bool:
    if not rows:
        return False
    if isinstance(payload, dict):
        # FRASER's documented JSON envelope reports the total count and zero-based start.
        if payload.get("total") is not None:
            try:
                total = int(payload["total"])
                start = int(payload.get("start", (page - 1) * PAGE_SIZE))
                return start + len(rows) < total
            except (TypeError, ValueError):
                pass
        if payload.get("links", {}).get("next"):
            return True
        for obj in [payload, *[x for x in payload.values() if isinstance(x, dict)]]:
            meta = obj.get("meta") if isinstance(obj, dict) else None
            if isinstance(meta, dict):
                for key in ("last_page", "total_pages", "lastPage", "totalPages"):
                    if meta.get(key) is not None:
                        return page < int(meta[key])
                if meta.get("has_more") is not None:
                    return bool(meta["has_more"])
                if meta.get("hasMore") is not None:
                    return bool(meta["hasMore"])
    return len(rows) >= PAGE_SIZE


def pages(client: FraserClient, path: str, params: dict[str, Any], fields: str = FIELDS,
          one_page: bool = False):
    page = 1
    while True:
        current = dict(params, page=page, limit=PAGE_SIZE, format="json", fields=fields)
        payload = client.get_json(path, current)
        rows = record_list(payload)
        yield payload, rows
        if one_page or not has_next_page(payload, rows, page):
            break
        page += 1


def first_record(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        try:
            rows = record_list(payload)
            if rows:
                return rows[0]
        except ValueError:
            pass
        return payload
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    return {}


def record_id(record: dict[str, Any]) -> str:
    for key in ("id", "record_id", "recordId", "recordID", "uid"):
        value = record.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    for key in ("item_id", "itemId", "title_id", "titleId", "toc_id"):
        if record.get(key) is not None:
            return str(record[key])
    for key, value in record.items():
        if str(key).lower() == "recordinfo" and isinstance(value, dict):
            for child_key, child_value in value.items():
                if str(child_key).lower() == "recordidentifier":
                    identifiers = text_values(child_value)
                    if identifiers:
                        return identifiers[0]
    for key in ("metadata", "record", "mods"):
        nested = record.get(key)
        if isinstance(nested, dict):
            nested_id = record_id(nested)
            if nested_id:
                return nested_id
    for value in recursive_values(record, {"recordidentifier"}):
        for text in text_values(value):
            if text.strip():
                return text.strip()
    for value in recursive_values(record, {"identifier"}):
        for text in text_values(value):
            match = re.search(r"/(?:item|title|toc)/(\d+)(?:/|$)", text)
            if match:
                return match.group(1)
            if text.isdigit():
                return text
    return ""


def record_type(record: dict[str, Any]) -> str:
    for key in ("record_type", "recordType", "resource_type", "resourceType", "contentType"):
        value = record.get(key)
        if isinstance(value, str) and value.lower() in {"title", "item", "toc"}:
            return value.lower()
    value = record.get("type")
    if isinstance(value, str) and value.lower() in {"title", "item", "toc"}:
        return value.lower()
    for value in recursive_values(record, {"recordtype"}):
        kinds = [text.lower() for text in text_values(value)]
        for kind in kinds:
            if kind in {"title", "item", "toc"}:
                return kind
    for value in record.values():
        if isinstance(value, str):
            match = re.search(r"/api/(title|item|toc)/(\d+)", value)
            if match:
                return match.group(1)
    return "unknown"


def author_entity_names(record: dict[str, Any]) -> list[str]:
    """Return display names from an /author directory record."""
    meta = row_metadata(record)
    names = [name for name, _ in name_role_pairs(meta)]
    if not names:
        for value in recursive_values(meta, {"namepart"}):
            names.extend(text_values(value))
    if not names:
        for key in ("name", "label", "title"):
            if isinstance(meta.get(key), str):
                names.append(meta[key])
    return list(dict.fromkeys(n.strip() for n in names if n and n.strip()))


def title_text(record: dict[str, Any]) -> str:
    values = recursive_values(record, {"titleinfo"})
    parts = [s for v in values for s in text_values(v)]
    if not parts:
        parts = [str(record[k]) for k in ("title", "name") if isinstance(record.get(k), str)]
    return " ".join(dict.fromkeys(p for p in parts if p))[:1000]


def genre_values(record: dict[str, Any]) -> list[str]:
    vals = recursive_values(record, {"genre"})
    return list(dict.fromkeys(t for v in vals for t in text_values(v)))


def date_values(record: dict[str, Any]) -> list[str]:
    vals = recursive_values(record, {"dateissued", "datecreated", "datecaptured", "dateother", "date"})
    out = []
    for value in vals:
        out.extend(text_values(value))
    return list(dict.fromkeys(out))


def exact_date(record: dict[str, Any]) -> str | None:
    for value in date_values(record):
        value = value.strip()
        for match in re.finditer(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", value):
            try:
                return dt.date(int(match[1]), int(match[2]), int(match[3])).isoformat()
            except ValueError:
                continue
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y%m%d"):
            try:
                return dt.datetime.strptime(value, fmt).date().isoformat()
            except ValueError:
                pass
    return None


def name_role_pairs(record: dict[str, Any]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for node in recursive_values(record, {"name"}):
        nodes = node if isinstance(node, list) else [node]
        for part in nodes:
            if isinstance(part, dict):
                name_parts = recursive_values(part, {"namepart"})
                fragments: list[tuple[str, str]] = []
                for value in name_parts:
                    if isinstance(value, dict):
                        part_type = str(value.get("@type", value.get("type", ""))).casefold()
                        part_text = " ".join(text_values(value))
                        if part_text:
                            fragments.append((part_type, part_text))
                    else:
                        fragments.extend(("", text) for text in text_values(value))
                family = [text for part_type, text in fragments if "family" in part_type]
                given = [text for part_type, text in fragments if "given" in part_type]
                other = [text for part_type, text in fragments if "family" not in part_type and "given" not in part_type]
                if family and given:
                    names = [" ".join(given + family + other)]
                elif fragments:
                    names = [" ".join(text for _, text in fragments)]
                else:
                    names = []
                if not names and isinstance(part.get("name"), str):
                    names = [part["name"]]
                role_nodes = recursive_values(part, {"roleterm", "role"})
                roles = [s for v in role_nodes for s in text_values(v)]
                for name in names:
                    if name.strip():
                        result.append((name.strip(), " ".join(roles).strip().lower()))
            elif isinstance(part, str) and part.strip():
                result.append((part.strip(), ""))
    # Deduplicate repeated MODS projections.
    return list(dict.fromkeys(result))


def creator_names(record: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(name for name, role in name_role_pairs(record) if role and any(r in role for r in CREATOR_ROLES)))


def speech_genre(genres: list[str]) -> bool:
    normalized = {re.sub(r"[^a-z]+", " ", g.casefold()).strip() for g in genres}
    return bool(normalized & SPEECH_GENRES)


def speech_like_title(title: str) -> bool:
    return bool(re.search(r"\b(speech|speeches|remarks|address|testimony)\b", title, re.I))


def location_urls(record: dict[str, Any]) -> list[str]:
    found: list[str] = []
    locations = recursive_values(record, {"location"})
    def walk(node: Any, under_file_location: bool = False) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).lower() in {"url", "href", "downloadurl", "download_url", "pdfurl", "texturl"}:
                    found.extend(text_values(value))
                elif isinstance(value, (dict, list)):
                    walk(value, under_file_location)
        elif isinstance(node, list):
            for value in node:
                walk(value, under_file_location)
    for node in locations:
        walk(node, True)
    urls = []
    for value in found:
        absolute = urljoin("https://fraser.stlouisfed.org", value.strip())
        parsed = urlparse(absolute)
        if parsed.scheme == "https" and parsed.hostname == "fraser.stlouisfed.org" and "/files/" in parsed.path:
            if Path(parsed.path).suffix.lower() in TEXT_EXTENSIONS or not Path(parsed.path).suffix:
                urls.append(absolute)
    return list(dict.fromkeys(urls))


def api_record_url(kind: str, identifier: str) -> str:
    if kind in {"item", "title", "toc"}:
        return f"{BASE}/{kind}/{identifier}"
    return ""


def row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("metadata", "record", "mods"):
        if isinstance(row.get(key), dict):
            return row[key]
    return row


def writer_manifest(db: sqlite3.Connection) -> None:
    rows = db.execute("SELECT record_id,record_type,title,speech_date,creators,genre,query_name,fraser_url,download_paths,sha256,status,reason FROM records ORDER BY speech_date,record_id").fetchall()
    temp = MANIFEST.with_suffix(".csv.tmp")
    with temp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["fraser_id", "record_type", "title", "speech_date", "creator_names", "genre", "search_name", "fraser_url", "download_paths", "sha256", "status", "reason"])
        writer.writerows(rows)
    os.replace(temp, MANIFEST)


def upsert_record(db: sqlite3.Connection, record: dict[str, Any], kind: str, identifier: str,
                  query: str, status: str, reason: str, speech_date: str | None = None,
                  creators: list[str] | None = None, genres: list[str] | None = None,
                  file_urls: list[str] | None = None, paths: list[str] | None = None,
                  hashes: list[str] | None = None) -> None:
    previous = db.execute("SELECT download_paths,sha256,status FROM records WHERE record_id=?", (identifier,)).fetchone()
    old_paths, old_hashes, old_status = previous if previous else ("", "", "")
    merged_paths = list(filter(None, old_paths.split(";"))) + list(paths or [])
    merged_hashes = list(filter(None, old_hashes.split(";"))) + list(hashes or [])
    merged_paths = list(dict.fromkeys(merged_paths))
    merged_hashes = list(dict.fromkeys(merged_hashes))
    if old_status == "downloaded" and status not in {"downloaded", "already_downloaded"}:
        status = old_status
    if old_status == "downloaded":
        reason = ""
    title = title_text(record)
    creator_string = "; ".join(dict.fromkeys(creators or []))
    genre_string = "; ".join(dict.fromkeys(genres or []))
    source_url = api_record_url(kind, identifier)
    db.execute("""INSERT INTO records(record_id,record_type,title,speech_date,creators,genre,query_name,fraser_url,
        download_paths,sha256,status,reason,raw_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(record_id) DO UPDATE SET
          record_type=excluded.record_type, title=excluded.title,
          speech_date=COALESCE(excluded.speech_date,records.speech_date),
          creators=CASE WHEN excluded.creators!='' THEN excluded.creators ELSE records.creators END,
          genre=CASE WHEN excluded.genre!='' THEN excluded.genre ELSE records.genre END,
          query_name=CASE WHEN excluded.query_name!='' THEN excluded.query_name ELSE records.query_name END,
          fraser_url=CASE WHEN excluded.fraser_url!='' THEN excluded.fraser_url ELSE records.fraser_url END,
          download_paths=?, sha256=?, status=?, reason=?, raw_json=excluded.raw_json, updated_at=excluded.updated_at""",
        (identifier, kind, title, speech_date, creator_string, genre_string, query, source_url,
         ";".join(merged_paths), ";".join(merged_hashes), status, reason,
         json.dumps(record, ensure_ascii=False), dt.datetime.now(dt.timezone.utc).isoformat(),
         ";".join(merged_paths), ";".join(merged_hashes), status, reason))
    db.commit()


def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download_file(url: str, identifier: str, speech_date: str, slot: int, log: logging.Logger) -> tuple[str, str] | None:
    parsed = urlparse(url)
    if parsed.hostname != "fraser.stlouisfed.org" or "/files/" not in parsed.path:
        return None
    extension = Path(parsed.path).suffix.lower()
    if extension and extension not in TEXT_EXTENSIONS:
        return None
    year = speech_date[:4] if speech_date else "undated"
    folder = DOWNLOAD_DIR / year
    folder.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", identifier)[:80] or hashlib.sha1(url.encode()).hexdigest()[:12]
    target = folder / f"{safe_id}_{slot:02d}{extension or '.bin'}"
    if target.exists() and target.stat().st_size:
        return str(target.relative_to(TRIAL_ROOT)), digest_file(target)
    temp = target.with_suffix(target.suffix + ".part")
    headers = {"User-Agent": USER_AGENT, "Accept": "application/pdf,text/html,text/plain,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=90) as response:
            final_url = urlparse(response.geturl())
            if final_url.hostname != "fraser.stlouisfed.org":
                raise ValueError("download redirected outside fraser.stlouisfed.org")
            content_type = response.headers.get_content_type().lower()
            if content_type not in {"application/pdf", "text/html", "text/plain", "application/msword", "application/rtf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/octet-stream"}:
                raise ValueError(f"unsupported document content type {content_type}")
            total = 0
            with temp.open("wb") as out:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_FILE_BYTES:
                        raise ValueError(f"document exceeds {MAX_FILE_BYTES} bytes")
                    out.write(chunk)
            if not extension:
                extension = {"application/pdf": ".pdf", "text/html": ".html", "text/plain": ".txt", "application/msword": ".doc", "application/rtf": ".rtf"}.get(content_type, ".bin")
                target = target.with_suffix(extension)
            os.replace(temp, target)
            return str(target.relative_to(TRIAL_ROOT)), digest_file(target)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        temp.unlink(missing_ok=True)
        log.warning("download failed for FRASER item %s (%s)", identifier, exc.__class__.__name__)
        return None


class Harvest:
    def __init__(self, client: FraserClient, db: sqlite3.Connection, dates: list[str], rosters: list[list[str]],
                 start: str, end: str, download: bool, log: logging.Logger):
        self.client, self.db, self.dates, self.rosters = client, db, dates, rosters
        self.start, self.end, self.download, self.log = start, end, download, log
        self.visited_titles: set[tuple[str, str]] = set()
        self.stats = Counter()

    def enumerate_matching_authors(self, queries: list[str], probe: bool = False) -> dict[str, tuple[str, list[str]]]:
        """Match the paginated FRASER author directory to historical voter aliases."""
        matches: dict[str, tuple[str, list[str]]] = {}
        for payload, rows in pages(self.client, "/author", {}, fields=AUTHOR_FIELDS, one_page=probe):
            self.stats["author_pages"] += 1
            if probe:
                self.log.info("author directory probe: rows=%s; top-level keys=%s", len(rows), list(payload)[:15] if isinstance(payload, dict) else type(payload).__name__)
                for row in rows[:5]:
                    self.log.info("probe author: id=%s names=%s", record_id(row), author_entity_names(row)[:3])
            for row in rows:
                author_id = record_id(row)
                names = author_entity_names(row)
                if not author_id or not names:
                    continue
                matched_queries = [query for query in queries if any(
                    same_person(fraser_name_key(name), name_key(query)) for name in names
                )]
                if matched_queries:
                    matches[author_id] = (names[0], matched_queries)
            if probe:
                self.log.info("voter author matches on probe page: %s", [
                    {"id": author_id, "name": name, "voter_aliases": aliases}
                    for author_id, (name, aliases) in matches.items()
                ])
        return matches

    def process_author_records(self, author_id: str, author_name: str, query: str, probe: bool = False) -> int:
        count = 0
        for payload, rows in pages(self.client, f"/author/{author_id}/records", {"role": "creator"}, one_page=probe):
            self.stats["creator_record_pages"] += 1
            if probe:
                self.log.info("creator records probe: author=%r id=%s rows=%s", author_name, author_id, len(rows))
                for row in rows[:5]:
                    meta = row_metadata(row)
                    self.log.info("probe record: type=%s id=%s title=%r genre=%s", record_type(row), record_id(row), title_text(meta)[:140], genre_values(meta))
            for row in rows:
                self.visit_record(row, query, inherited_names=[(author_name, "creator")], inherited_genres=[], depth=0)
                count += 1
            if probe:
                return count
        return count

    def visit_record(self, row: dict[str, Any], query: str, inherited_names: list[tuple[str, str]],
                     inherited_genres: list[str], depth: int) -> None:
        if depth > 8:
            self.log.warning("nested title depth cap reached for query %r", query)
            self.stats["review"] += 1
            return
        meta = row_metadata(row)
        identifier = record_id(row) or record_id(meta)
        kind = record_type(row)
        if kind == "unknown":
            kind = record_type(meta)
        if not identifier:
            identifier = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
        names = name_role_pairs(meta)
        # The API request already constrained this author ID to role=creator. Preserve
        # that explicit association when item-level MODS names are partial or omitted.
        effective_names = list(dict.fromkeys(inherited_names + names))
        genres = genre_values(meta)
        effective_genres = genres if genres else inherited_genres
        title = title_text(meta)

        if kind == "toc":
            return

        # FRASER titles may be standalone documents or parents of items/nested titles.
        if kind == "title":
            children: list[dict[str, Any]] = []
            title_id = identifier
            visit_key = (title_id, query)
            if visit_key not in self.visited_titles:
                self.visited_titles.add(visit_key)
                try:
                    for _, page_rows in pages(self.client, f"/title/{title_id}/items", {}):
                        self.stats["child_pages"] += 1
                        children.extend(page_rows)
                except RequestLimitReached:
                    self.log.warning("request cap reached before title children for %s", title_id)
                    raise
                except (ValueError, RuntimeError) as exc:
                    self.log.warning("could not enumerate children for title %s: %s", title_id, exc)
            if children:
                self.stats["parent_titles"] += 1
                for child in children:
                    self.visit_record(child, query, effective_names, effective_genres, depth + 1)
                return
            # Empty child list means this may be a standalone title with its own file.

        if kind not in {"item", "title"}:
            self.stats["untyped"] += 1
            if kind == "unknown" and self.stats["untyped"] <= 10:
                self.log.warning("unrecognized FRASER record shape; id=%s keys=%s", identifier, list(row)[:20])
            return

        speech_date = exact_date(meta)
        genres_norm = effective_genres
        genre_ok = speech_genre(genres_norm)
        title_hint = speech_like_title(title)
        if not genre_ok and not title_hint:
            return

        creators = [(name, role) for name, role in effective_names if role and any(r in role for r in CREATOR_ROLES)]
        creator_matches: list[str] = []
        explicit_name_matches: list[tuple[str, str]] = []
        if speech_date:
            if not (self.start <= speech_date <= self.end):
                return
            active_keys = [name_key(n) for n in roster_on(speech_date, self.dates, self.rosters)]
            for name, role in effective_names:
                if same_person(fraser_name_key(name), name_key(query)) and any(same_person(fraser_name_key(name), k) for k in active_keys):
                    explicit_name_matches.append((name, role))
                    if role and any(r in role for r in CREATOR_ROLES):
                        creator_matches.append(name)
        else:
            # Without an exact day we cannot safely apply the rotation/roster filter.
            explicit_name_matches = [(name, role) for name, role in effective_names if same_person(fraser_name_key(name), name_key(query))]

        if creator_matches and genre_ok and speech_date:
            status, reason = "candidate", ""
        else:
            status = "review"
            reasons = []
            if not genre_ok:
                reasons.append("genre is not an explicit speech genre")
            if not speech_date:
                reasons.append("exact speech date unavailable")
            if not explicit_name_matches:
                reasons.append("no matching voter creator on speech date")
            elif not creator_matches:
                reasons.append("author role missing or not creator")
            reason = "; ".join(reasons) or "metadata needs manual review"

        if status == "review":
            self.stats["review"] += 1
        else:
            self.stats["eligible"] += 1

        file_urls = location_urls(meta)
        paths: list[str] = []
        hashes: list[str] = []
        old = self.db.execute("SELECT status,download_paths FROM records WHERE record_id=?", (identifier,)).fetchone()
        if old and old[0] == "downloaded" and all((TRIAL_ROOT / p).exists() for p in filter(None, old[1].split(";"))):
            status = "downloaded"
            paths = list(filter(None, old[1].split(";")))
            prior = self.db.execute("SELECT sha256 FROM records WHERE record_id=?", (identifier,)).fetchone()
            hashes = list(filter(None, (prior[0] or "").split(";"))) if prior else []
            self.stats["already_downloaded"] += 1
        elif status == "candidate" and self.download:
            if not file_urls and kind == "item":
                try:
                    detail_payload = self.client.get_json(f"/item/{identifier}", {"format": "json", "fields": FIELDS})
                    detail = first_record(detail_payload)
                    file_urls = location_urls(row_metadata(detail))
                    if detail:
                        meta = row_metadata(detail)
                        file_urls = file_urls or location_urls(meta)
                except RequestLimitReached:
                    raise
                except (ValueError, RuntimeError) as exc:
                    self.log.warning("detail metadata unavailable for item %s: %s", identifier, exc)
            if not file_urls:
                status, reason = "no_file", "confirmed speech metadata but no direct FRASER file URL"
                self.stats["no_file"] += 1
            else:
                for idx, url in enumerate(file_urls, 1):
                    saved = download_file(url, identifier, speech_date or "", idx, self.log)
                    if saved:
                        paths.append(saved[0]); hashes.append(saved[1])
                if paths:
                    status = "downloaded"
                    self.stats["downloaded"] += 1
                else:
                    status, reason = "no_file", "file URL present but no supported document downloaded"
                    self.stats["no_file"] += 1
        upsert_record(self.db, meta, kind, identifier, query, status, reason, speech_date,
                      creator_matches or [n for n, _ in explicit_name_matches], genres_norm,
                      file_urls, paths, hashes)

    def run_author(self, author_id: str, author_name: str, query: str, probe: bool = False) -> int:
        self.log.info("fetching FRASER creator records: author=%s id=%s voter_match=%s", author_name, author_id, query)
        try:
            return self.process_author_records(author_id, author_name, query, probe=probe)
        except RequestLimitReached as exc:
            self.log.warning("stopped query early: %s", exc)
            raise


def report(db: sqlite3.Connection) -> None:
    writer_manifest(db)
    totals = db.execute("SELECT status,COUNT(*) FROM records GROUP BY status ORDER BY status").fetchall()
    logging.getLogger("fraser_trial").info("manifest refreshed at %s; statuses=%s", MANIFEST.relative_to(TRIAL_ROOT), dict(totals))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2000-01-01")
    parser.add_argument("--end-date", default=dt.date.today().isoformat())
    parser.add_argument("--member", action="append", help="limit to author query substring; may be repeated")
    parser.add_argument("--probe", action="store_true", help="issue one cached-or-live author-directory page, inspect schema, and stop")
    parser.add_argument("--dry-run", action="store_true", help="list scoped author queries without API calls")
    parser.add_argument("--metadata-only", action="store_true", help="save matching metadata but do not download files")
    parser.add_argument("--refresh-cache", action="store_true", help="re-fetch cached API pages")
    parser.add_argument("--max-api-requests", type=int, help="hard cap on API calls for a controlled first run")
    args = parser.parse_args()
    try:
        start = dt.date.fromisoformat(args.start_date).isoformat()
        end = dt.date.fromisoformat(args.end_date).isoformat()
    except ValueError:
        parser.error("dates must be YYYY-MM-DD")
    if start > end:
        parser.error("--start-date must be on or before --end-date")

    log = logger()
    dates, rosters = load_rosters(start, end)
    queries = build_author_queries(start, end, dates, rosters)
    if args.member:
        queries = [q for q in queries if any(term.casefold() in q.casefold() for term in args.member)]
    if args.dry_run:
        print(f"Scoped author queries: {len(queries)} ({start} through {end})")
        for query in queries:
            print(query)
        return 0
    if not queries:
        raise SystemExit("No author queries were derived from the project voter history and speech corpus.")

    client = FraserClient(load_key(), args.refresh_cache, args.max_api_requests, log)
    db = init_db()
    harvester = Harvest(client, db, dates, rosters, start, end, not args.metadata_only, log)
    log.info("starting FRASER audit: %d voter-name aliases; date range %s..%s; page size %d", len(queries), start, end, PAGE_SIZE)
    try:
        matching_authors = harvester.enumerate_matching_authors(queries, probe=args.probe)
        if args.probe:
            log.info("probe stopped after author-directory page; no speech records downloaded")
        else:
            log.info("matched %d FRASER author entities; fetching creator-only records", len(matching_authors))
            for author_id, (author_name, aliases) in matching_authors.items():
                for query in aliases:
                    harvester.run_author(author_id, author_name, query)
    except RequestLimitReached:
        log.warning("stopped at configured maximum API request count")
    finally:
        report(db)
        db.close()
    log.info("API requests this run: %d; candidate/download counters: %s", client.calls, dict(harvester.stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
