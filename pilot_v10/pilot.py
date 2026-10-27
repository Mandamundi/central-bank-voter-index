"""Pilot of the v10 candidate rubric on a fixed sample, scored twice. Writes only inside pilot_v10/.

    python3 pilot_v10/pilot.py sample                       # from the repository root: draw the sample by rule
    cd boe && .venv/bin/python ../pilot_v10/pilot.py submit boe     # per bank, from its folder: submit one batch
    cd boe && .venv/bin/python ../pilot_v10/pilot.py collect boe    # collect it into pilot_v10/results_boe.jsonl
    python3 pilot_v10/pilot.py report                       # from the repository root: compare runs and v9

The bank's own data/scores.jsonl and data/batches.json are never touched: batches are recorded in
pilot_v10/batches_{bank}.json under the scorer version "gpt-6-luna:v10-pilot".
"""
from __future__ import annotations

import collections
import csv
import json
import os
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FOLDERS = {"fed": "fed_speech_index", "boe": "boe", "boj": "boj"}
RUNS = 2
SEED = 10

# Every text in these windows that v9 found policy-relevant, plus a seeded random draw per bank and year
# (relevant or not, so relevance changes show up too).
WINDOWS = [
    ("fed", "2020-02-15", "2020-04-30", "COVID emergency"),
    ("fed", "2022-06-01", "2022-07-15", "75bp hikes"),
    ("fed", "2023-12-01", "2024-01-31", "pivot debate"),
    ("boe", "2020-03-01", "2020-06-30", "COVID emergency"),
    ("boe", "2021-09-01", "2021-12-31", "before the first hike"),
    ("boj", "2022-10-01", "2024-04-30", "YCC changes and exit"),
]
PER_YEAR = 2
YEARS = range(2018, 2027)


def speeches(bank: str) -> list[dict]:
    csv.field_size_limit(sys.maxsize)
    rows = []
    for path in sorted((ROOT / FOLDERS[bank] / "data" / "speeches").glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as f:
            rows.extend({k: r[k] for k in ("id", "date", "speaker", "title")} for r in csv.DictReader(f))
    return rows


def v9_scores(bank: str) -> dict[str, dict]:
    path = ROOT / FOLDERS[bank] / "data" / "scores.jsonl"
    return {s["id"]: s for s in map(json.loads, path.read_text(encoding="utf-8").splitlines()) if s["scorer_version"].endswith(":v9")}


def sample() -> None:
    chosen = []
    for bank in FOLDERS:
        v9 = v9_scores(bank)
        pool = sorted((s for s in speeches(bank) if s["id"] in v9), key=lambda s: (s["date"], s["id"]))
        picked = set()
        for b, start, end, label in WINDOWS:
            if b == bank:
                for s in pool:
                    if start <= s["date"] <= end and v9[s["id"]]["policy_relevant"]:
                        picked.add(s["id"])
                        chosen.append({**s, "bank": bank, "why": f"window: {label}"})
        rng = random.Random(f"{SEED}-{bank}")
        for year in YEARS:
            rest = [s for s in pool if s["date"][:4] == str(year) and s["id"] not in picked]
            for s in rng.sample(rest, min(PER_YEAR, len(rest))):
                picked.add(s["id"])
                chosen.append({**s, "bank": bank, "why": f"random {year}"})
    for row in chosen:
        v = v9_scores(row["bank"])[row["id"]]
        row["v9_relevant"], row["v9_level"] = v["policy_relevant"], v["level"]
    (HERE / "sample.json").write_text(json.dumps(chosen, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(collections.Counter((r["bank"], r["why"].split(":")[0]) for r in chosen))
    print(f"{len(chosen)} texts, {len(chosen) * RUNS} requests")


def bank_setup(bank: str):
    """The bank's own modules (run from its folder with its venv) plus the candidate rubric."""
    sys.path[:0] = [os.getcwd(), str(HERE)]
    from dotenv import load_dotenv
    import config, openai_api, prompt, store, rubric_v10
    load_dotenv(config.ROOT / ".env")
    load_dotenv(config.ROOT.parent / ".env")
    if bank == "fed":
        import score
        dates, levels = score.load_fed_funds()

        def setting(day):
            level = score.fed_funds_on(day, dates, levels)
            return f"federal funds {'target range' if '-' in level else 'target rate'} {level}%."
    else:
        import policy
        setting = policy.context_for
    bank_def = prompt.BANK
    schema = rubric_v10.score_model(bank_def.policy_area)
    system = rubric_v10.system_prompt(bank_def)
    return config, openai_api, store, rubric_v10, setting, system, schema


def submit(bank: str) -> None:
    config, openai_api, store, rubric_v10, setting, system, schema = bank_setup(bank)
    ids = [r["id"] for r in json.loads((HERE / "sample.json").read_text()) if r["bank"] == bank]
    by_id = {s["id"]: s for s in store.load_speeches()}
    requests_ = []
    for i in ids:
        s = by_id[i]
        body = openai_api.request_body(config.MODEL, config.REASONING_EFFORT, config.MAX_OUTPUT_TOKENS, system,
                                       rubric_v10.user_prompt(s, setting(s["date"])), schema.model_json_schema())
        requests_ += [(f"{i}#{run}", "", body) for run in range(1, RUNS + 1)]
    queue = openai_api.BatchQueue(os.environ["OPENAI_API_KEY"], HERE / f"batches_{bank}.json", "gpt-6-luna:v10-pilot")
    if queue.open_batches():
        sys.exit("A pilot batch is already open for this bank; collect it first.")
    print(f"{bank}: submitted {queue.submit(requests_, config.BATCH_QUEUE_TOKENS, config.BATCH_MAX_REQUESTS)} requests")


def collect(bank: str) -> None:
    config, openai_api, store, rubric_v10, setting, system, schema = bank_setup(bank)
    import score
    by_id = {s["id"]: s for s in store.load_speeches()}
    out = HERE / f"results_{bank}.jsonl"
    queue = openai_api.BatchQueue(os.environ["OPENAI_API_KEY"], HERE / f"batches_{bank}.json", "gpt-6-luna:v10-pilot")

    def on_result(custom_id: str, _tag: str, text: str) -> bool:
        speech_id, run = custom_id.split("#")
        try:
            r = schema.model_validate_json(text)
        except ValueError as error:
            print(f"  {custom_id}: bad response ({str(error)[:100]})")
            return False
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"id": speech_id, "run": int(run), "policy_relevant": r.policy_relevant,
                                "level": r.level if r.policy_relevant else None, "evidence": r.evidence,
                                "evidence_verified": score.verify_quotes(r.evidence, by_id[speech_id]["text"]),
                                "rationale": r.rationale}, ensure_ascii=False) + "\n")
        return True

    counts = queue.collect(on_result, lambda i, reason: print(f"  {i}: failed ({reason[:100]})"))
    print(bank, counts, openai_api.usage_summary())


def report() -> None:
    rows = json.loads((HERE / "sample.json").read_text())
    res = collections.defaultdict(dict)
    for bank in FOLDERS:
        path = HERE / f"results_{bank}.jsonl"
        if path.exists():
            for r in map(json.loads, path.read_text(encoding="utf-8").splitlines()):
                res[r["id"]][r["run"]] = r
    lines = []
    for row in rows:
        runs = res.get(row["id"], {})
        if len(runs) < RUNS:
            continue
        a, b = runs[1], runs[2]
        lines.append({**row, "v10_levels": [a["level"], b["level"]], "v10_relevant": [a["policy_relevant"], b["policy_relevant"]],
                      "rationale": a["rationale"], "rationale_2": b["rationale"], "evidence": a["evidence"],
                      "quotes_ok": all(a["evidence_verified"]) and all(b["evidence_verified"])})
    (HERE / "pilot_results.json").write_text(json.dumps(lines, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(lines)} of {len(rows)} texts have both runs; written to pilot_v10/pilot_results.json")


if __name__ == "__main__":
    command = sys.argv[1]
    {"sample": sample, "report": report}.get(command, lambda: None)() if command in ("sample", "report") else \
        {"submit": submit, "collect": collect}[command](sys.argv[2])
