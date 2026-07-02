"""Unit tests for the shared polite HTTP layer used by HTML scrapers.

The point of `PoliteClient` is to make our scrapers behave like a low-volume,
well-mannered client so they stay under anti-bot thresholds instead of fighting
them: per-host throttling, backoff that honors `Retry-After`, sane default
headers, and a loud `SourceBlockedError` when a host keeps refusing us (so the
pipeline can dead-letter instead of hammering a wall).

Everything that would sleep or read the clock is injected, so these tests are
deterministic and never actually wait.
"""

from __future__ import annotations

import httpx
import pytest

from sources._http import DEFAULT_HEADERS, PoliteClient, SourceBlockedError


class FakeClock:
    """Monotonic clock whose `sleep` advances `now`, so throttle math is exact."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class FakeHttp:
    """Stand-in for httpx.Client: returns canned responses in order."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict | None, dict | None]] = []

    def get(self, url, params=None, headers=None):
        self.calls.append((url, params, headers))
        return self.responses.pop(0)


def _resp(status: int, *, headers: dict | None = None, text: str = "ok") -> httpx.Response:
    return httpx.Response(
        status,
        headers=headers,
        text=text,
        request=httpx.Request("GET", "https://example.com/x"),
    )


def _client(http: FakeHttp, clock: FakeClock, **kw) -> PoliteClient:
    return PoliteClient(
        http=http,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        rate_per_sec=2,  # -> 0.5s min interval between calls to a host
        **kw,
    )


def test_get_returns_response_and_applies_default_headers():
    http = FakeHttp([_resp(200)])
    clock = FakeClock()
    client = _client(http, clock)

    resp = client.get("https://www.ebay.com/itm/123")

    assert resp.status_code == 200
    sent_headers = http.calls[0][2]
    assert sent_headers["User-Agent"] == DEFAULT_HEADERS["User-Agent"]
    assert "Accept-Language" in sent_headers


def test_caller_headers_override_defaults():
    http = FakeHttp([_resp(200)])
    clock = FakeClock()
    client = _client(http, clock)

    client.get("https://www.ebay.com/x", headers={"Accept-Language": "fr-FR"})

    assert http.calls[0][2]["Accept-Language"] == "fr-FR"


def test_throttles_back_to_back_requests_to_same_host():
    http = FakeHttp([_resp(200), _resp(200)])
    clock = FakeClock()
    client = _client(http, clock)

    client.get("https://us.shein.com/a")
    client.get("https://us.shein.com/b")

    # rate_per_sec=2 -> at least 0.5s spacing enforced via sleep on the 2nd call.
    assert clock.slept and clock.slept[0] >= 0.5


def test_does_not_throttle_across_different_hosts():
    http = FakeHttp([_resp(200), _resp(200)])
    clock = FakeClock()
    client = _client(http, clock)

    client.get("https://www.ebay.com/a")
    client.get("https://us.shein.com/b")

    assert clock.slept == []


def test_retries_on_429_honoring_retry_after():
    http = FakeHttp([_resp(429, headers={"Retry-After": "3"}), _resp(200)])
    clock = FakeClock()
    client = _client(http, clock)

    resp = client.get("https://www.ebay.com/itm/123")

    assert resp.status_code == 200
    assert 3 in clock.slept
    assert len(http.calls) == 2


def test_raises_source_blocked_after_exhausting_retries_on_403():
    http = FakeHttp([_resp(403), _resp(403), _resp(403)])
    clock = FakeClock()
    client = _client(http, clock, max_retries=2)

    with pytest.raises(SourceBlockedError):
        client.get("https://us.shein.com/blocked")

    # initial try + 2 retries = 3 attempts
    assert len(http.calls) == 3


def test_success_status_does_not_retry():
    http = FakeHttp([_resp(200)])
    clock = FakeClock()
    client = _client(http, clock, max_retries=2)

    client.get("https://www.ebay.com/ok")

    assert len(http.calls) == 1
