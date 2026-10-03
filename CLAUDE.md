# CLAUDE.md

Real-estate classifieds aggregator: background scrapers feed one normalised
dataset served over a filterable REST API. FastAPI · Tortoise ORM · PostgreSQL ·
APScheduler · Python 3.14.

`core/` is the backend. Design rationale lives in `core/docs/architecture.md` —
read it before changing the pipeline or the layering.

## Commands

All from `core/`. There is no poetry/uv; the venv is `core/venv`.

```bash
./venv/bin/pytest                 # 487 tests, no database needed
./venv/bin/ruff check src tests   # must be clean
./venv/bin/mypy                   # config sets packages/mypy_path

./scripts/db_up.sh                # PostgreSQL; needs sudo on this machine
./scripts/migrate.sh              # aerich upgrade (init-db on first run)
./scripts/dev.sh                  # API on :8000, /docs for the schema
./scripts/worker.sh               # scheduler as its own process

python -m realestate.cli scrape --source fixture
python -m realestate.cli parse --source fixture --reparse
python -m realestate.cli crawl --source olx_eg_wayback --max-fetches 60 --max-llm-calls 10 -v
python -m realestate.cli crawl --source olx_eg_wayback --rounds 0 --max-fetches 2000  # until idle; Ctrl-C safe

python -m realestate.cli archive audit --source olx_eg_wayback      # offline replay + quality report
python -m realestate.cli archive coverage --source olx_eg_wayback   # per year/quarter
python -m realestate.cli rules seed --source olx_eg_wayback --dry-run  # curated templates
python -m realestate.cli parse --source olx_eg_wayback --stale      # apply a new graph version
python -m realestate.cli archive rebuild --source olx_eg_wayback --dry-run  # --yes rewrites rows
python -m realestate.cli archive explore --source olx_eg_wayback --per-year 3  # routing false negatives
python -m realestate.cli archive lookup-links --source olx_eg_wayback      # CDX queries; network
```

`core/venv/bin/pip` has a stale shebang from an older checkout path; use
`./venv/bin/python -m pip`.

Integration tests need a separate database and are skipped without it:

```bash
REALESTATE_TEST_DB_URL=postgres://realestate:realestate@127.0.0.1:5432/realestate_test \
  ./venv/bin/pytest          # 523 tests
```

`main_frontend/` is the signed-in SvelteKit app (auth + price trends); it talks
to the API only from its server (see `main_frontend/README.md`):

```bash
cd main_frontend && npm install && npm run check && npm run build
API_BASE_URL=http://127.0.0.1:8000 npm run dev
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
than the concrete `DataSourceRegistry`, and why blob key naming and the key
policy (`validate_blob_key`) live in `domain/blob_keys.py` and not in a provider.
Blob backends (`local_fs`, `s3`) implement `BlobProvider`; a new one must pass
the shared contract suite in `tests/test_blob_provider.py`.

## Pipeline invariant

`fetch()` produces bytes only; they are archived to the blob store and recorded
as a PENDING `RawDocument`. `parse()` then reads those archived bytes back.

- `fetch()` must never parse.
- `parse()` must never touch the network, and must be deterministic.

Break either and replay (`--reparse`) stops being safe — which is the whole
reason the stages are split.

Archive sources keep this with rule graphs: `parse()` and `discover_links()` use
the extraction-graph version loaded once per source instance and **never call the
LLM** (not even its cache). The LLM only runs in `RuleInductionService`, and its
answers become new graph versions after validation. Keep it that way.

Identity is not induced: a source's `identity_policy()` turns advert URLs into
external ids, and templates must agree with it. Curated templates
(`olx_eg_wayback/templates.json`) are `HUMAN` states installed by `rules seed`
and outrank induced ones. Validate rule changes with `archive audit` (or `rules
seed --dry-run`) before applying them: `PARSED` only means a rule matched.

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
- **Frontier URL keys must match the CDX `urlkey`.** Links found in pages reach
  the frontier through `surt_key`, and `absolute_url` percent-encodes Arabic
  slugs so they match; a mismatch silently loses link evidence.
- **Ollama Cloud has no structured outputs** (the `format` schema parameter is
  unsupported there). The
  adapter forces a tool call and falls back to the first JSON object in the
  reply; validation in the induction service is what actually guarantees shape.
- **Market trends must use UTC end to end.** Buckets are cut with
  `date_trunc(..., 'UTC')`, and the chart uses d3's `scaleUtc`. With
  `scaleTime`, ticks sit at local midnight and, east of UTC, read a year early.
- **Never store raw session tokens.** `AuthService` keeps only their sha256;
  the raw token lives in the SvelteKit httpOnly cookie and nowhere else.
- **A graph upgrade is not a data migration.** New extraction versions reach
  already-`PARSED` documents only through `reparse_stale` (`parse --stale`;
  `crawl` does it after induction). Merged identities need `archive rebuild`.
- **Induction judges a candidate as it will run, on current evidence.** Navigation
  prompts show a gap's *current* misses, and a batch is checked under first-match
  order and for URL-shape reach. Extraction candidates must win inside the active
  graph, and a malfunctioning curated winner sends the gap to `NEEDS_HUMAN`. A
  saved version is crawl progress only if it changed outcomes. Isolated checks on
  stored samples are how 88% of the live navigation rules ended up dead
  (`docs/rule-induction-review.md`).
- **Frontier captures complete on acknowledgement, not on yield.** The Wayback
  source claims rows (`FETCHING`) and `IngestionService` calls
  `source.acknowledge()` after the blob and raw document are stored. Do not move
  `mark_fetched` back into `fetch()`.
- **Archive request spacing is a database row.** `WaybackClient` reserves
  slots from `archive_rate_gate` (`PostgresRateGate`) when given a gate; tests
  pass `LocalRateGate` or none. Do not drop the gate from `defaults.py` or
  `Container.wayback`, or parallel crawls will exceed the archive's limits.
- **Blob keys must be canonical and root-relative.** Every provider calls
  `validate_blob_key`, which rejects `..`, absolute paths and `a//b`, since
  external ids reach these keys. The S3 backend needs the `s3` extra
  (`aiobotocore`); its tests use moto's server and skip without it.

## Adding a data source

1. Subclass `HttpDataSource` (shared client, rate limit, retry, UA rotation) or
   `BrowserDataSource` (camoufox/Playwright) in
   `infrastructure/sources/<key>/source.py`.
2. Register it in `infrastructure/sources/defaults.py`; drop
   `implemented=False` once it works.
3. Configure it under `[sources.<key>]` in `etc/settings.toml`; `params` arrives
   as `ctx.params`.

Historical sources replayed from the Wayback Machine instead subclass
`WaybackDataSource` (domain + year range only); their rules are induced, see
`docs/sources.md`.

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
user sessions guard `/auth/me` and `/markets/*` only (`/listings` stays open),
with no login rate limiting or email verification yet.
