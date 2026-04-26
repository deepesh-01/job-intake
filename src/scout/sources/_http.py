"""Shared HTTP client for source modules. 1 req/sec polite throttle per host."""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

import httpx

USER_AGENT = (
    "job-intake/0.1 (personal scraper; contact via repo)"
)
DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)

_last_hit: dict[str, float] = defaultdict(float)
_MIN_INTERVAL = 1.0  # §10.3 — 1 req/sec/domain


def _throttle(host: str) -> None:
    elapsed = time.monotonic() - _last_hit[host]
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_hit[host] = time.monotonic()


def get_json(url: str, *, params: dict[str, Any] | None = None, retries: int = 3) -> Any:
    """GET with exponential backoff on 429/5xx. Raises httpx.HTTPError on failure."""
    host = httpx.URL(url).host
    backoff = 1.0
    last_exc: Exception | None = None
    for attempt in range(retries):
        _throttle(host)
        try:
            r = httpx.get(
                url,
                params=params,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=DEFAULT_TIMEOUT,
                follow_redirects=True,
            )
            if r.status_code == 429 or 500 <= r.status_code < 600:
                last_exc = httpx.HTTPStatusError(
                    f"{r.status_code} from {url}", request=r.request, response=r
                )
                time.sleep(backoff)
                backoff *= 2
                continue
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            last_exc = e
            time.sleep(backoff)
            backoff *= 2
    assert last_exc is not None
    raise last_exc
