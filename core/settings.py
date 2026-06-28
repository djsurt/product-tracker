"""Centralized configuration loaded from environment variables.

Using pydantic-settings gives us typed, validated config in one place — a small
but important SWE habit: never read os.environ scattered across the codebase.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "local"
    log_level: str = "info"

    database_url: str = "postgresql+psycopg://deals:deals@localhost:5432/deals"
    redis_url: str = "redis://localhost:6379/0"

    # --- Auth ---
    # DEV DEFAULT ONLY. Override JWT_SECRET in .env / cloud secrets in production;
    # anyone with this value can mint valid tokens.
    jwt_secret: str = "dev-insecure-change-me-0123456789abcdef"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # --- Pipeline (Phase 2) ---
    mock_store_url: str = "http://localhost:9000"
    # How often Beat enqueues a refresh sweep. Short for the demo; we'd raise
    # this (and stagger per-product) in production to respect source rate limits.
    refresh_interval_seconds: int = 30

    # --- Real sources (Phase 3) ---
    # These stay None until you register for keys. The source registry only
    # activates a source when its credentials are present, so the stack runs
    # fine without them.
    ebay_env: str = "production"  # "production" or "sandbox"
    ebay_client_id: str | None = None
    ebay_client_secret: str | None = None
    bestbuy_api_key: str | None = None

    # --- Reliability + caching (Phase 3) ---
    # Max calls/sec we allow ourselves to make to each source (rate limiting).
    source_rate_limit_per_sec: int = 5
    # How long one worker "owns" an offer's fetch, so two workers don't double
    # it (idempotency lock).
    fetch_lock_ttl_seconds: int = 10
    # Cache-aside TTL for the computed best deal of a product.
    best_deal_cache_ttl_seconds: int = 60
    # Window for the "lowest price in the last N days" comparison.
    price_history_window_days: int = 30
    # Base seconds for exponential backoff between fetch retries.
    fetch_retry_base_seconds: int = 2


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so the env is parsed once per process."""
    return Settings()
