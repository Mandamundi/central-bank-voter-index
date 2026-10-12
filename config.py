"""Settings shared by every script."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SPEECH_DIR = DATA / "speeches"          # one CSV per year: data/speeches/2025.csv
SCORES = DATA / "scores.jsonl"          # one JSON line per scored speech
SEEN_URLS = DATA / "seen_urls.txt"      # speech pages already checked, one "source<TAB>url" per line
VOTERS = DATA / "voters.csv"            # FOMC voters by date (see voters.py)
FED_FUNDS = DATA / "fed_funds.csv"      # FOMC target rate/range history
OUTPUT = ROOT / "output"

# Scoring model. Changing MODEL, PROMPT_VERSION (or SEED/THINKING_LEVEL, then bump PROMPT_VERSION) starts a new score vintage:
# score.py will re-score everything under the new SCORER_VERSION.
MODEL = "gemini-3.5-flash-lite"
PROMPT_VERSION = "v3"
SCORER_VERSION = f"{MODEL}:{PROMPT_VERSION}"
SEED = 20260922                        # fixed decoding seed
THINKING_LEVEL = "medium"               # minimal | low | medium | high
REQUESTS_PER_MINUTE = 14               # free tier allows 15; requests run in parallel
MAX_IN_FLIGHT = 25                      # cap on waiting requests (each takes ~30-90s); pacing sets the rate

# Index
START_DATE = None                       # first output date (ISO); None = first day any voter has a score

# Speech collection
REQUEST_PAUSE = 1.0                     # seconds between requests to the Fed and Reserve Bank sites
MIN_TEXT_CHARS = 1000                   # shorter text needs explicit prepared-remarks cues

# Collector outcomes are separate from the legacy seen list. Bump after extraction changes.
PROCESSING = DATA / "processing.json"
COLLECTOR_VERSION = "v3.4"
HTTP_CONNECT_TIMEOUT = 5
HTTP_READ_TIMEOUT = 15
HTTP_RETRY_BUDGET = 45

SOURCE_FAILURE_LIMIT = 2
SOURCE_PAUSE_MINUTES = 60
