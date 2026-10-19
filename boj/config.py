"""Paths and settings for this project. rubric.py is a copy shared with the Fed and sibling projects."""
from pathlib import Path

import rubric

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
SPEECH_DIR = DATA / 'speeches'
SCORES = DATA / 'scores.jsonl'
VOTERS = DATA / 'voters.csv'
PROCESSING = DATA / 'processing.json'
OUTPUT = ROOT / 'output'
BANK = 'boj'
BANK_NAME = 'Bank of Japan'
INDEX_PREFIX = BANK
BANK_TIMEZONE = 'Asia/Tokyo'

# None derives the first output day from complete verified membership coverage.
# This is NOT the first year of speeches available from the central bank.
START_DATE = None
BACKFILL_START = None
# Diagnostic only: old stances remain in the index until replaced, as in the Fed compiler.
STALE_STANCE_WARNING_DAYS = 180
RECHECK_DAYS = 7

# Same scoring settings as the Fed project. Changing MODEL or rubric.VERSION (or REASONING_EFFORT,
# then bump rubric.VERSION) starts a new score vintage.
MODEL = 'gpt-6-luna'
PROMPT_VERSION = rubric.VERSION        # shared with the Fed and sibling projects
SCORER_VERSION = f'{MODEL}:{PROMPT_VERSION}'
REASONING_EFFORT = 'medium'            # none | low | medium | high | xhigh | max (Luna's default is medium)
MAX_OUTPUT_TOKENS = 16000              # reasoning plus the JSON answer; a guard, far above normal use
REQUESTS_PER_MINUTE = 200              # direct mode
MAX_IN_FLIGHT = 50
BATCH_QUEUE_TOKENS = 15_000_000        # input tokens queued at once in batch mode (the lowest tier allows 20M)
BATCH_MAX_REQUESTS = 50_000            # Batch API limit per batch

REQUEST_PAUSE = 0.5
HTTP_CONNECT_TIMEOUT = 8
HTTP_READ_TIMEOUT = 30
HTTP_RETRY_BUDGET = 60
SOURCE_FAILURE_LIMIT = 3
SOURCE_PAUSE_MINUTES = 60
COLLECTOR_VERSION = 'v2'
# Small prepared opening remarks are legitimate records. Short HTML may be a PDF wrapper.
MIN_TEXT_CHARS = 200
MIN_HTML_TEXT_CHARS = 800
