# Repository Guidelines

## Project Structure & Module Organization

- `src/realestate/` contains the FastAPI backend: `domain/`, `application/`, `infrastructure/`, `api/`, and `config/`. `bootstrap.py` wires dependencies.
- `tests/` holds unit/integration tests and archived-page fixtures; `var/fixtures/` provides sample listings.
- `etc/`, `scripts/`, and `migrations/` contain configuration templates, development scripts, and Aerich migrations.
- `../landing/` is a separate SvelteKit/TypeScript site with components in `src/lib/components/`, routes in `src/routes/`, and assets in `static/`.

Read `../docs/development.md` and `../docs/architecture.md` before backend changes.

## Build, Test, and Development Commands

Run backend commands from this directory with Python 3.12+:

```bash
python3 -m venv venv
./venv/bin/python -m pip install -e '.[dev]'
# First setup only; preserve existing local settings:
cp etc/settings.example.toml etc/settings.toml
./scripts/db_up.sh     # Start PostgreSQL via Docker Compose
./scripts/migrate.sh   # Initialize/apply schema migrations
./scripts/dev.sh       # Reloading API at http://127.0.0.1:8000/docs
./scripts/worker.sh    # Background scraping scheduler
./venv/bin/pytest
./venv/bin/ruff check src tests
./venv/bin/mypy
```

For landing changes, run `npm ci`, `npm run dev`, `npm run check`, and `npm run build` from `../landing/`. `npm run format` applies Prettier.

## Coding Style & Naming Conventions

Use four-space Python indentation, typed functions, `snake_case` functions/modules, and `PascalCase` classes. Ruff checks a 100-character line limit; mypy rejects untyped definitions. Follow Prettier and two-space indentation for Svelte/TypeScript.

Keep domain code framework-free and application code independent of infrastructure. Prefer async I/O and constructor injection. `fetch()` collects bytes; `parse()` stays deterministic, offline, and free of LLM calls.

## Testing Guidelines

Use pytest and pytest-asyncio. Name files `test_*.py` and functions `test_<behavior>`. Add regression tests using fakes or sanitized fixtures. No numeric coverage threshold is configured.

Database integration tests skip without `REALESTATE_TEST_DB_URL`; use only a disposable PostgreSQL database because teardown deletes rows. For landing changes, run checks/build and inspect responsive layouts and form behavior.

## Commit & Pull Request Guidelines

History uses `feat:` and `docs:` prefixes alongside imperative subjects. Write concise, action-oriented commits. PRs should explain behavior changes, link relevant issues, report validation and skipped checks, and include screenshots for UI changes. Commit migrations with schema changes and update affected documentation.

## Security & Configuration Tips

Never commit `.env`, local settings, virtual environments, blobs, logs, or landing waitlist records. Keep sanitized fixtures and example configuration versioned.
