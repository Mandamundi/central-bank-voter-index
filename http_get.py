"""One HTTP client for every source.

Some Reserve Bank sites refuse plain script requests, so requests go through curl_cffi with a
browser TLS fingerprint. A short pause between requests keeps the load on each site low.
"""
from __future__ import annotations

import time

from curl_cffi import requests

import config

_session = requests.Session(impersonate="chrome")
_last = 0.0


def get(url: str, attempts: int = 3) -> requests.Response:
    global _last
    for attempt in range(attempts):
        wait = config.REQUEST_PAUSE - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
        try:
            r = _session.get(url, timeout=90)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                time.sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except requests.RequestsError:
            if attempt == attempts - 1:
                raise
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("unreachable")
