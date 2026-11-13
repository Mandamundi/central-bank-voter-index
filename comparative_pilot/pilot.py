"""Pilot: score Fed speeches by pairwise comparison (Bradley-Terry with ties) instead of one absolute level.

Writes only inside comparative_pilot/; the Fed project's data is read, never changed. Run from the Fed folder:

    cd fed_speech_index
    .venv/bin/python ../comparative_pilot/pilot.py sample            # draw the texts and the pairs (fixed seed)
    .venv/bin/python ../comparative_pilot/pilot.py extract [--limit N]  # one call per text: verbatim policy passages
    .venv/bin/python ../comparative_pilot/pilot.py compare [--limit N]  # one call per pair
    .venv/bin/python ../comparative_pilot/pilot.py report            # fit the model, run the tests

extract and compare are resumable: results are appended to jsonl files and finished items are skipped.

The comparison asks the v10 question without the level table: which speaker wants policy moved further,
or sooner, from the current setting (toward tightening; easing counts the other way), or about the same?
Each answer is one comparison; the model turns them into one continuous score per text.

Side tests built into the pair list:
  reversed   a subset asked again with A and B swapped (order bias and repeatability)
  full_text  a subset asked again on the full speeches instead of the extracted passages
"""
from __future__ import annotations

import csv
import json
import math
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Literal

sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

import config        # noqa: E402
import openai_api    # noqa: E402
import score         # noqa: E402
import store         # noqa: E402

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample.json"
PAIRS = HERE / "pairs.json"
PASSAGES = HERE / "passages.jsonl"
VERDICTS = HERE / "verdicts.jsonl"
SEED = 11
WORKERS = 12
FORCED = [("8c34327f8f30", "3df92926cf7f")]         # Waller Oct 8 vs Nov 10, 2026
HARD = ["8c34327f8f30", "3df92926cf7f", "704c75bec360", "6716f4e89bf2", "28923f8352cf"]


# ---------------------------------------------------------------- prompts

EXTRACT_SYSTEM = """You are a senior Federal Reserve analyst. From a speech by a Federal Reserve official, copy out
VERBATIM the passages in which the speaker states their own view on where U.S. monetary policy should go over the
next several months: the move they want or expect (or a hold), its timing and size, the conditions or data it
depends on, the risks they weigh, and any hedges or qualifications attached to those views. Include sentences on
the balance sheet when they bear on the stance. In a crisis, the speaker's support for the emergency response
(emergency rate cuts, asset purchases, lending or liquidity facilities) IS their policy view, even when the
measures are already in place and nothing new is proposed: copy those passages. Leave out descriptions of the economy, history or other people's
views unless the speaker uses them directly to justify the policy view.

Copy text exactly as written (same words and order); you may cut a long passage at sentence boundaries. Return
2-8 passages, about 350 words in total at most. If the speech states no policy view, return an empty list."""


class Passages(BaseModel):
    passages: List[str] = Field(description="Verbatim passages, in speech order.")


COMPARE_SYSTEM = """You are a senior Federal Reserve analyst. You compare two speakers' views on U.S. monetary policy.

Question: which speaker wants monetary policy moved further toward TIGHTENING, or sooner, from the policy setting
current when they spoke, over the near term (the next few meetings, roughly six months)? Easing counts the other
way: a speaker who wants cuts is less hawkish than one who wants a hold, and wanting faster or larger cuts is less
hawkish still. Answer "A", "B", or "same" if their wanted change is about equally hawkish.

Judge the CHANGE each wants from the setting in force on their own date, not how high rates are: "keep policy where
it is" is the same whether rates are at zero or high. Weigh direction first, then how firmly and how soon (a move at
the next meeting is more than a move "at some point"), then size (a larger step or more moves is more), then
conditions (a move the speaker expects is more than one they would make only if a risk materialises).
- A move the committee has already made is part of the setting; a speaker who endorses it and wants nothing further
  holds.
- Score what the speaker says policy should do, not how high inflation or growth is.
- Standard wording ("data dependent", "we will act as appropriate") is not a lean. A conditional statement leans
  only when it names a specific development the speaker sees as likely or as a material risk.
- Crisis: while a speaker presents a financial or economic crisis as still unfolding and supports the emergency
  response (emergency rate cuts, asset purchases, lending or liquidity facilities), that is the most dovish
  position of all, even if the measures are already in place and the speaker asks for nothing new. Keeping or
  expanding emergency support during the crisis is more dovish than any call for ordinary easing.
- Supporting tapering, ending purchases or faster runoff is tightening; starting or expanding purchases is easing;
  reserve-management bill purchases are technical and do not change the stance.

Write one sentence saying what decided it, then the answer."""


class Verdict(BaseModel):
    reason: str = Field(description="One sentence: what decided the comparison.")
    answer: Literal["A", "B", "same"] = Field(description="Which speaker wants the more hawkish change, or same.")


# ---------------------------------------------------------------- data

def speeches_by_id():
    rows = {}
    for s in store.load_speeches():
        rows[s["id"]] = s
    return rows


def setting(date, dates, levels):
    level = score.fed_funds_on(date, dates, levels)
    unit = "target range" if "-" in level else "target rate"
    return f"federal funds {unit} {level}%"


def jsonl(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


LOCK = threading.Lock()


def append(path, row):
    with LOCK, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def call(system, user, schema_model, key, tries=5):
    body = openai_api.request_body(config.MODEL, config.REASONING_EFFORT, config.MAX_OUTPUT_TOKENS, system, user,
                                   schema_model.model_json_schema())
    for attempt in range(tries):
        try:
            return schema_model.model_validate_json(openai_api.output_text(openai_api.create(key, body)))
        except Exception as error:  # rate limits and transient errors: back off and retry
            if attempt == tries - 1:
                raise
            time.sleep(min(60, 5 * 2 ** attempt) + random.random())


def api_key():
    load_dotenv(Path.cwd() / ".env"); load_dotenv(Path.cwd().parent / ".env")
    return os.environ["OPENAI_API_KEY"]


# ---------------------------------------------------------------- sample

def cmd_sample():
    rng = random.Random(SEED)
    scores = store.load_scores()
    speeches = speeches_by_id()
    relevant = {i: s for i, s in scores.items() if s.get("level") is not None and i in speeches}
    level = {i: s["level"] for i, s in relevant.items()}
    chosen = [i for i in relevant if speeches[i]["date"] >= "2025-07-01"]
    chosen += [i for i in HARD if i in relevant]
    # Texts the v10 pilot scored differently on its two runs (the borderline ones), and a few holds it agreed on.
    pilot = json.loads((HERE.parent / "pilot_v10" / "pilot_results.json").read_text())
    fed = [r for r in pilot if r["bank"] == "fed" and r["id"] in relevant and len(r["v10_levels"]) >= 2 and None not in r["v10_levels"][:2]]
    chosen += [r["id"] for r in fed if r["v10_levels"][0] != r["v10_levels"][1]]
    holds = [r["id"] for r in fed if r["v10_levels"][:2] == [5, 5]]
    chosen += rng.sample(holds, min(6, len(holds)))
    # Older texts at the extremes, so the whole scale is covered.
    older = [i for i in relevant if speeches[i]["date"] < "2025-07-01" and i not in chosen]
    for lv, n in [(0, 4), (1, 2), (2, 3), (8, 4), (9, 4)]:
        pool = [i for i in older if level[i] == lv]
        chosen += rng.sample(pool, min(n, len(pool)))
    chosen = list(dict.fromkeys(chosen))
    sample = [{"id": i, "date": speeches[i]["date"], "speaker": speeches[i]["speaker"], "title": speeches[i]["title"],
               "v10_level": level[i]} for i in chosen]

    # Pairs: each text starts 2 with random partners and 2 with partners at the same or a neighbouring v10 level
    # (the informative ones); with the pairs others start, that is about 8 comparisons per text.
    pairs = set(tuple(sorted(p)) for p in FORCED)
    for i in chosen:
        others = [j for j in chosen if j != i]
        near = [j for j in others if abs(level[j] - level[i]) <= 1]
        for j in rng.sample(others, 2) + rng.sample(near, min(2, len(near))):
            pairs.add(tuple(sorted((i, j))))
    pairs = sorted(pairs)
    rng.shuffle(pairs)
    items = []
    for a, b in pairs:
        if rng.random() < 0.5:
            a, b = b, a
        items.append({"key": f"p|{a}|{b}", "a": a, "b": b, "mode": "passages", "test": "main"})
    for a, b in FORCED:
        items.append({"key": f"p|{b}|{a}", "a": b, "b": a, "mode": "passages", "test": "reversed"})
    for it in rng.sample([x for x in items if x["test"] == "main"], 120):
        items.append({"key": f"p|{it['b']}|{it['a']}", "a": it["b"], "b": it["a"], "mode": "passages", "test": "reversed"})
    for it in rng.sample([x for x in items if x["test"] == "main"], 80):
        items.append({"key": f"f|{it['a']}|{it['b']}", "a": it["a"], "b": it["b"], "mode": "full", "test": "full_text"})
    items = list({x["key"]: x for x in items}.values())
    SAMPLE.write_text(json.dumps(sample, indent=1), encoding="utf-8")
    PAIRS.write_text(json.dumps(items, indent=1), encoding="utf-8")
    from collections import Counter
    print(f"{len(sample)} texts; {len(items)} comparisons {dict(Counter(x['test'] for x in items))}")
    print("v10 levels in sample:", dict(sorted(Counter(s['v10_level'] for s in sample).items())))


# ---------------------------------------------------------------- extract

def _letters(text):
    return re.sub(r"[^a-z]", "", text.lower())


def _shingles(text, k=5):
    words = re.findall(r"[a-z]+", text.lower())
    return {" ".join(words[i:i + k]) for i in range(max(len(words) - k + 1, 1))}


def verbatim(passages, text):
    """Passages that really are in the speech. PDF text has footnote numbers, page breaks and broken
    ligatures inside sentences, so letters only are compared; failing that, 80% of the passage's 5-word
    runs must occur in the speech."""
    body, runs = _letters(text), _shingles(text)
    kept = []
    for p in passages:
        p = p.strip('“”"')
        if _letters(p) and (_letters(p) in body or len(_shingles(p) & runs) >= 0.8 * len(_shingles(p))):
            kept.append(p)
    return kept


def cmd_extract(limit=None):
    key = api_key()
    speeches = speeches_by_id()
    done = {r["id"] for r in jsonl(PASSAGES)}
    todo = [s for s in json.loads(SAMPLE.read_text()) if s["id"] not in done][:limit]
    print(f"extracting {len(todo)} texts ({len(done)} done)")

    def work(s):
        sp = speeches[s["id"]]
        user = f"Speaker: {sp['speaker']}\nDate: {sp['date']}\nTitle: {sp['title']}\n\nSPEECH TEXT:\n{sp['text'][:150_000]}"
        got = call(EXTRACT_SYSTEM, user, Passages, key).passages
        kept = verbatim(got, sp["text"])
        append(PASSAGES, {"id": s["id"], "passages": kept, "returned": len(got), "verbatim": len(kept)})
        return len(kept), len(got)

    with ThreadPoolExecutor(WORKERS) as ex:
        for n, f in enumerate(as_completed([ex.submit(work, s) for s in todo]), 1):
            f.result()
            if n % 20 == 0:
                print(f"  {n}/{len(todo)}", flush=True)
    print(openai_api.usage_summary())


# ---------------------------------------------------------------- compare

def cmd_compare(limit=None):
    key = api_key()
    speeches = speeches_by_id()
    passages = {r["id"]: r["passages"] for r in jsonl(PASSAGES)}
    dates, levels = score.load_fed_funds()
    done = {r["key"] for r in jsonl(VERDICTS)}
    todo = [p for p in json.loads(PAIRS.read_text()) if p["key"] not in done][:limit]
    missing = {i for p in todo if p["mode"] == "passages" for i in (p["a"], p["b"]) if i not in passages}
    if missing:
        sys.exit(f"{len(missing)} texts have not been through extract yet; run it first")
    print(f"comparing {len(todo)} pairs ({len(done)} done)")

    def describe(label, i, mode):
        sp = speeches[i]
        head = f"SPEAKER {label}: {sp['speaker']}, {sp['date']}. Policy setting then: {setting(sp['date'], dates, levels)}."
        if mode == "full" or not passages[i]:
            # Full text on request, or when extraction found no forward-looking passage (crisis texts that
            # describe emergency measures already taken).
            return f"{head}\nFULL SPEECH TEXT:\n{sp['text'][:60_000]}"
        return head + "\nTheir words on policy:\n" + "\n".join(f"- {p}" for p in passages[i])

    def work(p):
        user = describe("A", p["a"], p["mode"]) + "\n\n" + describe("B", p["b"], p["mode"])
        v = call(COMPARE_SYSTEM, user, Verdict, key)
        append(VERDICTS, {**p, "answer": v.answer, "reason": v.reason})

    with ThreadPoolExecutor(WORKERS) as ex:
        for n, f in enumerate(as_completed([ex.submit(work, p) for p in todo]), 1):
            f.result()
            if n % 50 == 0:
                print(f"  {n}/{len(todo)}", flush=True)
    print(openai_api.usage_summary())


def cmd_recompare_crisis():
    """Re-ask, with the current prompt, every main and reversed comparison that involves a v10 level-0 text.
    Results go to verdicts_crisis.jsonl and replace the earlier verdicts for the same pairs."""
    level = {s["id"]: s["v10_level"] for s in json.loads(SAMPLE.read_text())}
    target = [p for p in json.loads(PAIRS.read_text()) if p["mode"] == "passages" and 0 in (level[p["a"]], level[p["b"]])]
    out = HERE / "verdicts_crisis.jsonl"
    done = {r["key"] for r in jsonl(out)}
    global VERDICTS
    VERDICTS, original = out, VERDICTS
    pending = [p for p in target if p["key"] not in done]
    PAIRS_BACKUP = PAIRS.read_text()
    try:
        PAIRS.write_text(json.dumps(pending), encoding="utf-8")
        cmd_compare()
    finally:
        PAIRS.write_text(PAIRS_BACKUP, encoding="utf-8")
        VERDICTS = original


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    if command == "sample":
        cmd_sample()
    elif command == "extract":
        cmd_extract(limit)
    elif command == "compare":
        cmd_compare(limit)
    elif command == "recompare-crisis":
        cmd_recompare_crisis()
    elif command == "report":
        import report
        report.main()
    else:
        sys.exit(__doc__)
