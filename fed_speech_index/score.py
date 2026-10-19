"""Score every stored speech by an FOMC voter (past or present) that has no score yet, newest first, with OpenAI.

Safe to stop and rerun at any time: each result is appended to data/scores.jsonl as soon as
it arrives.

    python score.py              # score everything outstanding now, one request per speech
    python score.py --limit 20   # score at most 20 speeches
    python score.py --batch      # backfill through the Batch API (half price, results within 24 hours)

Direct mode (the daily run) sends requests in parallel through one shared limiter
(config.REQUESTS_PER_MINUTE per rolling minute). A 429 for a per-minute limit waits and retries;
a 429 for exhausted credit stops the run.

Batch mode first collects any finished batches into data/scores.jsonl, then submits the speeches
still outstanding, up to config.BATCH_QUEUE_TOKENS of queued input. Rerun it (every few hours,
say) until it reports nothing left; submitted batches are recorded in data/batches.json.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import os
import re
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from dotenv import load_dotenv
import requests

import config
import openai_api
import prompt
import store
import voters

BATCHES = config.DATA / "batches.json"


def load_fed_funds() -> tuple[list[str], list[str]]:
    with config.FED_FUNDS.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [r["date"] for r in rows], [r["level"] for r in rows]


def fed_funds_on(date: str, dates: list[str], levels: list[str]) -> str:
    i = bisect.bisect_right(dates, date) - 1
    return levels[max(i, 0)]


def _norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


def verify_quotes(quotes: list[str], text: str) -> list[bool]:
    body = _norm(text)
    return [_norm(q) in body for q in quotes]


def request_body(speech: dict, ff_level: str) -> dict:
    return openai_api.request_body(config.MODEL, config.REASONING_EFFORT, config.MAX_OUTPUT_TOKENS,
                                   prompt.system_prompt(), prompt.user_prompt(speech, ff_level),
                                   prompt.SpeechScore.model_json_schema())


def parse_result(text: str) -> prompt.SpeechScore:
    """The model's JSON as a score. Raises ValueError if it is malformed or the level is invalid."""
    result = prompt.SpeechScore.model_validate_json(text)
    if result.policy_relevant and result.level not in prompt.LEVELS:
        raise ValueError(f"invalid level {result.level}")
    return result


def call_openai(api_key: str, speech: dict, ff_level: str) -> prompt.SpeechScore:
    return parse_result(openai_api.output_text(openai_api.create(api_key, request_body(speech, ff_level))))


def score_record(speech: dict, result: prompt.SpeechScore) -> dict:
    return {
        "id": speech["id"],
        "scorer_version": config.SCORER_VERSION,
        "policy_relevant": result.policy_relevant,
        "level": result.level if result.policy_relevant else None,   # 0 from the model -> None
        "evidence": result.evidence,
        "evidence_verified": verify_quotes(result.evidence, speech["text"]),
        "rationale": result.rationale,
        "scored_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }


class QuotaExhausted(Exception):
    pass


class FatalAPIError(Exception):
    pass


class RateLimiter:
    """At most `per_minute` request starts in any rolling 60-second window (shared by all threads)."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.starts: list[float] = []
        self.lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                self.starts = [s for s in self.starts if now - s < 60]
                if len(self.starts) < self.per_minute:
                    self.starts.append(now)
                    return
                wait_s = 60 - (now - self.starts[0]) + 0.1
            time.sleep(wait_s)

    def pause(self) -> None:
        """After a per-minute 429, treat the current minute as full."""
        with self.lock:
            now = time.monotonic()
            self.starts = [now] * self.per_minute


def score_one(api_key: str, limiter: RateLimiter, stop: list, speech: dict, ff_level: str) -> prompt.SpeechScore | None:
    """Score one speech (up to 3 attempts). Returns None if it should be retried on a later run."""
    for _ in range(3):
        if stop:
            return None
        limiter.acquire()
        try:
            return call_openai(api_key, speech, ff_level)
        except (ValueError, TypeError) as e:   # empty, malformed, truncated or refused output
            print(f"  {speech['id']}: bad response ({str(e)[:120]}), retrying")
        except requests.RequestException as e:   # network error or timeout
            print(f"  {speech['id']}: network error ({str(e)[:80]}), retrying")
            time.sleep(10)
        except openai_api.APIError as e:
            code = e.status_code
            if code in (400, 401, 403, 404):   # bad key, bad model ID, bad request
                raise FatalAPIError(f"OpenAI API error {code}: {e}") from e
            if code == 429 and e.code == "insufficient_quota":
                raise QuotaExhausted() from e
            if code == 429:
                print(f"  {speech['id']}: rate limit hit, waiting")
                limiter.pause()
            else:
                print(f"  {speech['id']}: API error {code}, retrying")
                time.sleep(10)
    return None


def run_batch(key: str, todo: list[dict], ff_level) -> int:
    """Collect finished batches, then submit what is still outstanding."""
    queue = openai_api.BatchQueue(key, BATCHES, config.SCORER_VERSION)
    speeches = {s["id"]: s for s in todo}

    def on_result(speech_id: str, tag: str, text: str) -> bool:
        speech = speeches.get(speech_id)
        if not speech:      # scored meanwhile, or no longer stored
            return False
        try:
            result = parse_result(text)
        except ValueError as error:
            print(f"  {speech_id}: bad response ({str(error)[:120]}); will be resubmitted")
            return False
        store.append_score(score_record(speech, result))
        del speeches[speech_id]
        return True

    def on_failure(speech_id: str, reason: str) -> None:
        print(f"  {speech_id}: failed in batch ({reason[:120]}); will be resubmitted")

    print("Collecting finished batches...")
    collected = queue.collect(on_result, on_failure)
    print(f"  {collected['scored']} scored, {collected['failed']} to resubmit, {collected['still_running']} batches still running")
    waiting = queue.pending()
    outstanding = [s for s in todo if s["id"] in speeches and s["id"] not in waiting]
    submitted = queue.submit([(s["id"], "", request_body(s, ff_level(s))) for s in outstanding],
                             config.BATCH_QUEUE_TOKENS, config.BATCH_MAX_REQUESTS)
    left = len(outstanding) - submitted
    print(f"Submitted {submitted}; {len(queue.pending())} speeches in open batches; {left} waiting for queue room.")
    if left or queue.open_batches():
        print("Rerun `uv run score.py --batch` later to collect results and submit the rest.")
    else:
        print("Nothing left to score.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--batch", action="store_true", help="Score through the Batch API (backfills)")
    args = ap.parse_args()

    load_dotenv(config.ROOT / ".env")          # local runs: this project's .env, then the repository's shared one;
    load_dotenv(config.ROOT.parent / ".env")   # the GitHub Action sets the env var directly
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("OPENAI_API_KEY is not set (put it in .env locally, or in the repo's Actions secrets).")
    limiter = RateLimiter(config.REQUESTS_PER_MINUTE)

    ff_dates, ff_levels = load_fed_funds()
    scored = store.load_scores()
    # only speeches by people who were FOMC voters at some point (skips Reserve Bank staff speeches)
    voter_keys = set(voters.Roster().ever())
    if not voter_keys:
        sys.exit("No voter history available; run backfill.py without --skip-voters first.")
    outstanding = [s for s in store.load_speeches() if s["id"] not in scored]
    todo = [s for s in outstanding
            if any(voters.same_person(voters.name_key(s["speaker"]), k) for k in voter_keys)]
    print(f"Voter filter: {len(outstanding) - len(todo)} unscored speeches excluded because their speaker does not match voter history.")
    todo.sort(key=lambda s: (s["date"], s["id"]), reverse=True)
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(todo)} speeches to score with {config.SCORER_VERSION}")
    if args.batch:
        return run_batch(key, todo, lambda s: fed_funds_on(s["date"], ff_dates, ff_levels))
    waiting = openai_api.BatchQueue(key, BATCHES, config.SCORER_VERSION).pending()
    if waiting:
        todo = [s for s in todo if s["id"] not in waiting]
        print(f"  {len(waiting)} speeches are in open batches and are left to them (collect with --batch).")

    lock = threading.Lock()
    counts = {"done": 0, "failed": 0}
    stop: list[str] = []          # set by the first quota/fatal error

    def job(speech: dict) -> None:
        try:
            result = score_one(key, limiter, stop, speech, fed_funds_on(speech["date"], ff_dates, ff_levels))
        except QuotaExhausted:
            stop.append("quota")
            return
        except FatalAPIError as e:
            stop.append(str(e))
            return
        with lock:
            if result is None:
                if stop:
                    return
                counts["failed"] += 1
                print(f"  {speech['id']}: gave up for now (will retry next run)")
                return
            store.append_score(score_record(speech, result))
            counts["done"] += 1
            tag = f"level {result.level}" if result.policy_relevant else "not policy"
            print(f"[{counts['done']}/{len(todo)}] {speech['date']} {speech['speaker']}: {tag}", flush=True)

    pending = set()
    with ThreadPoolExecutor(max_workers=config.MAX_IN_FLIGHT) as pool:
        for speech in todo:
            while len(pending) >= config.MAX_IN_FLIGHT and not stop:
                _, pending = wait(pending, timeout=5, return_when=FIRST_COMPLETED)
            if stop:
                break
            pending.add(pool.submit(job, speech))
            time.sleep(60.0 / config.REQUESTS_PER_MINUTE)   # spread first attempts evenly
        for f in pending:
            f.result()   # surface unexpected exceptions

    if stop and stop[0] == "quota":
        sys.exit(f"OpenAI credit exhausted after {counts['done']} speeches; add credit and rerun.")
    elif stop:
        sys.exit(stop[0])
    print(f"Scored {counts['done']}, failed {counts['failed']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
