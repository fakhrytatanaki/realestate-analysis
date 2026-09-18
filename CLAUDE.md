# CLAUDE.md

Real-estate classifieds aggregator: background scrapers feed one normalised
dataset served over a filterable REST API. FastAPI · Tortoise ORM · PostgreSQL ·
APScheduler · Python 3.14.

`core/` is the backend. Design rationale lives in `core/docs/architecture.md` —
read it before changing the pipeline or the layering.

## Commands

All from `core/`. There is no poetry/uv; the venv is `core/venv`.

```bash
./venv/bin/pytest                 # 81 tests, no database needed
./venv/bin/ruff check src tests   # must be clean
./venv/bin/mypy                   # config sets packages/mypy_path

./scripts/db_up.sh                # PostgreSQL; needs sudo on this machine
./scripts/migrate.sh              # aerich upgrade (init-db on first run)
./scripts/dev.sh                  # API on :8000, /docs for the schema
./scripts/worker.sh               # scheduler as its own process

python -m realestate.cli scrape --source fixture
python -m realestate.cli parse --source fixture --reparse
```

Integration tests need a separate database and are skipped without it:

```bash
REALESTATE_TEST_DB_URL=postgres://realestate:realestate@127.0.0.1:5432/realestate_test \
  ./venv/bin/pytest          # 96 tests
```

Docker needs `sudo` here — the user is not in the `docker` group.

## Architecture rules

```
domain/          entities, enums, ports (ABCs). Imports NO framework.
application/     services, jobs, pydantic DTOs. Imports domain only.
infrastructure/  Tortoise, httpx, APScheduler, filesystem.
api/             FastAPI routers + DI.
bootstrap.py     the ONLY module that may import every layer.
```

Both of these must return nothing — re-run after any refactor:

```bash
grep -rE "^\s*(from|import) realestate\.infrastructure" src/realestate/application src/realestate/domain
grep -rE "^\s*(from|import) (tortoise|fastapi|pydantic)" src/realestate/domain
```

This is why `IngestionService` depends on the `SourceRegistry` *port* rather
than the concrete `DataSourceRegistry`, and why blob key naming lives in
`domain/blob_keys.py` and not in the filesystem provider.

## Pipeline invariant

`fetch()` produces bytes only; they are archived to the blob store and recorded
as a PENDING `RawDocument`. `parse()` then reads those archived bytes back.

- `fetch()` must never parse.
- `parse()` must never touch the network, and must be deterministic.

Break either and replay (`--reparse`) stops being safe — which is the whole
reason the stages are split.

## Gotchas (each one cost real debugging time)

- **Tortoise 1.x state is a contextvar.** An ASGI server runs the lifespan in a
  different task from request handlers, so `Container.init_db` passes
  `_enable_global_fallback=True`. Remove it and every request-time query dies
  with "No TortoiseContext is currently active". `close_connections` clears it,
  so repeated init/teardown (tests) stays clean.
- **Never add `src/__init__.py`.** `src/` is the package root, not a package;
  adding it makes mypy fail with "Source file found twice under different
  module names" and shadows the installed `realestate` package.
- **A queryset `.update()` does not fire `auto_now`.** The unchanged-listing
  path in `TortoiseListingRepository.upsert_many` relies on this to move
  `last_seen_at` without touching `updated_at`, so `updated_at` keeps meaning
  "when the advert really changed". Do not switch it to `.save()`.
- **RawSQL annotations land in `WHERE`, not `HAVING`** (verified on Tortoise
  1.1), which is what makes the haversine radius filter's counts and paging
  correct. Re-check if Tortoise is upgraded.
- **Bounding boxes must round outward** (`_floor`/`_ceil` in
  `infrastructure/db/geo.py`). The box is an indexed prefilter, so anything it
  wrongly excludes can never be recovered by the exact distance check.
- **Blob keys must be canonical and root-relative.** `LocalFsBlobProvider`
  rejects `..`, absolute paths and `a//b`, since external ids reach these keys.

## Adding a data source

1. Subclass `HttpDataSource` (shared client, rate limit, retry, UA rotation) or
   `BrowserDataSource` (camoufox/Playwright) in
   `infrastructure/sources/<key>/source.py`.
2. Register it in `infrastructure/sources/defaults.py`; drop
   `implemented=False` once it works.
3. Configure it under `[sources.<key>]` in `etc/settings.toml`; `params` arrives
   as `ctx.params`.

`FixtureDataSource` is the worked example: vocabulary maps, per-item tolerant
parsing that skips one bad advert rather than losing the page, deterministic
output. `enabled` governs *scheduling* only — manual runs of a disabled source
are allowed; unimplemented stubs return HTTP 501 regardless.

## Conventions

- Prefer async throughout; constructor injection everywhere, resolved in
  `bootstrap.Container` (lazy `cached_property` singletons).
- Repositories return domain dataclasses, never ORM models; API returns pydantic
  DTOs, never ORM models.
- Add filters in one place: `ListingQueryParams` (`application/dto/query.py`)
  validates and converts to `domain/query.ListingQuery`.
- Never commit `core/etc/settings.toml`, `var/blob/`, `var/log/`, or the venv.
  `var/fixtures/` **is** committed — the fixture source reads it.
- Schema changes need a migration: `./venv/bin/aerich migrate && aerich upgrade`.

## Known gaps (deliberate, documented)

No cross-source deduplication; no currency normalisation (`price_max` compares
raw numbers across EGP and USD); location denormalised onto the listing row;
auth covers only the ingestion trigger endpoint.
