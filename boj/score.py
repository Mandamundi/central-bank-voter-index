"""Score every stored speech by a policy voter whose membership term covers the speech date that has no score yet, newest first, with Gemini.

Safe to stop and rerun at any time: each result is appended to data/scores.jsonl as soon as
it arrives. When the daily free-tier quota is exhausted the script stops cleanly; rerun it
the next day to continue.

    python score.py              # score everything outstanding
    python score.py --limit 20   # score at most 20 speeches

Requests run in parallel (free-tier calls take ~30-90s each). Every request, including
retries, passes one shared limiter (config.REQUESTS_PER_MINUTE per rolling minute). The Gemini
REST endpoint is called directly, so each attempt is exactly one HTTP request (the SDK retries
behind the caller's back). A 429 on
the per-day quota stops the run at once; a 429 on a per-minute limit waits and retries.
"""
from __future__ import annotations

import argparse
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
import prompt
import store
import voters
import policy
import unicodedata


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", "", s).casefold()


def verify_quotes(quotes: list[str], text: str) -> list[bool]:
    body = _norm(text)
    return [bool(_norm(q)) and _norm(q) in body for q in quotes]


INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"


class APIError(Exception):
    def __init__(self, status_code: int, body: str):
        super().__init__(f"HTTP {status_code}: {body[:500]}")
        self.status_code = status_code
        self.body = body


def output_text(data: dict) -> str:
    """Text of the model's final output (same rule as the google-genai SDK's output_text)."""
    for step in reversed(data.get("steps") or []):
        if step.get("type") == "model_output":
            texts = [c.get("text", "") for c in step.get("content") or [] if c.get("type") == "text"]
            if texts:
                return "".join(texts)
    return "".join(o.get("text", "") for o in data.get("outputs") or [] if o.get("type") == "text")


def call_gemini(api_key: str, speech: dict, setting: str) -> prompt.SpeechScore:
    # Interactions API (https://ai.google.dev/gemini-api/docs/interactions). Temperature is left at
    # the default, as Google recommends for Gemini 3 models; a fixed seed and thinking level keep
    # runs as repeatable as the API allows. store=False: nothing is kept server-side.
    body = {
        "model": config.MODEL,
        "input": prompt.user_prompt(speech, setting),
        "system_instruction": prompt.system_prompt(),
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": prompt.SpeechScore.model_json_schema(),
        },
        "generation_config": {"seed": config.SEED, "thinking_level": config.THINKING_LEVEL},
        "store": False,
    }
    r = requests.post(INTERACTIONS_URL, json=body, headers={"x-goog-api-key": api_key}, timeout=300)
    if r.status_code != 200:
        raise APIError(r.status_code, r.text)
    result = prompt.SpeechScore.model_validate_json(output_text(r.json()))
    if result.policy_relevant and result.level not in prompt.LEVELS:
        raise ValueError(f"invalid level {result.level}")
    return result


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


def is_daily_quota(e: Exception) -> bool:
    return "perday" in getattr(e, "body", str(e)).lower().replace(" ", "").replace("_", "")


def score_one(api_key: str, limiter: RateLimiter, stop: list, speech: dict, setting: str) -> prompt.SpeechScore | None:
    """Score one speech (up to 3 attempts). Returns None if it should be retried on a later run."""
    for _ in range(3):
        if stop:
            return None
        limiter.acquire()
        try:
            return call_gemini(api_key, speech, setting)
        except (ValueError, TypeError) as e:   # empty, malformed or invalid JSON
            print(f"  {speech['id']}: bad response ({str(e)[:120]}), retrying")
        except requests.RequestException as e:   # network error or timeout
            print(f"  {speech['id']}: network error ({str(e)[:80]}), retrying")
            time.sleep(10)
        except APIError as e:
            code = e.status_code
            if code in (400, 401, 403, 404):   # bad key, bad model ID, bad request
                raise FatalAPIError(f"Gemini API error {code}: {e}") from e
            if code == 429 and is_daily_quota(e):
                raise QuotaExhausted() from e
            if code == 429:
                print(f"  {speech['id']}: per-minute limit hit, waiting")
                limiter.pause()
            else:
                print(f"  {speech['id']}: API error {code}, retrying")
                time.sleep(10)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument('--preview', metavar='SPEECH_ID', help='Print the exact dated scoring prompt without calling the API')
    args = ap.parse_args()
    if args.preview:
        import json
        speech = next((s for s in store.load_speeches() if s['id'] == args.preview), None)
        if not speech:
            ap.error('Unknown speech ID')
        print(json.dumps({'system': prompt.system_prompt(), 'user': prompt.user_prompt(speech, policy.context_for(speech['date']))}, ensure_ascii=False, indent=2))
        return 0

    if args.limit is not None and args.limit < 1:
        ap.error("--limit must be positive")
    load_dotenv(config.ROOT / ".env")   # local runs; the GitHub Action sets the env var directly
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        message = "GEMINI_API_KEY is not set (put it in .env locally, or in this repository's Actions secrets)."
        existing_scores = store.load_scores()
        store.atomic_json(config.DATA / 'last_scoring_run.json', {
            'completed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'scorer_version': config.SCORER_VERSION, 'done': 0, 'failed': 0,
            'remaining': len([s for s in store.load_speeches() if s['id'] not in existing_scores]),
            'stop_reason': message, 'success': False})
        print(message)
        return 2
    limiter = RateLimiter(config.REQUESTS_PER_MINUTE)

    scored = store.load_scores()
    # only texts delivered during a recorded voter term
    voter_keys = set(voters.Roster().ever())
    if not voter_keys:
        sys.exit("No verified voter membership history available.")
    outstanding = [s for s in store.load_speeches() if s["id"] not in scored]
    roster = voters.Roster()
    todo = [s for s in outstanding if roster.is_voter(s["speaker"], s["date"])]
    print(f"Voter filter: {len(outstanding) - len(todo)} unscored speeches excluded because their speaker does not match voter history.")
    todo.sort(key=lambda s: (s["date"], s["id"]), reverse=True)
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(todo)} speeches to score with {config.SCORER_VERSION}")

    lock = threading.Lock()
    counts = {"done": 0, "failed": 0}
    stop: list[str] = []          # set by the first quota/fatal error

    def job(speech: dict) -> None:
        try:
            result = score_one(key, limiter, stop, speech, policy.context_for(speech["date"]))
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
            store.append_score({
                "id": speech["id"],
                "scorer_version": config.SCORER_VERSION,
                "input_hash": store.score_input_hash(speech),
                "content_hash": speech["content_hash"],
                "policy_context": policy.context_for(speech["date"]),
                "policy_relevant": result.policy_relevant,
                "level": result.level if result.policy_relevant else None,   # 0 from the model -> None
                "evidence": result.evidence,
                "evidence_verified": verify_quotes(result.evidence, speech["text"]),
                "rationale": result.rationale,
                "scored_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            })
            counts["done"] += 1
            tag = f"level {result.level}" if result.policy_relevant else "not policy"
            print(f"[{counts['done']}/{len(todo)}] {speech['date']} {speech['speaker']}: {tag}", flush=True)

    pending = set()
    with ThreadPoolExecutor(max_workers=config.MAX_IN_FLIGHT) as pool:
        for speech in todo:
            while len(pending) >= config.MAX_IN_FLIGHT and not stop:
                completed, pending = wait(pending, timeout=5, return_when=FIRST_COMPLETED)
                for future in completed:
                    future.result()
            if stop:
                break
            pending.add(pool.submit(job, speech))
            time.sleep(60.0 / config.REQUESTS_PER_MINUTE)   # spread first attempts evenly
        for f in pending:
            f.result()   # surface unexpected exceptions

    if stop and stop[0] == "quota":
        print(f"Daily quota reached after {counts['done']} speeches; rerun after midnight Pacific to continue.")
    elif stop:
        print(stop[0], file=sys.stderr)
    print(f"Scored {counts['done']}, failed {counts['failed']}.")
    final_scores = store.load_scores()
    remaining = [s['id'] for s in store.load_speeches()
                 if roster.is_voter(s['speaker'], s['date']) and s['id'] not in final_scores]
    store.atomic_json(config.DATA / 'last_scoring_run.json', {
        'completed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'scorer_version': config.SCORER_VERSION, 'done': counts['done'],
        'failed': counts['failed'], 'remaining': len(remaining), 'stop_reason': stop,
        'success': not stop or stop[0] == 'quota'})
    # A backlog or the daily quota is normal; only a fatal API error fails the run.
    return 2 if stop and stop[0] != 'quota' else 0


if __name__ == "__main__":
    sys.exit(main())
