"""Read/write the speech store (data/speeches/{year}.csv) and the score file."""
from __future__ import annotations

import csv
import json
import sys

import config

FIELDS = ["id", "date", "speaker", "role", "title", "url", "source", "text"]

csv.field_size_limit(sys.maxsize)


def load_speeches() -> list[dict]:
    rows = []
    for path in sorted(config.SPEECH_DIR.glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as f:
            rows.extend(csv.DictReader(f))
    return rows


def add_speeches(new: list[dict]) -> int:
    """Merge new speeches into the store, skipping ids already present. Returns count added."""
    existing = load_speeches()
    ids = {r["id"] for r in existing}
    added = [r for r in new if r["id"] not in ids and not ids.add(r["id"])]
    if not added:
        return 0
    by_year: dict[str, list[dict]] = {}
    for r in existing + added:
        by_year.setdefault(r["date"][:4], []).append(r)
    config.SPEECH_DIR.mkdir(parents=True, exist_ok=True)
    for year in {r["date"][:4] for r in added}:
        rows = sorted(by_year[year], key=lambda r: (r["date"], r["id"]))
        with (config.SPEECH_DIR / f"{year}.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow(r)
    return len(added)


def load_scores(scorer_version: str = config.SCORER_VERSION) -> dict[str, dict]:
    scores = {}
    if config.SCORES.exists():
        with config.SCORES.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    s = json.loads(line)
                    if s["scorer_version"] == scorer_version:
                        scores[s["id"]] = s
    return scores


def append_score(score: dict) -> None:
    with config.SCORES.open("a", encoding="utf-8") as f:
        f.write(json.dumps(score, ensure_ascii=False) + "\n")


def load_seen_urls() -> set[str]:
    if not config.SEEN_URLS.exists():
        return set()
    return set(config.SEEN_URLS.read_text(encoding="utf-8").split())


def save_seen_urls(urls: set[str]) -> None:
    config.SEEN_URLS.write_text("\n".join(sorted(urls)) + "\n", encoding="utf-8")
