"""Structured logging (Phase 5 observability).

Plain text logs are fine to read but miserable to *query*. structlog emits one
event per line with key/value context (offer_id, source, price, task), so in
production you can grep/aggregate "show me every fetch for offer X across all
workers" — the observability goal of tracing a price through the pipeline.

- **local**: pretty, colorized console output (human-friendly).
- **anything else**: JSON lines (machine-friendly for log aggregators).

`configure_logging()` is called once per process (API, worker, beat); after that
any module gets a logger via `get_logger(__name__)`.
"""

from __future__ import annotations

import logging

import structlog

from core.settings import get_settings


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(format="%(message)s", level=level)

    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer = (
        structlog.dev.ConsoleRenderer()
        if settings.env == "local"
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None):
    return structlog.get_logger(name)
