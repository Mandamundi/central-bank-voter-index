#!/usr/bin/env python3
"""Request a FRASER API key or store the emailed key in this trial's ignored .env."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
KEY_URL = "https://fraser.stlouisfed.org/api/api_key"
DEFAULT_DESCRIPTION = "Local FOMC voting-member speech coverage audit"


def save_key(key: str) -> None:
    key = key.strip()
    if not key or any(ord(char) < 32 or ord(char) == 127 for char in key):
        raise ValueError("The API key must be nonempty and single-line.")
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    lines = [line for line in lines if not line.startswith("FRASER_API_KEY=")]
    lines.append(f"FRASER_API_KEY={key}")
    temp_file = ENV_FILE.with_name(".env.tmp")
    temp_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(temp_file, 0o600)
    os.replace(temp_file, ENV_FILE)
    os.chmod(ENV_FILE, 0o600)


def request_key(description: str) -> int:
    email = input("Email address for the FRASER key: ").strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        print("Enter a valid email address.", file=sys.stderr)
        return 2
    body = json.dumps({"email": email, "description": description}).encode("utf-8")
    request = Request(
        KEY_URL,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            # Never print the response body: a provider could return a credential there.
            print(f"FRASER accepted the key request (HTTP {response.status}).")
    except HTTPError as exc:
        print(f"FRASER key request failed with HTTP {exc.code}; response body suppressed.", file=sys.stderr)
        return 1
    except (URLError, TimeoutError, OSError) as exc:
        print(f"FRASER key request could not be completed: {exc.__class__.__name__}.", file=sys.stderr)
        return 1
    print("Check your inbox for the API key. Then run: uv run request_api_key.py save-key")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    request_parser = sub.add_parser("request", help="request a key by email")
    request_parser.add_argument("--description", default=DEFAULT_DESCRIPTION)
    sub.add_parser("save-key", help="save the emailed key to this folder's ignored .env")
    args = parser.parse_args()

    if args.command == "request":
        return request_key(args.description)
    key = getpass.getpass("Paste the FRASER API key (input hidden): ")
    try:
        save_key(key)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"Key saved to {ENV_FILE.name}; its contents were not displayed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
