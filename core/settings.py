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

    # RapidAPI "Real-Time Product Search" (Gmail-friendly signup).
    rapidapi_key: str | None = None
    rapidapi_product_host: str = "real-time-product-search.p.rapidapi.com"
    rapidapi_country: str = "us"

    # --- Scraper (Phase 5) ---
    # Off by default; set SCRAPER_ENABLED=true to add it as a live source. It
    # scrapes HTML, defaulting to our own mock store's /html pages.
    scraper_enabled: bool = False
    scraper_base_url: str | None = None  # falls back to mock_store_url
    ebay_scraper_enabled: bool = False
    ebay_scraper_base_url: str = "https://www.ebay.com"
    shein_scraper_enabled: bool = False
    shein_scraper_base_url: str = "https://us.shein.com"

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

    # --- Notifications + redirect (Phase 4) ---
    # SMTP target for alert emails. Defaults point at MailHog (a dev SMTP catcher
    # that swallows mail and shows it in a web UI), so nothing real is ever sent
    # in development. In compose this is overridden to host "mailhog".
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = False
    email_from: str = "Deal Hunter <deals@dealhunter.local>"
    # Public base URL used to build links (the /go redirect) inside emails.
    app_base_url: str = "http://localhost:8000"
    # Cooldown: don't re-fire the same alert within this window (anti-spam).
    alert_debounce_seconds: int = 3600


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so the env is parsed once per process."""
    return Settings()
