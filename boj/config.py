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

MODEL = 'gemini-3.5-flash-lite'
PROMPT_VERSION = rubric.VERSION        # shared with the Fed and sibling projects
SCORER_VERSION = f'{MODEL}:{PROMPT_VERSION}'
SEED = 20260922                        # same decoding seed as the Fed project
THINKING_LEVEL = 'medium'
REQUESTS_PER_MINUTE = 14
MAX_IN_FLIGHT = 25

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
