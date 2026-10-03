# Repository Guidelines

## Project Structure & Module Organization

- `core/src/realestate/` contains the FastAPI backend: `domain/`, `application/`, `infrastructure/`, `api/`, and `config/`; `bootstrap.py` wires dependencies.
- `core/tests/` contains tests and archived-page fixtures; `core/var/fixtures/` supplies sample listings.
- `core/etc/`, `core/scripts/`, and `core/migrations/` hold settings templates, development scripts, and Aerich migrations.
- `landing/` is an independent SvelteKit/TypeScript site; components live in `src/lib/components/`, routes in `src/routes/`, and assets in `static/`.
- Read `docs/development.md` and `docs/architecture.md` before backend changes.

## Build, Test, and Development Commands

Backend commands run from `core/` with Python 3.12+:

```bash
python3 -m venv venv
./venv/bin/python -m pip install -e '.[dev]'
# On first setup only; preserve existing settings:
cp etc/settings.example.toml etc/settings.toml
./scripts/db_up.sh          # Start PostgreSQL through Docker Compose
./scripts/migrate.sh        # Initialize/apply schema migrations
./scripts/dev.sh            # Reloading API; http://127.0.0.1:8000/docs
./scripts/worker.sh         # Background scheduler
./venv/bin/pytest
./venv/bin/ruff check src tests
./venv/bin/mypy
```

From `landing/`, use Node.js 22.12+: `npm ci`, `npm run dev`, `npm run check`, and `npm run build`. `npm run preview` previews the build; `npm run format` applies Prettier.

## Coding Style & Naming Conventions

Use four-space Python indentation, typed functions, `snake_case` functions/modules, and `PascalCase` classes. Ruff enforces a 100-character limit; mypy rejects untyped definitions. Follow Prettier for two-space Svelte/TypeScript indentation and PascalCase component filenames.

Keep domain code framework-free and application code independent of infrastructure. Prefer async I/O and constructor injection. `fetch()` collects bytes; `parse()` must remain deterministic, offline, and free of LLM calls.

## Testing Guidelines

Use pytest and pytest-asyncio; name files `test_*.py` and functions `test_<behavior>`. Add regression tests using fakes or fixtures. No numeric coverage threshold is configured.

Database integration tests skip without `REALESTATE_TEST_DB_URL`. Point it only at a disposable PostgreSQL database: teardown deletes rows. For landing changes, run checks/build and inspect responsive layouts and form behavior.

## Commit & Pull Request Guidelines

History commonly uses `feat:` and `docs:` prefixes alongside imperative subjects. Write concise, action-oriented commits. PRs should explain behavior changes, link relevant issues, report validation and skipped checks, and include screenshots for UI changes. Commit migrations with schema changes and update affected documentation.

## Security & Configuration Tips

Never commit `.env`, local settings, virtual environments, blobs, logs, or landing waitlist records. Keep sanitized fixtures and example configuration versioned.
