"""OpenAI Responses API calls for scoring, one at a time or through the Batch API.

This file is identical in the Fed, BOE and BOJ projects. score.py builds the prompt and turns a
response into a stored score; this module only talks to the API.

  request_body()   one scoring request: the scoring instructions as a developer message marked as the
                   only part to cache, the speech as the user message, the response schema as a strict
                   JSON schema, the configured reasoning effort
  create()         send one request now (daily runs: a handful of new speeches)
  BatchQueue       send many requests through the Batch API (backfills: half price, results
                   within 24 hours). Submitted batches are recorded in data/batches.json, so a
                   run can stop at any time; the next run collects finished batches first.

  usage_summary()  token use of every response read so far (input, cache reads, cache writes, output)

Prompt caching. GPT-5.6 and later bill a cache write at 1.25x the input price, and by default
("implicit" mode) every request writes its whole prompt to the cache. Every speech is different, so
those entries are never read again and the speech tokens cost 25% more for nothing. Requests here
use explicit mode with one breakpoint at the end of the scoring instructions: only that shared part
(about 2,000 tokens, above the 1,024-token minimum) is written, later requests read it at 0.1x, and
the speech text is billed at the plain input rate with no write.

Batch API: https://developers.openai.com/api/docs/guides/batch
Prompt caching: https://developers.openai.com/api/docs/guides/prompt-caching
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Callable

import requests

API = "https://api.openai.com/v1"
DONE = {"completed", "expired", "cancelled", "failed"}     # batch statuses that will not change again


class APIError(Exception):
    def __init__(self, status_code: int, body: str):
        super().__init__(f"HTTP {status_code}: {body[:500]}")
        self.status_code = status_code
        self.body = body

    @property
    def code(self) -> str:
        try:
            return (json.loads(self.body).get("error") or {}).get("code") or ""
        except (ValueError, AttributeError):
            return ""


def strict_schema(schema: dict) -> dict:
    """A pydantic JSON schema in the form strict structured outputs require: no titles, every
    property required, no additional properties."""
    schema = copy.deepcopy(schema)

    def fix(node):
        if isinstance(node, dict):
            node.pop("title", None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                fix(value)
        elif isinstance(node, list):
            for value in node:
                fix(value)
    fix(schema)
    return schema


def request_body(model: str, effort: str, max_output_tokens: int, system: str, user: str, schema: dict) -> dict:
    # Temperature is not sent: reasoning models only accept it with reasoning effort "none".
    # store=False: nothing is kept server-side. The instructions go in a developer message rather
    # than top-level "instructions", which cannot carry a cache breakpoint.
    return {
        "model": model,
        "input": [
            {"role": "developer", "content": [
                {"type": "input_text", "text": system, "prompt_cache_breakpoint": {"mode": "explicit"}}]},
            {"role": "user", "content": [{"type": "input_text", "text": user}]},
        ],
        "prompt_cache_options": {"mode": "explicit"},
        "reasoning": {"effort": effort},
        "text": {"format": {"type": "json_schema", "name": "speech_score", "schema": strict_schema(schema), "strict": True}},
        "max_output_tokens": max_output_tokens,
        "store": False,
    }


def prompt_text(body: dict) -> str:
    """All the prompt text of a request body (for token estimates)."""
    return "".join(part["text"] for message in body["input"] for part in message["content"])


USAGE = {"responses": 0, "input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "reasoning": 0}


def count_usage(data: dict) -> None:
    usage = data.get("usage") or {}
    details = usage.get("input_tokens_details") or {}
    USAGE["responses"] += 1
    USAGE["input"] += usage.get("input_tokens") or 0
    USAGE["cache_read"] += details.get("cached_tokens") or 0
    USAGE["cache_write"] += details.get("cache_write_tokens") or 0
    USAGE["output"] += usage.get("output_tokens") or 0
    USAGE["reasoning"] += (usage.get("output_tokens_details") or {}).get("reasoning_tokens") or 0


def usage_summary() -> str:
    u = USAGE
    if not u["responses"]:
        return "Token use: no responses read."
    plain = u["input"] - u["cache_read"] - u["cache_write"]
    return (f"Token use over {u['responses']} responses: input {u['input']:,} (plain {plain:,}, cache reads "
            f"{u['cache_read']:,}, cache writes {u['cache_write']:,}); output {u['output']:,} "
            f"(of which reasoning {u['reasoning']:,}).")


def output_text(data: dict) -> str:
    """The JSON text of a Responses API result. Raises ValueError for refusals and truncated output."""
    count_usage(data)
    if data.get("status") not in (None, "completed"):
        reason = (data.get("incomplete_details") or {}).get("reason") or data.get("status")
        raise ValueError(f"response not completed ({reason})")
    texts = []
    for item in data.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") == "refusal":
                raise ValueError(f"model refused: {part.get('refusal', '')[:200]}")
            if part.get("type") == "output_text":
                texts.append(part.get("text", ""))
    if not texts:
        raise ValueError("response has no output text")
    return "".join(texts)


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


def _check(r: requests.Response) -> requests.Response:
    if r.status_code != 200:
        raise APIError(r.status_code, r.text)
    return r


def create(api_key: str, body: dict, timeout: int = 600) -> dict:
    """Send one request now. Each call is exactly one HTTP request (retries are the caller's)."""
    return _check(requests.post(f"{API}/responses", json=body, headers=_headers(api_key), timeout=timeout)).json()


def estimate_tokens(text: str) -> int:
    """Rough upper estimate of a request's input tokens, for staying under the batch queue limit."""
    cjk = len(re.findall(r"[　-鿿＀-￯]", text))
    return int((len(text) - cjk) / 3.5 + cjk * 1.2) + 50


class BatchQueue:
    """Batches submitted for one score vintage, recorded in a JSON file next to the scores."""

    def __init__(self, api_key: str, path: Path, scorer_version: str):
        self.api_key = api_key
        self.path = path
        self.scorer_version = scorer_version
        self.batches = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def _save(self) -> None:
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.batches, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)

    def open_batches(self) -> list[dict]:
        return [b for b in self.batches if b["status"] not in DONE and b["scorer_version"] == self.scorer_version]

    def pending(self) -> dict[str, str]:
        """{speech id: what was submitted for it} in batches not yet collected."""
        return {i: tag for b in self.open_batches() for i, tag in b["requests"].items()}

    def queued_tokens(self) -> int:
        return sum(b["estimated_tokens"] for b in self.open_batches())

    def collect(self, on_result: Callable[[str, str, str], bool], on_failure: Callable[[str, str], None]) -> dict:
        """Check every open batch; for a finished one, pass each result to on_result(id, tag, json text)
        and each failed request to on_failure(id, reason). Unfinished batches are left for a later run."""
        counts = {"scored": 0, "failed": 0, "still_running": 0}
        for batch in self.open_batches():
            info = _check(requests.get(f"{API}/batches/{batch['id']}", headers=_headers(self.api_key), timeout=60)).json()
            batch["request_counts"] = info.get("request_counts")
            if info["status"] not in DONE:
                counts["still_running"] += 1
                print(f"  batch {batch['id']}: {info['status']} {info.get('request_counts')}")
                continue
            for line in self._lines(info.get("output_file_id")):
                tag = batch["requests"].get(line["custom_id"], "")
                response = line.get("response") or {}
                try:
                    if line.get("error") or response.get("status_code") != 200:
                        raise ValueError(str(line.get("error") or response.get("body"))[:300])
                    ok = on_result(line["custom_id"], tag, output_text(response["body"]))
                except ValueError as error:
                    on_failure(line["custom_id"], str(error))
                    ok = False
                counts["scored" if ok else "failed"] += 1
            for line in self._lines(info.get("error_file_id")):
                on_failure(line["custom_id"], str(line.get("error") or (line.get("response") or {}).get("body"))[:300])
                counts["failed"] += 1
            if info.get("errors"):
                print(f"  batch {batch['id']} errors: {json.dumps(info['errors'])[:500]}")
            batch["status"] = info["status"]
            batch["collected_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            self._save()
            print(f"  batch {batch['id']}: {info['status']}, collected")
        self._save()
        return counts

    def _lines(self, file_id: str | None) -> list[dict]:
        if not file_id:
            return []
        r = _check(requests.get(f"{API}/files/{file_id}/content", headers=_headers(self.api_key), timeout=300))
        return [json.loads(line) for line in r.text.split("\n") if line.strip()]   # not splitlines(): see submit()

    def submit(self, requests_: list[tuple[str, str, dict]], token_budget: int, max_requests: int) -> int:
        """Submit (id, tag, body) requests in batches while the queued-token estimate stays under
        token_budget. Returns how many were submitted; the rest wait for a later run."""
        submitted = 0
        chunk, chunk_tokens = [], 0
        room = token_budget - self.queued_tokens()

        def send():
            nonlocal chunk, chunk_tokens, submitted, room
            if not chunk:
                return
            # ASCII-escaped JSON: speech texts can hold line-separator characters that would break JSONL lines.
            lines = "".join(json.dumps({"custom_id": i, "method": "POST", "url": "/v1/responses", "body": body}) + "\n"
                            for i, _, body in chunk)
            upload = _check(requests.post(f"{API}/files", headers=_headers(self.api_key), data={"purpose": "batch"},
                                          files={"file": ("speeches.jsonl", lines.encode("utf-8"), "application/jsonl")},
                                          timeout=600)).json()
            batch = _check(requests.post(f"{API}/batches", headers=_headers(self.api_key), timeout=60, json={
                "input_file_id": upload["id"], "endpoint": "/v1/responses", "completion_window": "24h",
                "metadata": {"scorer_version": self.scorer_version}})).json()
            self.batches.append({"id": batch["id"], "status": batch["status"], "scorer_version": self.scorer_version,
                                 "submitted_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                 "input_file_id": upload["id"], "estimated_tokens": chunk_tokens,
                                 "requests": {i: tag for i, tag, _ in chunk}})
            self._save()
            print(f"  submitted batch {batch['id']}: {len(chunk)} speeches, ~{chunk_tokens:,} input tokens")
            submitted += len(chunk)
            room -= chunk_tokens
            chunk, chunk_tokens = [], 0

        for speech_id, tag, body in requests_:
            tokens = estimate_tokens(prompt_text(body))
            if chunk_tokens + tokens > room:
                send()
                if tokens > room:
                    break
            chunk.append((speech_id, tag, body))
            chunk_tokens += tokens
            if len(chunk) >= max_requests:
                send()
        send()
        return submitted
