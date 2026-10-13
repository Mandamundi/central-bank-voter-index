"""Read/write the speech store (data/speeches/{year}.csv) and the score file."""
from __future__ import annotations

import csv
import json
import os
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
        path = config.SPEECH_DIR / f"{year}.csv"
        temporary = path.with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        os.replace(temporary, path)
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


def load_processing() -> dict[str, dict]:
    if not config.PROCESSING.exists():
        return {}
    return json.loads(config.PROCESSING.read_text(encoding="utf-8"))


def save_processing(outcomes: dict[str, dict]) -> None:
    config.DATA.mkdir(parents=True, exist_ok=True)
    temporary = config.PROCESSING.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(outcomes, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, config.PROCESSING)


def remove_speeches(records: list[dict]) -> int:
    """Remove these speeches from the store, and any scores they have."""
    remove = {r["id"] for r in records}
    if not remove:
        return 0
    all_rows = load_speeches()
    for year in {r["date"][:4] for r in records}:
        path = config.SPEECH_DIR / f"{year}.csv"
        temporary = path.with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(r for r in all_rows if r["date"][:4] == year and r["id"] not in remove)
        os.replace(temporary, path)
    if config.SCORES.exists():
        lines = config.SCORES.read_text(encoding="utf-8").splitlines(keepends=True)
        keep = [line for line in lines if not line.strip() or json.loads(line)["id"] not in remove]
        if len(keep) < len(lines):
            temporary = config.SCORES.with_suffix(".jsonl.tmp")
            temporary.write_text("".join(keep), encoding="utf-8")
            os.replace(temporary, config.SCORES)
    return len(remove)
