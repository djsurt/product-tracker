"""Pipeline metrics as Redis counters (Phase 5 observability).

The API, the workers, and Beat are *separate processes*. A plain in-process
counter would only ever see its own slice of the work, and the standard fix
(prometheus_client multiprocess mode) needs shared files and extra setup. Since
every process already shares Redis, we just `INCR` counters there — one source
of truth across the whole fleet — and render them in Prometheus text format at
`/metrics`.

Tradeoff: these are coarse counters (no histograms/labels). That's plenty to
demonstrate "watch fetches/failures/dead-letters climb as the pipeline runs";
a production setup would graduate to real prometheus_client instrumentation.
"""

from __future__ import annotations

import redis

from core.cache import get_redis

# Registered counters: name -> help text (shown in the Prometheus # HELP line).
COUNTERS: dict[str, str] = {
    "deal_fetch_success_total": "Successful offer fetches",
    "deal_fetch_retry_total": "Offer fetch attempts that were retried",
    "deal_fetch_failure_total": "Offer fetches that failed an attempt",
    "deal_dead_letter_total": "Tasks parked in the dead-letter table",
    "deal_notify_sent_total": "Alert notifications sent",
    "deal_redirect_click_total": "Clicks through the /go redirect",
}

_PREFIX = "metrics:"


def inc(name: str, amount: int = 1, client: redis.Redis | None = None) -> None:
    """Increment a counter. Never let metrics break the actual work."""
    try:
        (client or get_redis()).incrby(f"{_PREFIX}{name}", amount)
    except Exception:  # noqa: BLE001 - metrics are best-effort
        pass


def render(client: redis.Redis | None = None) -> str:
    """Render all counters in Prometheus text exposition format."""
    r = client or get_redis()
    lines: list[str] = []
    for name, help_text in COUNTERS.items():
        try:
            value = int(r.get(f"{_PREFIX}{name}") or 0)
        except Exception:  # noqa: BLE001
            value = 0
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} counter")
        lines.append(f"{name} {value}")
    return "\n".join(lines) + "\n"


def snapshot(client: redis.Redis | None = None) -> dict[str, int]:
    """Return counters as a dict for the HTML ops dashboard.

    Like `render`, this is best-effort. If Redis is unavailable, the dashboard
    should still load and show zeros rather than hiding the rest of the state.
    """
    try:
        r = client or get_redis()
    except Exception:  # noqa: BLE001
        return {name: 0 for name in COUNTERS}

    values: dict[str, int] = {}
    for name in COUNTERS:
        try:
            values[name] = int(r.get(f"{_PREFIX}{name}") or 0)
        except Exception:  # noqa: BLE001
            values[name] = 0
    return values
