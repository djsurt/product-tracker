# Internet-Scale Price Discovery

## Product direction

Deal Hunter should answer one concrete question:

> Given this exact product, where can I buy it right now, and is the price worth acting on?

The current application is a multi-source price tracker. It searches a fixed set of configured adapters, stores the returned offers, and refreshes those known offers later. That is useful infrastructure, but it does not feel like an internet-wide shopping product because it has no broad discovery layer and no reliable product-identity layer.

The target experience is:

> Paste any product. Deal Hunter identifies the exact model, scans shopping and web indexes, verifies the strongest merchant pages, and shows the current offers with evidence.

The goal is not to crawl every page on the internet. The goal is to discover a broad candidate set, verify the candidates that matter, and be transparent about coverage and freshness.

## Why the current architecture tops out

The current `PriceSource` contract combines discovery and refresh:

```text
search(query) -> NormalizedOffer[]
fetch(source_product_id) -> NormalizedOffer
```

That contract assumes every source can search a free-text query and return trustworthy offers. In practice:

- the registry contains a small, fixed set of retailer adapters;
- the generic webpage source only accepts a URL and deliberately returns no search results;
- the product being watched is represented mainly by `title` and `query`;
- an adapter result is stored as an offer before the system proves that it is the same model or variant;
- price, seller, shipping, condition, and variant information are not consistently represented;
- every tracked product independently performs discovery, so identical products are repeatedly searched.

Adding more adapters to this design increases result count, but also increases duplicate listings, wrong variants, stale pages, and maintenance work. Discovery, identity resolution, page extraction, and refresh need to be separate stages.

## Target pipeline

```text
User URL or product name
        |
        v
Resolve product identity
  brand / model / GTIN / MPN / variant
        |
        v
Discover candidate merchants
  shopping index + general web search + direct retailer APIs
        |
        v
Fetch and extract candidate pages
  JSON-LD -> retailer API -> HTML -> managed browser/scraper
        |
        v
Match exact product and variant
  identifiers first, attributes second, title similarity last
        |
        v
Persist verified offers and observations
        |
        v
Refresh known offers frequently; rediscover merchants periodically
```

## Product identity

Introduce a canonical product entity. A product should be described by structured identity fields rather than only a search string:

- brand;
- model and model number;
- GTIN, UPC, EAN, or ISBN when available;
- manufacturer part number (MPN);
- category;
- required variant attributes such as size, color, capacity, storage, generation, and pack count;
- condition, such as new, used, refurbished, or open box.

When a user pastes a product URL, first extract structured product data from JSON-LD, OpenGraph, microdata, and visible page content. Product pages commonly publish `Product` data with identifiers such as GTIN and MPN. These identifiers should be treated as the strongest matching evidence.

Matching should be deterministic where possible:

1. exact GTIN match;
2. exact brand plus MPN match;
3. exact brand, model, and required variant attributes;
4. high-confidence normalized title match;
5. otherwise exclude the result or ask the user to confirm it.

An LLM may help extract attributes from messy titles, but it should not be the only authority for deciding that two listings are the same product.

## Discovery providers

Use several discovery paths with different strengths.

### Shopping search

Use a shopping SERP provider for broad, structured candidate discovery. DataForSEO's Google Shopping product endpoint returns product titles, prices, rankings, merchant domains, ratings, and location-specific results. SerpApi provides a similar Google Shopping results interface.

Shopping results should produce candidate URLs and merchant metadata. They should not be treated as verified offers until the product page is fetched and matched.

### General web search

Use a web index for independent retailers and niche stores that do not appear in shopping feeds. Brave Search API provides web results from an independent index and supports country and language targeting.

Generate a small number of precise queries from the resolved identity, for example:

```text
"<brand> <model>" price
"<mpn>" buy
"<gtin>"
"<brand> <model>" in stock
```

Domain-specific queries can supplement this for the highest-value retailers.

### Direct APIs

Keep direct retailer APIs for sources that provide reliable identity and price data. eBay's Browse API supports keyword and GTIN search. Best Buy exposes a large product catalog with pricing and availability.

Direct integrations are especially useful for frequent refreshes because they avoid repeated search-engine queries.

### Page extraction

Use a layered extractor:

1. existing JSON-LD, OpenGraph, and microdata extraction;
2. direct retailer API;
3. plain HTTP fetch for server-rendered pages;
4. managed browser or ecommerce scraping provider for JavaScript-heavy or protected pages.

A managed provider can be appropriate for initial coverage. Bright Data, for example, maintains ecommerce scrapers that return structured product, price, availability, seller, and review data for several major retailers. The application should still store the extraction method and freshness so the user can tell what was verified.

Do not guess a price when extraction fails. Store the failure reason and keep the candidate out of the trusted offer list.

## Data model direction

The current tracked-product and offer tables should evolve toward shared products and richer offers.

### Product

```text
id
brand
name
model
gtin
mpn
category
variant_attributes (JSON)
identity_confidence
```

### User watch

```text
user_id
product_id
target_price
is_active
```

### Offer

```text
product_id
merchant
seller
retailer_sku
url
price
shipping_price
estimated_tax
total_price
currency
condition
availability
variant_attributes (JSON)
match_confidence
discovery_provider
extraction_method
last_success_at
last_failure_at
```

### Supporting records

Add records for discovery runs and price observations. A discovery run should retain its provider, query, location, timestamp, candidate count, verified count, and failure summary. This supports a useful coverage statement such as:

> Scanned 34 stores; 17 exact matches; 12 prices verified in the last two hours; 5 excluded as wrong variant or stale.

Most importantly, make products shareable across users. If many users watch the same model, the system should discover and refresh the product once, then reuse its observations for each user's watch.

## Refresh strategy

Wide discovery should happen less often than offer refresh:

- discover broadly when a product is first added;
- rediscover daily or weekly to find new merchants;
- refresh the strongest offers every 1–4 hours;
- refresh secondary offers every 12–24 hours;
- trigger an earlier rediscovery when most known offers disappear;
- deduplicate requests by canonical product and merchant.

This keeps the product responsive without paying to search the entire web for every page load.

## User experience

The main product page should lead with coverage and confidence:

```text
Sony WH-1000XM5
Exact model: YY2954

Scanned 34 stores
17 exact matches · 12 verified recently

Best total price: $279.99 at Example Store
Lowest verified price in 90 days: $269.99
Verdict: Wait — this is 8% above the usual deal price
```

Each result should show the merchant, seller, condition, total price, last checked time, and why it was accepted or excluded. This is more valuable than a large number of loosely matched cards.

## Recommended scope

Start with electronics and appliances. They have relatively strong model numbers, GTINs, and MPNs, which makes exact matching practical. Apparel, bundles, used marketplaces, and products with many regional variants should come later because their identity problem is much harder.

For the first useful version, support:

- product URL ingestion;
- identity extraction from the URL;
- one shopping discovery provider;
- one general web search provider;
- direct eBay and Best Buy searches;
- JSON-LD extraction for ordinary retailer pages;
- an exact-match filter;
- shared product records;
- coverage and freshness display.

The 3D preview, screenshot identification, marketplace browsing, and visible MCP setup should be removed from the primary workflow while this is built. They do not improve discovery coverage or confidence.

## Evaluation criteria

Create a test set of approximately 30 real products across the supported categories. Track:

- exact-product precision;
- number of merchants discovered;
- percentage of offers with a recent verified price;
- wrong-variant rate;
- discovery latency;
- extraction failure rate;
- cost per product per month.

Prioritize matching precision over raw merchant count. One incorrect “best deal” damages trust more than several missing stores.

## Staged implementation

### Stage 1: prove broad discovery

Add a shopping discovery provider and a general web search provider. Keep the existing source interface temporarily, but write discovered candidates to a separate table instead of immediately treating them as offers.

### Stage 2: resolve identity

Add structured identity fields, extract identifiers from pasted URLs, and implement exact-match scoring. Add a review state for ambiguous products rather than silently accepting them.

### Stage 3: verify pages

Run candidates through the layered extraction service. Persist extraction method, match evidence, seller, condition, shipping, and freshness.

### Stage 4: share and refresh

Split `Product` from `UserWatch`, deduplicate discovery and refresh work, and introduce scheduled rediscovery separate from price refresh.

### Stage 5: improve the decision

Use the verified history to show a buy/wait recommendation, total-price comparison, historical percentile, and confidence. Only after this foundation is reliable should additional category coverage be added.

