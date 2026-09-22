# Development and code practices

[Guide index](README.md) · [Module map](modules.md)

## Local setup

Use Python 3.12+ (declared in [pyproject.toml](../core/pyproject.toml)) and Docker
Compose for PostgreSQL. Scripts expect the environment at `core/venv`.

From the repository root, on a fresh checkout:

```bash
cd core
python3 -m venv venv
./venv/bin/pip install -e '.[dev]'
cp etc/settings.example.toml etc/settings.toml
./scripts/db_up.sh
./scripts/migrate.sh
./venv/bin/python -m realestate.cli scrape --source fixture --max-items 2
./scripts/dev.sh
```

Preserve an existing `settings.toml` when setting up an existing workspace. Match
its database URL to your Compose settings. The two-payload smoke run selects the
Cairo fixtures in the current sorted fixture directory; see the
[mixed fixture format caveat](sources.md). Open `http://127.0.0.1:8000/docs`.

## Practices to preserve

- Use async I/O and constructor injection; wire new implementations in
  `bootstrap.Container` or an existing factory/registry.
- Keep ORM objects inside infrastructure. Return domain dataclasses from
  repositories and Pydantic DTOs from HTTP handlers.
- Keep `fetch()` limited to collection and `parse()` deterministic and offline.
  Preserve metadata needed to interpret archived bytes during replay.
- Use domain enums, `Decimal` for monetary values, and timezone-aware datetimes.
  Keep unknown source fields in `attributes` and mappings in the source adapter.
- Raise domain exceptions for service failures; map HTTP statuses in
  `api/errors.py`. Log contextual fields through `LogProvider.bind()`.
- Annotate functions. Ruff uses a 100-character limit and checks imports, async
  patterns, and common errors; mypy rejects untyped definitions in source code.
- Explain invariants and non-obvious decisions in docstrings/comments. Keep
  workflow and architecture guidance here, linked to its code owner.
- Do not commit local settings, `.env`, virtual environments, blobs, or logs.
  Sanitized test fixtures under `core/var/fixtures/` are versioned.

Do not add `src/__init__.py`: `src/` is the package root. Preserve
`_enable_global_fallback=True` in `Container.init_db()`; API lifespan and requests
run in different tasks and need the Tortoise context to remain accessible.

## Common changes

| Change | Touch together |
|---|---|
| Add a source | Adapter, default registration, example settings, offline fixtures/tests; [source guide](sources.md) |
| Add a filter | Query DTO + validation, domain query, repository filter/sort, API and database tests |
| Add a listing field | Domain draft/entity, ORM model, read/write mappers, DTO, hash policy, source mapping, migration |
| Add a storage/logging backend | Domain port implementation, settings choices, factory wiring, contract tests |
| Add a scheduled action | Application job closure, container registration, service behavior tests |
| Add an endpoint | Versioned router, DTOs/dependencies, domain error mapping, API tests |

For schema changes, from `core/`:

```bash
./venv/bin/aerich migrate --name describe_change
# Review the generated migration before applying it.
./venv/bin/aerich upgrade
```

Commit migrations with model changes. Use `db.generate_schemas` only for tests or
throwaway databases; normal startup relies on Aerich migrations.

## Verification

From `core/`:

```bash
./venv/bin/pytest
./venv/bin/ruff check src tests
./venv/bin/mypy
```

Most tests use fakes, temporary storage, or mocked HTTP. Repository integration
tests require a **dedicated disposable database**: their teardown deletes all rows
in the listing, raw-document, and scrape-run tables.

```bash
REALESTATE_TEST_DB_URL=postgres://realestate:realestate@127.0.0.1:5432/realestate_test \
  ./venv/bin/pytest tests/test_repositories_integration.py
```

Create that separate database first; Compose creates only the configured default
database. Without the variable, integration tests skip. API fakes implement only
part of the real filtering behavior, so database tests matter for query changes.

After a layer refactor, these searches should have no matches (exit code 1):

```bash
rg '^\s*(from|import) realestate\.(infrastructure|api|config|bootstrap)' src/realestate/domain src/realestate/application
rg '^\s*(from|import) (tortoise|fastapi|pydantic)' src/realestate/domain
```

## First steps for an agent or engineer

1. Read repository instructions and `git status --short`; preserve existing work.
2. Read [architecture](architecture.md), then the target module and related tests.
3. Make the smallest coherent change across the relevant boundaries above.
4. Run focused tests, required lint/type checks, and database tests when relevant.
   Report skipped checks or existing failures explicitly; avoid fixed test counts.
5. Review the diff and update the affected guide/module entry. State what changed,
   how it was checked, and any remaining limitation.
