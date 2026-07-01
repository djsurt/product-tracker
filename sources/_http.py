"""PoliteClient: the shared "be a good citizen" HTTP layer for HTML scrapers.

The durable way to not get blocked at personal-tracking volume isn't to defeat
anti-bot systems — it's to never look like an abuser. This wraps an httpx.Client
and adds the few behaviors that keep us under the radar:

- **Per-host throttling** with jitter: at most `rate_per_sec` calls to any one
  host, spacing requests out (the single biggest factor in not tripping limits).
- **One live session per host** (cookies/keep-alive) via the shared httpx.Client,
  instead of a flurry of cold connections.
- **Backoff that honors `Retry-After`** on 429/503, with jittered exponential
  backoff otherwise.
- **A loud `SourceBlockedError`** once a host keeps refusing us (403, or retries
  exhausted), so the worker's retry/dead-letter path takes over rather than us
  hammering a wall.

Everything time-related (`sleep`, `monotonic`) and the RNG are injectable so the
behavior is unit-testable without real waiting. Scrapers use this as the default
client; tests that inject their own fake client bypass it entirely.
"""

from __future__ import annotations

import random
import time
from typing import Callable

import httpx

# A single, plausible desktop-Chrome header set. We are not running a UA-rotation
# farm — one honest, stable client identity at low volume is both calmer and far
# less maintenance than spoofing many.
DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Statuses that mean "slow down / try again", vs an outright block.
_RETRYABLE = {429, 503}
_BLOCKED = {403}


class SourceBlockedError(Exception):
    """Raised when a host keeps refusing us. Surfaced to the pipeline so the
    fetch can be retried later / dead-lettered, not silently swallowed."""


class PoliteClient:
    def __init__(
        self,
        *,
        http: httpx.Client | None = None,
        timeout: float = 10.0,
        rate_per_sec: int = 1,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self._http = http or httpx.Client(timeout=timeout, follow_redirects=True)
        self._min_interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._monotonic = monotonic
        self._rng = rng or random.Random()
        # Last request time per host, so spacing is enforced per-host.
        self._last_call: dict[str, float] = {}

    def get(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        merged = {**DEFAULT_HEADERS, **(headers or {})}
        host = httpx.URL(url).host or ""

        for attempt in range(self._max_retries + 1):
            self._throttle(host)
            resp = self._http.get(url, params=params, headers=merged)
            self._last_call[host] = self._monotonic()

            if resp.status_code in _BLOCKED:
                # A 403 is a block, not a transient hiccup. Pause (in case it's a
                # soft rate-block) and retry a few times before giving up loudly.
                if attempt >= self._max_retries:
                    raise SourceBlockedError(
                        f"{host} returned {resp.status_code} after "
                        f"{attempt + 1} attempts"
                    )
                self._sleep(self._backoff(attempt, resp))
                continue

            if resp.status_code in _RETRYABLE:
                if attempt >= self._max_retries:
                    raise SourceBlockedError(
                        f"{host} returned {resp.status_code} after "
                        f"{attempt + 1} attempts"
                    )
                self._sleep(self._backoff(attempt, resp))
                continue

            return resp

        # Unreachable: the loop either returns or raises.
        raise SourceBlockedError(f"{host}: exhausted retries")

    # --- internals ---
    def _throttle(self, host: str) -> None:
        """Sleep just long enough that calls to `host` are spaced out."""
        last = self._last_call.get(host)
        if last is None or self._min_interval <= 0:
            return
        elapsed = self._monotonic() - last
        wait = self._min_interval - elapsed
        if wait > 0:
            # A little jitter so requests aren't metronomically regular.
            self._sleep(wait + self._rng.uniform(0, self._min_interval / 2))

    def _backoff(self, attempt: int, resp: httpx.Response) -> float:
        """Prefer the server's `Retry-After`; otherwise jittered exponential."""
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return float(int(retry_after))
            except ValueError:
                pass  # HTTP-date form: fall through to computed backoff
        return self._backoff_base * (2**attempt) + self._rng.uniform(0, 1)
