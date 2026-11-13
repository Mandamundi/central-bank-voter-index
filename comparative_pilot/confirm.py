"""Confirmation run (Test 1): comparative scoring on a bank's full history, prompts frozen, adaptive pairing.

    cd boe && .venv/bin/python ../comparative_pilot/confirm.py prepare boe     # in the bank's own folder
    cd fed_speech_index
    .venv/bin/python ../comparative_pilot/confirm.py extract boe               # from here on, any folder
    .venv/bin/python ../comparative_pilot/confirm.py compare boe
    .venv/bin/python ../comparative_pilot/confirm.py report boe
    .venv/bin/python ../comparative_pilot/confirm.py cross boe boj             # pairs across two banks

prepare writes comparative_pilot/<bank>/texts.json (every relevant voter text with its v10 level, policy
setting and full text), so later steps need no bank code. Everything is resumable; finished calls are
skipped. Pass marks are fixed in PREREGISTRATION.md before the run.

Pairing is adaptive (simulation: 6 comparisons per text match the pilot's 8): in each of 3 rounds every text
gets one more pair, usually with a text whose current estimate is closest (1 in 4 with a random text, so
v10 band errors can still be caught); estimates are refitted between rounds.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Literal

sys.path.insert(0, os.getcwd())
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

HERE = Path(__file__).resolve().parent
WORKERS = 12
ROUNDS = 3
# Side tests (reversed, full text) per bank; the Fed gets more (PREREGISTRATION_FED.md). Not part of the prompts.
# Fed: the published index starts 2008-01-01 (config.START_DATE); texts from three years earlier are scored so
# that stances on that day rest on scored history (PREREGISTRATION_FED.md).
FROM = {"fed": "2005-01-01"}
SIDE = {"boe": (60, 40), "boj": (60, 40), "fed": (150, 100)}
SEED = 21
BANK_NAMES = {"fed": "Federal Reserve", "boe": "Bank of England", "boj": "Bank of Japan"}

# ---------------------------------------------------------------- frozen prompts (hashed in PREREGISTRATION.md)

EXTRACT_SYSTEM = """You are a senior central-bank analyst. From a speech by a member of a central bank's monetary
policy committee, copy out VERBATIM the passages in which the speaker states their own view on where monetary
policy should go over the next several months: the move they want or expect (or a hold), its timing and size,
the conditions or data it depends on, the risks they weigh, and any hedges or qualifications attached to those
views. Include sentences on the balance sheet or other instruments when they bear on the stance. In a crisis,
the speaker's support for the emergency response (emergency rate cuts, asset purchases, lending or liquidity
facilities) IS their policy view, even when the measures are already in place and nothing new is proposed:
copy those passages. Leave out descriptions of the economy, history or other people's views unless the speaker
uses them directly to justify the policy view.

Copy text exactly as written, in the speech's own language (same words and order); you may cut a long passage
at sentence boundaries. Return 2-8 passages, about 350 words (or 700 Japanese characters) in total at most. If
the speech states no policy view, return an empty list."""

COMPARE_SYSTEM = """You are a senior central-bank analyst. You compare two speakers' views on monetary policy. They may
be at the same central bank or at different ones; judge each against their own bank's policy setting and usual
step.

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
- Supporting tapering, ending purchases or faster balance-sheet reduction is tightening; starting or expanding
  purchases is easing; purchases described as technical (reserve management, market functioning) do not change
  the stance.

Write one sentence saying what decided it, then the answer."""


class Passages(BaseModel):
    passages: List[str] = Field(description="Verbatim passages, in speech order.")


class Verdict(BaseModel):
    reason: str = Field(description="One sentence: what decided the comparison.")
    answer: Literal["A", "B", "same"] = Field(description="Which speaker wants the more hawkish change, or same.")


def prompt_hash():
    return hashlib.sha256((EXTRACT_SYSTEM + "\n----\n" + COMPARE_SYSTEM).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- files

def folder(bank):
    p = HERE / bank
    p.mkdir(exist_ok=True)
    return p


def jsonl(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


LOCK = threading.Lock()


def append(path, row):
    with LOCK, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_texts(bank):
    return json.loads((folder(bank) / "texts.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- API

def api_key():
    from dotenv import load_dotenv
    load_dotenv(Path.cwd() / ".env"); load_dotenv(HERE.parent / ".env")
    return os.environ["OPENAI_API_KEY"]


def call(system, user, model_cls, key, tries=5):
    import config
    import openai_api
    body = openai_api.request_body(config.MODEL, config.REASONING_EFFORT, config.MAX_OUTPUT_TOKENS, system, user,
                                   model_cls.model_json_schema())
    for attempt in range(tries):
        try:
            return model_cls.model_validate_json(openai_api.output_text(openai_api.create(key, body)))
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(min(60, 5 * 2 ** attempt) + random.random())


def run_parallel(items, work, label):
    with ThreadPoolExecutor(WORKERS) as ex:
        futures = [ex.submit(work, it) for it in items]
        failed = []
        for n, f in enumerate(as_completed(futures), 1):
            try:
                f.result()
            except Exception as e:          # one refused or broken call must not stop the run; logged and counted
                failed.append(str(e)[:120])
                print(f"  {label} FAILED: {str(e)[:120]}", flush=True)
            if n % 50 == 0:
                print(f"  {label} {n}/{len(items)}", flush=True)
    if failed:
        print(f"  {label}: {len(failed)} of {len(items)} failed after retries", flush=True)
    import openai_api
    print(openai_api.usage_summary())


# ---------------------------------------------------------------- prepare (in the bank's folder)

def cmd_prepare(bank):
    import build_index
    import store
    if bank == "fed":
        import score
        dates, levels = score.load_fed_funds()
        def setting(d):
            lv = score.fed_funds_on(d, dates, levels)
            return f"federal funds {'target range' if '-' in lv else 'target rate'} {lv}%."
        import prompt
        appendix = prompt.BANK.appendix
    else:
        import policy
        import prompt
        setting = policy.context_for
        appendix = prompt.BANK.appendix
    speeches = {s["id"]: s for s in store.load_speeches()}
    texts = []
    for s in build_index.policy_speeches():
        sp = speeches[s["id"]]
        texts.append({"id": s["id"], "bank": bank, "date": sp["date"], "available_date": s.get("available_date", sp["date"]),
                      "speaker": sp["speaker"], "title": sp["title"], "v10_level": s["level"], "setting": setting(sp["date"]),
                      "text": sp["text"]})
    texts = list({t["id"]: t for t in texts}.values())
    if bank in FROM:
        texts = [t for t in texts if t["date"] >= FROM[bank]]
    (folder(bank) / "texts.json").write_text(json.dumps(texts, ensure_ascii=False), encoding="utf-8")
    (folder(bank) / "appendix.txt").write_text(appendix, encoding="utf-8")
    print(f"{bank}: {len(texts)} relevant voter texts; v10 levels {dict(sorted(Counter(t['v10_level'] for t in texts).items()))}")


# ---------------------------------------------------------------- extract

def _letters(text):
    return re.sub(r"[\W\d_]+", "", text.lower())


def _grams(text, k=12):
    s = _letters(text)
    return {s[i:i + k] for i in range(max(len(s) - k + 1, 1))}


def verbatim(passages, text):
    """Passages really in the text, in any script: letters only (PDF footnote numbers, page breaks and broken
    ligatures are ignored); failing that, 80% of the passage's 12-letter runs must occur in the text."""
    body, grams = _letters(text), _grams(text)
    kept = []
    for p in passages:
        p = p.strip('“”"「」')
        g = _grams(p)
        if _letters(p) and (_letters(p) in body or len(g & grams) >= 0.8 * len(g)):
            kept.append(p)
    return kept


def cmd_extract(bank):
    key = api_key()
    out = folder(bank) / "passages.jsonl"
    done = {r["id"] for r in jsonl(out)}
    todo = [t for t in load_texts(bank) if t["id"] not in done]
    print(f"{bank}: extracting {len(todo)} texts ({len(done)} done)")

    def work(t):
        user = f"Speaker: {t['speaker']} ({BANK_NAMES[bank]})\nDate: {t['date']}\nTitle: {t['title']}\n\nSPEECH TEXT:\n{t['text'][:150_000]}"
        try:
            got = call(EXTRACT_SYSTEM, user, Passages, key).passages
        except ValueError as e:
            if "content_filter" not in str(e):
                raise
            # Refused by the API's content filter on every retry: no passages, so comparisons use the full text.
            append(out, {"id": t["id"], "passages": [], "returned": 0, "verbatim": 0, "error": "content_filter"})
            raise
        kept = verbatim(got, t["text"])
        append(out, {"id": t["id"], "passages": kept, "returned": len(got), "verbatim": len(kept)})
    run_parallel(todo, work, "extract")
    rows = jsonl(out)
    print(f"  passages returned {sum(r['returned'] for r in rows)}, verbatim {sum(r['verbatim'] for r in rows)}; "
          f"texts with none: {sum(not r['passages'] for r in rows)}")


# ---------------------------------------------------------------- compare

def describe(label, t, passages, appendix_seen, full=False):
    head = f"SPEAKER {label}: {t['speaker']}, {BANK_NAMES[t['bank']]}, {t['date']}. Policy setting then: {t['setting']}"
    if full or not passages:
        return f"{head}\nFULL SPEECH TEXT:\n{t['text'][:60_000]}"
    return head + "\nTheir words on policy:\n" + "\n".join(f"- {p}" for p in passages)


def ask_pairs(items, texts, passages, appendices, key, out):
    def work(p):
        a, b = texts[p["a"]], texts[p["b"]]
        notes = "\n\n".join(f"Notes on the {BANK_NAMES[bk]}:\n{appendices[bk].strip()}" for bk in dict.fromkeys([a["bank"], b["bank"]]) if appendices.get(bk, "").strip())
        user = (notes + "\n\n" if notes else "") + describe("A", a, passages[a["id"]], None, p["mode"] == "full") + "\n\n" + \
            describe("B", b, passages[b["id"]], None, p["mode"] == "full")
        v = call(COMPARE_SYSTEM, user, Verdict, key)
        append(out, {**p, "answer": v.answer, "reason": v.reason, "prompt": prompt_hash()})
    run_parallel(items, work, "compare")


def bank_material(bank):
    texts = {t["id"]: t for t in load_texts(bank)}
    passages = {r["id"]: r["passages"] for r in jsonl(folder(bank) / "passages.jsonl")}
    missing = [i for i in texts if i not in passages]
    if missing:
        sys.exit(f"{bank}: {len(missing)} texts not extracted yet")
    appendix = (folder(bank) / "appendix.txt").read_text(encoding="utf-8")
    return texts, passages, appendix


def cmd_compare(bank):
    import combined
    key = api_key()
    texts, passages, appendix = bank_material(bank)
    out = folder(bank) / "verdicts.jsonl"
    ids = sorted(texts)
    idx = {i: k for k, i in enumerate(ids)}
    y = np.array([texts[i]["v10_level"] for i in ids], float)
    rng = random.Random(SEED)
    for r in range(ROUNDS):
        rows = jsonl(out)
        seen = {tuple(sorted((x["a"], x["b"]))) for x in rows}
        if any(x.get("round") == r for x in rows) and sum(x.get("round") == r for x in rows) >= len(ids) // 2:
            continue                       # round finished earlier (resume)
        main = [x for x in rows if x["test"] == "main"]
        if main:
            est, _, _ = combined.fit(y, (np.array([idx[x["a"]] for x in main]), np.array([idx[x["b"]] for x in main]),
                                         np.array([{"A": 1.0, "B": 0.0, "same": 0.5}[x["answer"]] for x in main])), 7, 4, steps=1500)
        else:
            est = y * 10
        count = Counter(i for x in main for i in (x["a"], x["b"]))
        pairs, cap = [], 2 * (r + 1)
        for a in rng.sample(ids, len(ids)):
            if count[a] >= cap:
                continue
            others = [j for j in ids if j != a and tuple(sorted((a, j))) not in seen and count[j] < cap + 1]
            if not others:
                continue
            if rng.random() < 0.25:
                b = rng.choice(others)
            else:
                b = rng.choice(sorted(others, key=lambda j: abs(est[idx[j]] - est[idx[a]]))[:3])
            seen.add(tuple(sorted((a, b)))); count[a] += 1; count[b] += 1
            if rng.random() < 0.5:
                a, b = b, a
            pairs.append({"key": f"p|{a}|{b}", "a": a, "b": b, "mode": "passages", "test": "main", "round": r})
        print(f"{bank}: round {r + 1}/{ROUNDS}: {len(pairs)} comparisons")
        ask_pairs(pairs, texts, passages, {bank: appendix}, key, out)
    rows = jsonl(out)
    if not any(x["test"] != "main" for x in rows):
        main = [x for x in rows if x["test"] == "main"]
        side = [{"key": f"p|{x['b']}|{x['a']}", "a": x["b"], "b": x["a"], "mode": "passages", "test": "reversed"} for x in rng.sample(main, SIDE[bank][0])]
        side += [{"key": f"f|{x['a']}|{x['b']}", "a": x["a"], "b": x["b"], "mode": "full", "test": "full_text"} for x in rng.sample(main, SIDE[bank][1])]
        print(f"{bank}: side tests: {len(side)} comparisons")
        ask_pairs(side, texts, passages, {bank: appendix}, key, out)


def cmd_cross(bank1, bank2, n=120):
    """Pairs across two banks, matched on their separately fitted scores. Tests whether the two scales agree."""
    key = api_key()
    m1, m2 = bank_material(bank1), bank_material(bank2)
    texts = {**m1[0], **m2[0]}; passages = {**m1[1], **m2[1]}
    s1 = {r["id"]: float(r["combined"]) for r in json.loads((folder(bank1) / "scores.json").read_text())}
    s2 = {r["id"]: float(r["combined"]) for r in json.loads((folder(bank2) / "scores.json").read_text())}
    out = folder(f"cross_{bank1}_{bank2}") / "verdicts.jsonl"
    if not jsonl(out):
        rng = random.Random(SEED)
        pairs = []
        for a in rng.sample(sorted(s1), min(n, len(s1))):
            b = rng.choice(sorted(s2, key=lambda j: abs(s2[j] - s1[a]))[:8])   # close on score: the informative pairs
            x, z = (a, b) if rng.random() < 0.5 else (b, a)
            pairs.append({"key": f"p|{x}|{z}", "a": x, "b": z, "mode": "passages", "test": "cross"})
        ask_pairs(pairs, texts, passages, {bank1: m1[2], bank2: m2[2]}, key, out)
    rows = jsonl(out)
    score = {**s1, **s2}
    import combined
    fits = [json.loads((folder(b) / "results.json").read_text())["fit"] for b in (bank1, bank2)]
    s, nu = float(np.mean([f["s"] for f in fits])), float(np.mean([f["nu"] for f in fits]))
    dec =[r for r in rows if r["answer"] != "same"]
    right = sum((score[r["a"]] > score[r["b"]]) == (r["answer"] == "A") for r in dec)
    probs = [combined.predict(np.array([score[r["a"]], score[r["b"]]]), s, nu, 0, 1) for r in rows]
    loss = -np.mean([math.log(max({"A": p[0], "B": p[1], "same": p[2]}[r["answer"]], 1e-9)) for r, p in zip(rows, probs)])
    res = {"pairs": len(rows), "decisive": len(dec), "accuracy_of_separate_scores": round(right / len(dec), 3),
           "log_loss": round(float(loss), 3), "share_first_bank_more_hawkish": round(
               sum((r["answer"] == "A") == (r["a"] in s1) for r in dec) / len(dec), 3)}
    (folder(f"cross_{bank1}_{bank2}") / "results.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    {"prepare": cmd_prepare, "extract": cmd_extract, "compare": cmd_compare, "cross": cmd_cross,
     "report": lambda b: __import__("confirm_report").main(b)}[cmd](*args)
