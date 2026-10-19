"""Settings shared by every script."""
from pathlib import Path

import rubric

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SPEECH_DIR = DATA / "speeches"          # one CSV per year: data/speeches/2025.csv
SCORES = DATA / "scores.jsonl"          # one JSON line per scored speech
VOTERS = DATA / "voters.csv"            # FOMC voters by date (see voters.py)
FED_FUNDS = DATA / "fed_funds.csv"      # FOMC target rate/range history
OUTPUT = ROOT / "output"

# Scoring model. Changing MODEL or rubric.VERSION (or REASONING_EFFORT, then bump rubric.VERSION) starts a new score vintage:
# score.py will re-score everything under the new SCORER_VERSION. Use the same settings in all three projects.
MODEL = "gpt-6-luna"
PROMPT_VERSION = rubric.VERSION          # shared with the BOJ and BOE projects
SCORER_VERSION = f"{MODEL}:{PROMPT_VERSION}"
REASONING_EFFORT = "medium"             # none | low | medium | high | xhigh | max (Luna's default is medium)
MAX_OUTPUT_TOKENS = 16000               # reasoning plus the JSON answer; a guard, far above normal use
REQUESTS_PER_MINUTE = 200               # direct mode; well inside the paid-tier limits
MAX_IN_FLIGHT = 50                      # cap on waiting requests in direct mode
BATCH_QUEUE_TOKENS = 15_000_000         # input tokens queued at once in batch mode (the lowest tier allows 20M)
BATCH_MAX_REQUESTS = 50_000             # Batch API limit per batch

# Index
START_DATE = None                       # first output date (ISO); None = first day any voter has a score

# Speech collection
REQUEST_PAUSE = 1.0                     # seconds between requests to the Fed and Reserve Bank sites
MIN_TEXT_CHARS = 1000                   # shorter text needs explicit prepared-remarks cues

# Outcome of every page checked. Bump COLLECTOR_VERSION after extraction changes to recheck exclusions and failures.
PROCESSING = DATA / "processing.json"
COLLECTOR_VERSION = "v4"
HTTP_CONNECT_TIMEOUT = 5
HTTP_READ_TIMEOUT = 15
HTTP_RETRY_BUDGET = 45

SOURCE_FAILURE_LIMIT = 2
SOURCE_PAUSE_MINUTES = 60
