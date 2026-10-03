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

`WaybackDataSource` is generic: a subclass names a domain and a year range. Nothing
site-specific is hand-written. The crawl frontier (one row per CDX capture) is routed
by a *navigation* graph; fetched pages are parsed by an *extraction* graph. Both are
versioned state machines in Postgres (`rule_graph`, `rule_node`, `rule_edge`): edges
are guarded by conditions (URL regex, CSS, text regex, embedded-JSON paths, spaCy
patterns, DOM fingerprints, link evidence) and terminal states carry the action (a
route decision or an extraction template).

Misses are clustered into gaps (`rule_gap`) and sent to the LLM, which answers with
selectors, regexes and vocabulary -- never values. Answers are validated against
real samples before they become a new graph version, and every answer is kept in
`llm_decision`. `parse()` uses the graph version pinned for the run and never calls
the model, so replays stay deterministic. The only seed rule follows links from
recognised pages, which is how advert URLs with empty slugs get fetched.

Observations carry the capture time (`observed_at`); the listing row always reflects
the newest capture and `listing_observation` keeps every sighting (price history).
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
