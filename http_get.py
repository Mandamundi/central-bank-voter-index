"""Bounded requests with source-wide failure protection and persistent diagnostics."""
from __future__ import annotations

import time
from urllib.parse import urlsplit
from curl_cffi import requests

import config
import runlog

_session = requests.Session(impersonate='chrome')
_last = 0.0
_failures = 0
_paused = None
_source = None
_listing_problems = []
TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}


class SourcePaused(RuntimeError):
    pass


def begin_source(name):
    global _failures, _paused, _source, _listing_problems
    _source, _failures, _paused = name, 0, None
    _listing_problems = []


def listing_problem(message):
    _listing_problems.append(message)
    runlog.detail(f'  listing warning: {message}')


def listing_problems():
    return list(_listing_problems)


def paused_reason():
    return _paused


def status_code(error):
    response = getattr(error, 'response', None)
    code = getattr(response, 'status_code', None)
    return code if isinstance(code, int) and code >= 100 else None


def temporary_failure(error):
    if isinstance(error, SourcePaused):
        return True
    if not isinstance(error, (requests.RequestsError, TimeoutError)):
        return False
    status = status_code(error)
    return status is None or status in TRANSIENT_STATUS


def _failed(error):
    global _failures, _paused
    if temporary_failure(error):
        _failures += 1
        if _source and _failures >= config.SOURCE_FAILURE_LIMIT:
            _paused = f'{_failures} consecutive connection/server failures: {error}'
    else:
        # A 404 proves the server responded. It must not trip the availability guard.
        _failures = 0


def get(url: str, attempts: int = 2) -> requests.Response:
    global _last, _failures
    if _paused:
        raise SourcePaused(_paused)
    if urlsplit(url).scheme not in {'http', 'https'} or not urlsplit(url).netloc:
        raise ValueError(f'Invalid HTTP URL: {url}')
    if attempts < 1:
        raise ValueError('attempts must be positive')
    start = time.monotonic()
    last_error = None
    for attempt in range(attempts):
        wait = max(0, config.REQUEST_PAUSE - (time.monotonic() - _last))
        if wait:
            time.sleep(wait)
        remaining = config.HTTP_RETRY_BUDGET - (time.monotonic() - start)
        if remaining <= 0:
            break
        _last = time.monotonic()
        runlog.detail(f'  GET {url} (attempt {attempt + 1}/{attempts}, elapsed {time.monotonic() - start:.0f}s)')
        response = None
        try:
            connect = min(config.HTTP_CONNECT_TIMEOUT, remaining / 2)
            read = min(config.HTTP_READ_TIMEOUT, remaining - connect)
            response = _session.get(url, timeout=(connect, read))
            response.raise_for_status()
            _failures = 0
            return response
        except requests.RequestsError as error:
            last_error = error
            code = getattr(error, 'code', None)
            status = response.status_code if response is not None and response.status_code >= 100 else None
            runlog.detail(f'  request failed after {time.monotonic() - start:.1f}s: {error}')
            if ((status is not None and status not in TRANSIENT_STATUS)
                    or code in {1, 3, 60} or attempt == attempts - 1):
                _failed(error)
                raise
            delay = min(5 * (2 ** attempt), max(0, config.HTTP_RETRY_BUDGET - (time.monotonic() - start)))
            runlog.detail(f'  retrying in {delay:.0f}s')
            time.sleep(delay)
    error = last_error or TimeoutError(f'HTTP retry budget exhausted for {url}')
    _failed(error)
    raise error
