"""Registry of active price sources.

A source is only registered when its credentials are configured. This is what
makes the "wire real APIs but don't block the demo" strategy work: with no keys,
only the mock store is active; add eBay/Best Buy keys to .env and restart, and
they appear automatically. The pipeline code never changes.
"""

from core.settings import get_settings
from sources.base import PriceSource
from sources.bestbuy import BestBuySource
from sources.ebay import EbaySource
from sources.ebay_scraper import EbayScraperSource
from sources.mock import MockStoreSource
from sources.rapidapi import RapidApiProductSource
from sources.scraper import ScraperSource
from sources.shein_scraper import SheinScraperSource


def _build_registry() -> dict[str, PriceSource]:
    s = get_settings()
    sources: list[PriceSource] = [MockStoreSource()]  # always available

    if s.ebay_client_id and s.ebay_client_secret:
        sources.append(EbaySource())
    if s.bestbuy_api_key:
        sources.append(BestBuySource())
    if s.rapidapi_key:
        sources.append(RapidApiProductSource())
    if s.scraper_enabled:
        sources.append(ScraperSource())
    if s.ebay_scraper_enabled:
        sources.append(EbayScraperSource())
    if s.shein_scraper_enabled:
        sources.append(SheinScraperSource())

    return {src.name: src for src in sources}


_SOURCES = _build_registry()


def get_sources() -> list[PriceSource]:
    return list(_SOURCES.values())


def get_source(name: str) -> PriceSource:
    return _SOURCES[name]


def active_source_names() -> list[str]:
    return list(_SOURCES.keys())
