# realestatepy

A classifieds / real-estate data aggregator. Scrapes listings from multiple
portals in the background, archives every raw payload, normalises them into one
dataset, and serves it over a filterable REST API.

FastAPI · Tortoise ORM · PostgreSQL · APScheduler · Python 3.12+

## Layout

```
realestatepy/
├── docker-compose.yml        # local PostgreSQL
└── core/                     # the backend service
    ├── etc/                  # configuration
    ├── var/                  # mutable data: blob/, log/, fixtures/
    ├── scripts/              # db_up.sh, migrate.sh, dev.sh, worker.sh
    ├── migrations/           # aerich migrations
    ├── docs/architecture.md  # design, layering rules, how to add a source
    ├── src/realestate/       # domain / application / infrastructure / api
    └── tests/
```

## Quick start

```bash
cd core
python -m venv venv && ./venv/bin/pip install -e ".[dev]"
cp etc/settings.example.toml etc/settings.toml

./scripts/db_up.sh            # start PostgreSQL (add sudo if not in the docker group)
./scripts/migrate.sh          # create the schema

./venv/bin/python -m realestate.cli scrape --source fixture
./scripts/dev.sh              # http://127.0.0.1:8000/docs
```

The `fixture` source reads `var/fixtures/*.json` and needs no network, so the
whole pipeline is runnable immediately.

## Using it

```bash
# Cheap rentals, cheapest first
curl "localhost:8000/api/v1/listings?listing_type=RENT&price_max=20000&sort=price_asc"

# Everything within 10 km of downtown Cairo, nearest first
curl "localhost:8000/api/v1/listings?lat=30.0444&lon=31.2357&radius_km=10&sort=distance"

# What sources exist, and how each last run went
curl "localhost:8000/api/v1/sources"

# Trigger a run (202 + run id; poll /runs/{id})
curl -X POST -H "X-Admin-Key: dev-admin-key" \
  "localhost:8000/api/v1/sources/fixture/runs"
```

Filters: `q`, `source_key`, `listing_type`, `property_type`, `price_min/max`,
`currency`, `price_type`, `country_code`, `city`, `district`, `location`,
`bedrooms_min/max`, `bathrooms_min`, `area_min/max`, `lat`+`lon`+`radius_km`,
`is_active`, `listed_after/before`, `sort`, `limit`, `offset`. Full schema at
`/docs`.

## CLI

```bash
python -m realestate.cli sources                          # what is registered
python -m realestate.cli scrape --source fixture          # fetch + parse
python -m realestate.cli parse  --source fixture --reparse  # replay stored blobs
python -m realestate.cli search --listing-type RENT --limit 5
```

## Background scraping

```bash
./scripts/worker.sh           # scheduler as its own process
```

Schedules come from `etc/settings.toml`. Set `scheduler.run_in_api = true` to
run them inside the API process instead.

## How it works

Collection is two-stage: payloads are archived to a blob store first, then
parsed into listings. When a portal changes its markup you fix the parser and
replay history — no re-scraping:

```bash
python -m realestate.cli parse --source dubizzle_eg --reparse
```

Adding a portal means implementing one interface (`DataSource.fetch` /
`.parse`) and registering it. `dubizzle_eg` and `zillow` ship as registered
stubs that return HTTP 501 until implemented.

See [`core/docs/architecture.md`](core/docs/architecture.md) for the layering
rules, the geo-search strategy, and a step-by-step guide to adding a source.

## Development

```bash
cd core
./venv/bin/pytest          # 81 tests, no database needed
./venv/bin/ruff check src tests
./venv/bin/mypy
```
