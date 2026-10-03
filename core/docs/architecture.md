# Architecture

## The shape of the problem

Every classifieds portal is different. dubizzle.eg may expose a JSON search API;
Zillow buries its results in an embedded blob behind bot detection and may need
a stealth browser such as camoufox. What they have in common is only the *shape*
of the job: get bytes, turn bytes into listings, store listings.

So the bytes-getting is behind one interface, and everything downstream --
archiving, normalising, querying, scheduling -- is written once.

## Layers

```
        domain/          pure Python: entities, enums, and the ports (ABCs).
          ^              Imports nothing from this project, no frameworks.
          |
    application/         services, jobs, pydantic DTOs. Imports domain only.
          ^
          |
infrastructure/  api/    Tortoise, FastAPI, httpx, APScheduler, the filesystem.
          ^              Imports domain + application.
          |
      bootstrap.py       the composition root -- the only module that may
                         import from every layer at once.
```

The rule is enforceable with grep, and worth re-running after any refactor:

```bash
grep -rE "^\s*(from|import) realestate\.infrastructure" src/realestate/application src/realestate/domain
grep -rE "^\s*(from|import) (tortoise|fastapi|pydantic)" src/realestate/domain
```

Both must come back empty. This is why `IngestionService` depends on the
`SourceRegistry` *port* rather than the concrete `DataSourceRegistry`, and why
blob key naming lives in `domain/blob_keys.py` rather than inside the
filesystem provider: neither is a storage detail.

## The two-stage pipeline

```
 DataSource.fetch(ctx) ──► RawPayload (bytes)
                             │
                             ├─► BlobProvider.put()  ──► var/blob/{source}/{Y}/{m}/{d}/{uuid}.json
                             └─► RawDocument row      ──► status = PENDING
                                          │
 DataSource.parse(payload) ◄──────────────┘  (reads the archived bytes back)
        │
        └─► ListingDraft[] ──► ListingRepository.upsert_many() ──► listing rows
                                          │
                                          └─► RawDocument status = PARSED / FAILED
```

Stage 2 only ever reads from the blob store. That is the whole point: when a
selector breaks — and it will — you fix the parser and replay months of stored
payloads without touching the network:

```bash
python -m realestate.cli parse --source dubizzle_eg --reparse
```

A payload that fails to parse is marked `FAILED` with the traceback, and the
bytes remain in the blob store. Nothing is ever lost to a parser bug.

## Change detection

`(source_key, external_id)` is the idempotency key. On every upsert the draft's
business fields are hashed (`domain/hashing.py`):

- hash differs → the row is updated and `updated_at` moves.
- hash matches → only `last_seen_at` moves, via a queryset `.update()` that
  deliberately does not fire Tortoise's `auto_now`.

So `updated_at` keeps meaning *"when did this advert actually change"*, even
though the scraper re-reads the same adverts every hour.

## Ports and their implementations

| Port (`domain/ports/`) | Shipped implementation | Extension point |
|---|---|---|
| `BlobProvider` | `LocalFsBlobProvider` (`var/blob`), `S3BlobProvider` (any S3-compatible store) | Native GCS/Azure — one class + one branch in `BlobProviderFactory`, passing `tests/test_blob_provider.py` |
| `LogProvider` | `StdStreamLogProvider`, `FileLogProvider`, `CompositeLogProvider` | ship to a log aggregator |
| `DataSource` | `FixtureDataSource`; `HttpDataSource` / `BrowserDataSource` bases | every new portal |
| `SourceRegistry` | `DataSourceRegistry` | — |
| `ListingRepository` etc. | Tortoise repositories | PostGIS, a read replica |
| `JobScheduler` | `ApSchedulerJobScheduler` | a distributed queue (ARQ, Celery) |

## Adding a data source

1. **Pick a base.** `HttpDataSource` gives you a shared `httpx.AsyncClient`, a
   concurrency cap, a minimum delay between requests, retry with exponential
   backoff, and user-agent rotation — call `self.request(...)`.
   `BrowserDataSource` is the starting point for camoufox/Playwright work.

2. **Write the class** in `infrastructure/sources/<key>/source.py`:

   ```python
   class DubizzleEgDataSource(HttpDataSource):
       key = "dubizzle_eg"
       display_name = "Dubizzle Egypt"
       country_code = "EG"

       async def fetch(self, ctx: FetchContext) -> AsyncIterator[RawPayload]:
           for page in range(1, (ctx.page_limit or 10) + 1):
               response = await self.request("GET", f"{BASE}/search?page={page}")
               yield RawPayload(
                   content=response.content,
                   kind=RawDocumentKind.JSON,
                   content_type="application/json",
                   source_url=str(response.url),
                   meta={"page": page},
               )

       async def parse(self, payload: RawPayload) -> Sequence[ListingDraft]:
           ...  # bytes -> ListingDraft[]
   ```

   `fetch` must not parse, and `parse` must not touch the network — otherwise
   replays stop being deterministic.

3. **Register it** in `infrastructure/sources/defaults.py` (drop
   `implemented=False` once it works).

4. **Configure it** in `etc/settings.toml`:

   ```toml
   [sources.dubizzle_eg]
   enabled = true
   interval_minutes = 180
   params = { region = "cairo" }
   ```

   `params` reaches the factory as `ctx.params`.

5. **Try it**: `python -m realestate.cli scrape --source dubizzle_eg`

`enabled` governs *scheduling* only — a manual run of a configured-off source is
allowed, which is how a new adapter gets tried out. A source registered with
`implemented=False` is refused whatever the trigger, with HTTP 501.

`FixtureDataSource` is a complete worked example: vocabulary maps
(`"rent"` → `ListingType.RENT`), tolerant per-item parsing that logs and skips a
malformed advert rather than losing the page, and deterministic output.

## Geo search

No PostGIS. Coordinates are `DECIMAL(9,6)` with a composite `(latitude,
longitude)` index, and a radius search runs in two steps:

1. A bounding box, computed in `infrastructure/db/geo.py`, filters on the
   indexed columns. The longitude delta is scaled by `1/cos(latitude)` because
   meridians converge towards the poles, and the bounds are rounded *outward* —
   the box must always be a superset of the circle, since anything it excludes
   can never be recovered by step 2.
2. An exact haversine expression, annotated with Tortoise's `RawSQL`, applies
   the true radius and powers `sort=distance`. Tortoise inlines annotation
   filters into `WHERE` (not `HAVING`), so counts and paging stay exact.

Both steps live behind `ListingRepository.search`. Moving to PostGIS later means
rewriting that one class.

## Known limitations

- **No cross-source deduplication.** The same flat on two portals is two rows.
  `content_hash`, coordinates, price and area are the inputs a later fuzzy
  matching job would need; a `canonical_listing_id` self-FK is the intended
  landing spot.
- **No currency normalisation.** `price` and `currency` are stored as found, so
  `price_max=20000` compares raw numbers across EGP and USD. A `FxRateProvider`
  port is the natural fix.
- **Denormalised location.** Good for the hot search path; a `Location` table
  with geocoding would be better for hierarchical queries.
- **Auth** covers only the admin trigger endpoint. Read endpoints are open.
