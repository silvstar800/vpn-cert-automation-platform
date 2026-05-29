#!/usr/bin/env python3
"""Simple operational smoke check for certsvc."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ENV_PATH = Path("/opt/certsvc/.env")
BASE_URLS = ["http://127.0.0.1", "http://127.0.0.1:8443"]
CHECK_PATHS = ["/health", "/clients", "/equipment-assets", "/leases", "/system/status"]


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def fetch(url: str, token: str = "", retries: int = 3) -> tuple[bool, str]:
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Internal-Token"] = token

    last_message = ""
    for attempt in range(retries):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read().decode("utf-8", "ignore")
                return True, f"{response.status} {body[:160]}"
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")
            last_message = f"HTTP {exc.code} {detail[:160]}"
        except Exception as exc:
            last_message = f"ERR {type(exc).__name__}: {exc}"

        if attempt < retries - 1:
            time.sleep(1)

    return False, last_message


if __name__ == "__main__":
    env = load_env(ENV_PATH)
    token = env.get("INTERNAL_API_TOKEN", "")
    failures = 0

    for base in BASE_URLS:
        print(f"\n== {base} ==")
        for path in CHECK_PATHS:
            ok, message = fetch(base + path, token=token)
            status = "PASS" if ok else "FAIL"
            print(f"[{status}] {path} -> {message}")
            if not ok:
                failures += 1

    print(f"\nTOTAL_FAILURES={failures}")
    sys.exit(1 if failures else 0)
