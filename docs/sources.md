# Data sources

[Guide index](README.md) · [Architecture](architecture.md)

## Shipped adapters

Registration in [defaults.py](../core/src/realestate/infrastructure/sources/defaults.py)
defines what exists; `[sources.<key>]` settings define scheduling and constructor
parameters. `enabled=false` still permits manual runs of implemented sources.

| Source | Current behavior | Configuration |
|---|---|---|
| `fixture` | Reads sorted `*.json` files; parses a top-level `results` array | `params.directory`, default `var/fixtures`; enabled hourly in example settings |
| `dubizzle_eg` | HTTP collector and parser for Elasticsearch `_msearch` responses | Implemented; disabled in example settings |
| `zillow` | Registered stub; fetch/parse raise `DataSourceNotImplementedError` | Cannot run; HTTP trigger returns 501 |
| `olx_eg_wayback` | OLX Egypt 2010-2023 from the Wayback Machine; navigation and extraction rules induced by an LLM and stored as rule graphs | `params.domain`, `from_year`, `to_year`, `user_agent`, `min_delay_seconds`, `max_fetches_per_run`; crawl with `cli crawl` |
| `dubizzle_eg_wayback` | Separate Dubizzle Egypt archive, 2023-2026; reviewed JSON-list rules plus observed 2023 JSON detail and sales-card fallback | Disabled by default; same archive parameters; explicitly install rules with `cli rules seed --source dubizzle_eg_wayback` |

The shared fixture directory currently contains both generic `results` fixtures
and `dubizzle_eg_apartments_sale.json` in Dubizzle's format. A full `fixture` run
archives that file but fails to parse it, making the run `PARTIAL`. Use a directory
containing only generic fixtures for a complete clean fixture run. Dubizzle tests
pass its fixture directly to the Dubizzle parser.

## Dubizzle specifics

The adapter posts NDJSON queries to the endpoint configured in its source code;
it archives each category/page response as JSON. Parser mappings cover purpose,
property subtype, rental period, installment terms, location, and extra attributes.
Malformed individual hits are logged and skipped.

Supported constructor `params` are `auth_token`, `categories`, `page_size`, and
`location_external_id`. Keep credential overrides in local configuration. Example:

```toml
[sources.dubizzle_eg]
enabled = false
interval_minutes = 180
params = { categories = ["apartments-duplex-for-sale"], page_size = 20, location_external_id = "0-1" }
```

Defaults are four apartment/villa sale/rent categories, 50 hits per page, and
10 pages **per category**. `FetchContext.page_limit` overrides the page count;
`since` adds a timestamp filter. Neither is currently exposed by the scrape CLI.
`max_items` caps payloads across categories, not individual adverts. Collection
does not inspect responses to stop on empty pages. Offline tests validate request
construction and parsing; they do not establish live endpoint availability.

## Archive sources (rule graphs)

For diagrams and a detailed walkthrough of the implemented OLX crawler, see
[the Wayback crawler state machine guide](wayback-crawler-state-machine.md).

`WaybackDataSource` is generic: a subclass names a domain and a year range, and may
add two fixed, site-specific pieces (see below). The crawl frontier (one row per CDX capture) is routed
by a *navigation* graph; fetched pages are parsed by an *extraction* graph. Both are
versioned state machines in Postgres (`rule_graph`, `rule_node`, `rule_edge`): edges
are guarded by conditions (URL regex, CSS, text regex, embedded-JSON paths, spaCy
patterns, DOM fingerprints, link evidence) and terminal states carry the action (a
route decision or an extraction template).

Misses are clustered into gaps (`rule_gap`) and sent to the LLM, which answers with
selectors, regexes and vocabulary -- never values. Answers are validated against
real samples before they become a new graph version, and every answer is kept in
`llm_decision`. `parse()` uses the graph version pinned for the run and never calls
the model, so replays stay deterministic. The only generic seed rule follows links
from recognised pages, which is how advert URLs with empty slugs get fetched.

Two things a subclass can fix in code instead of leaving to induction:

- `identity_policy()` returns an `IdentityPolicy`: regexes reading the advert id
  from an advert URL, and URL patterns that are never one advert (home, category,
  search). Parsing derives external ids through it, and induction validates with
  it. OLX ids stay bare numbers (`535715253`) across list and detail pages.
- `extraction_seed()` returns curated templates (OLX ships
  [`templates.json`](../core/src/realestate/infrastructure/sources/olx_eg_wayback/templates.json),
  one per known page design). `rules seed` installs them as `HUMAN` states ahead of
  every induced one, optionally retiring faulty induced states; a curated template
  that works always wins.

Dubizzle also ships reviewed source-specific graphs, installed explicitly with
`rules seed` without replacing either domain's active graph.

Wayback collection limits count selected capture attempts, including failures,
rather than only yielded payloads. The source reports attempts and handled
failures through `FetchContext.progress`; ingestion records failures as run
errors and the crawler deducts attempts across rounds. HTTP retries and redirect
hops are part of one selected capture attempt. For a bounded pilot, use the
optional `crawl --require-complete-enumeration` gate; see [operations](operations.md).

Replay follows redirects explicitly. Each request must stay on `web.archive.org`;
each replay must retain `id_` mode and an original host equal to the configured
domain or its `www` variant. OLX additionally permits the requested in-domain
city subdomain, matching its older layouts. Other subdomains and live-site escapes are rejected
before requesting the target. Redirect hops receive the same throttle as initial
requests. A timezone-aware `Memento-Datetime`, normalized to UTC, determines the
served time; otherwise a valid full timestamp in the final replay URL is required.
Short prefixes alone cannot supply an observation time. The header takes precedence
when it differs from the final URL, and `replay_timestamp` retains that URL's time.

New raw metadata keeps `requested_timestamp`, `requested_original_url`,
`requested_replay_url`, `served_timestamp`, `served_original_url`,
`timestamp_source`, `capture_drift_seconds`, `served_url_key` and `redirect_chain`
(all visited replay URLs, including the final one). Existing `timestamp`,
`captured_at`, `original_url`, `replay_url` and `source_url` describe the served
capture, so offline extraction uses its actual URL/time. `url_key`, `digest` and
`cdx_digest` refer to the requested frontier/CDX row; a redirected capture's digest
is not inferred from that row. Earlier raw documents retain their original metadata.
Captures served outside the configured `from_year`/`to_year` range are held for
review as failed frontier entries and do not emit raw payloads or observations.

Dubizzle seed v1 supports complete Arabic/English apartment/duplex category
JSON lists (`state.algolia.content.hits`). It validates each item's property
taxonomy, reads `extraFields.price`, deduplicates numeric external IDs, and uses
the original language path. Missing rental periods remain `UNKNOWN` with their
known amount. Subtypes remain `OTHER` until code meanings are validated; raw
subtype/payment/down-payment fields are retained as attributes. The observed
2023 detail shape (`state.ad.data`) requires matching numeric URL/advert IDs,
selects unique category/location levels and maps valid coordinates. Explicit
daily-period text sets price basis independently of amount; JSON-LD cannot
override the advert's taxonomy or price.

The 2023 English sales-card fallback recovers labelled cards when list state is
missing, malformed or not an array. Its guard excludes every decoded list array,
so HTML cannot override JSON, including rejected taxonomy or empty arrays. A
narrow empty-results rule requires an empty array, zero count, explicit visible
signal and no cards. Unsupported designs and challenges remain gaps. Structured
`archive extraction`/`archive extraction gap` logs report template, counts,
bounded rejection reasons, malformed state, unknown basis and category/purpose
conflicts. The fixture README and [Dubizzle plan](dubizzle-eg-wayback-plan.md)
record the remaining evidence and mapping gates before the bounded pilot.

Observations carry the capture time (`observed_at`); the listing row always reflects
the newest capture and `listing_observation` keeps one row per capture with that
capture's complete normalized `snapshot`, graph version and template (a re-parse
refreshes it). Captures are partial, so detail columns (rooms, area, coordinates,
URL, city...) missing from a newer capture keep their stored value, and an older
capture fills what the row lacks; `attributes._provenance` names the raw document
each merged value came from.

Deterministic engine rules the templates rely on:

- When several templates match a page, a curated one that works wins; otherwise
  the one extracting most adverts and detail values (priority breaks ties), so
  overlapping templates induced at different times cannot shadow each other.
- Sale/rent is read from the type field, category, title, location, then the
  advert's own text, then the page; a text naming both ("for rent / for sale") is
  skipped in favour of the next, and the first match is the fallback. A
  property-type text naming several types ("Houses - Apartments") is skipped too.
- With an identity policy, the advert URL's id wins over the template's, which
  must agree; a whole-page `url` pointing home falls back to the archived page URL.
- A price regex whose capture holds no amount falls back to the whole cell text
  when it carries a currency marker (`ج.م125,000`).
- Cities come from a curated alias table matched on whole names, never by
  splitting on hyphens; a country name is not a city.
- Reviewed templates can opt into `strict_classification` to use only per-item mapped fields.
- A `css` + `regex` field without `index` tries every matching node ("Bedrooms: 3"
  and "Bathrooms: 2" spans sharing one selector); a `title`/`alt` attribute that
  only adds a suffix to the visible text ("... - Cairo") yields the visible text.
- Prices below 100 (5 per night/week/m²) are placeholders and stored as unknown; with
  no price field value, a title price is used only if it carries a currency marker.
  `allow_title_price=false` disables that fallback; `default_rental_price_type`
  controls whether a missing rental period defaults to monthly or stays unknown.
- `price_period` parses explicit period text independently of amount. JSON fields
  and filters may select a unique array member with `select: {path, field, equals}`;
  missing or duplicate levels fail. `identity_url_pattern` requires URL/ID agreement,
  including the original page URL for detail templates. Coordinate pairs must be
  finite and within WGS-84 bounds.
- Induction summaries prioritize advert arrays and singleton detail objects;
  prompt copies redact contacts and runtime fields while retaining captured bytes.
- Induction rejects a field that stays empty on every advert of 2+ list sample pages
  (10+ adverts) or 3+ detail pages, doubly escaped regexes, home-page `url` recipes,
  one id across different adverts, price regexes that drop the amount, and any
  candidate that lowers the exact-match rate on verified gold pages.

See [the plan](historical-sources-plan.md) for the investigation behind this.

## Add or repair an adapter

1. Implement the [DataSource port](../core/src/realestate/domain/ports/data_source.py)
   in `infrastructure/sources/<key>/source.py`. Use `FixtureDataSource` for a simple
   parser example or `HttpDataSource` for HTTP collection.
2. Set stable `key`, `display_name`, and `country_code`. `fetch(ctx)` yields
   `RawPayload`; `parse(payload)` returns a sequence of `ListingDraft`.
3. Archive original bytes with accurate kind/content type, source URL, and any
   metadata needed by the parser. Keep network access and persistence out of parsing.
4. Normalize vocabulary explicitly; use stable external IDs. Reject invalid page
   structures with `ParseError`; log and skip isolated malformed adverts when safe.
5. Register a constructor factory in `defaults.py`, add example settings, and set
   `implemented=True` only once the adapter works. Close clients in `aclose()`.
6. Add sanitized fixtures and offline tests for deterministic parsing, missing
   fields, malformed pages/items, request construction, limits, and replay.
7. Run a bounded scrape against an appropriate environment, inspect run/document
   outcomes, then replay archived payloads after parser changes.

`HttpDataSource.request()` provides a lazy shared httpx client, concurrency/delay
limits, user-agent rotation, and bounded retries for HTTP transport failures and
selected transient statuses. `BrowserDataSource` only defines lifecycle hooks;
it ships without a browser driver implementation.

Factory `SourceContext.params` contains configured parameters. Per-run
`FetchContext.params` is a separate dictionary; each adapter must explicitly use
it if runtime overrides are desired. Existing adapters primarily use constructor
parameters, so adding a setting alone does not implement its behavior.

For recovery commands and replay limits, see [operations](operations.md).
