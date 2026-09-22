# Operations and API

[Guide index](README.md) · [Local setup](development.md)

Commands below run from `core/` unless stated otherwise.

## Configuration

Settings precedence is explicit `Settings(...)` arguments, environment, TOML,
then model defaults. The default file is `core/etc/settings.toml`; select another
with `REALESTATE_SETTINGS_FILE` before starting the process. Nested overrides use
`REALESTATE__`, for example `REALESTATE__DB__URL` or
`REALESTATE__SCHEDULER__RUN_IN_API`.

| Group | Controls |
|---|---|
| `app` | Name, environment, debug, admin API key |
| `db` | PostgreSQL URL and optional test schema generation |
| `blob` | Backend (`local_fs`) and archive root |
| `logging` | Level, console format, file sink/path |
| `scheduler` | API scheduling flags, timezone, misfire grace |
| `sources.<key>` | Enabled state, interval/cron, payload cap, adapter parameters |

Relative configured paths resolve against `core/`, independent of the shell's
working directory. The repository `.env` configures Docker Compose; the Python
settings loader does **not** load it as a dotenv file. Changing Compose credentials
or port requires a corresponding application DB URL change.

## Process model

```bash
./scripts/dev.sh      # API with reload; HOST/PORT override bind defaults
./scripts/worker.sh   # standalone scheduler, in a separate terminal
```

Use one scheduler owner. The API schedules only when both `scheduler.enabled`
and `scheduler.run_in_api` are true. The standalone worker starts scheduling
unconditionally; `scheduler.enabled=false` does not disable that entrypoint.

An implemented, enabled source needs `crontab` or `interval_minutes` to receive a
job; cron takes precedence. A 15-minute pending-parser job is registered only if
at least one scrape job exists. APScheduler coalesces missed ticks and allows one
instance per job **within that scheduler**. Multiple workers/API schedulers can
still duplicate runs. HTTP-triggered runs use FastAPI `BackgroundTasks`, separate
from APScheduler, and disappear if the process exits before they complete.

## HTTP surface

All application routes use `/api/v1`. OpenAPI lives at `/openapi.json`, with the
interactive reference at `/docs`.

| Method and path | Result |
|---|---|
| `GET /health` | Process liveness |
| `GET /health/ready` | Database query and blob-writability checks; inspect body for `degraded` (response remains 200) |
| `GET /listings` | Filtered page: `items` and `meta` (`total`, `limit`, `offset`, `has_more`) |
| `GET /listings/{id}` | One normalized listing |
| `GET /listings/{id}/raw` | Provenance metadata and blob locator; no payload bytes |
| `GET /sources`, `GET /sources/{key}` | Registration, enabled/implemented state, listing count, latest run |
| `POST /sources/{key}/runs` | Admin-only trigger; 202 with a run record; optional `max_items` query parameter |
| `GET /runs`, `GET /runs/{id}` | Run history and outcome; list supports source filter and pagination |

Search defaults to active listings, newest listed first, 20 per page (maximum
100). Repeat `source_key`, `property_type`, or `price_type` for multiple values.
Unknown query fields and incoherent ranges return validation errors. Radius
search requires `lat`, `lon`, and `radius_km` together; maximum radius is 500 km.
`sort=distance` requires those same inputs. Use `currency` and `price_type` when
comparing prices: values are not converted across currencies or rental periods.

```bash
curl 'http://127.0.0.1:8000/api/v1/listings?listing_type=RENT&currency=EGP&price_type=PER_MONTH&price_max=20000&sort=price_asc'
curl 'http://127.0.0.1:8000/api/v1/listings?lat=30.0444&lon=31.2357&radius_km=10&sort=distance'
# Uses the key from the local example settings.
curl -X POST -H 'X-Admin-Key: dev-admin-key' \
  'http://127.0.0.1:8000/api/v1/sources/fixture/runs?max_items=2'
```

Poll `/api/v1/runs/{id}` after a trigger; acceptance does not imply success.
Missing/incorrect admin keys (including an unset configured key) return 401.
Unknown resources return 404; unimplemented sources return 501. Domain errors
use `{ "error": "ExceptionName", "detail": "..." }`; FastAPI validation errors
retain their standard `detail` response.

## CLI and recovery

```bash
./venv/bin/python -m realestate.cli sources
./venv/bin/python -m realestate.cli scrape --source fixture --max-items 2
./venv/bin/python -m realestate.cli parse --source fixture --limit 200
./venv/bin/python -m realestate.cli parse --source dubizzle_eg --reparse --since-days 7 --limit 500
./venv/bin/python -m realestate.cli search --listing-type RENT --limit 5
```

Every CLI command initializes a database connection, including `sources`.
`scrape --max-items` counts payloads. Source-level configured `max_items` is passed
by scheduled jobs; manual CLI/HTTP callers must supply their own cap.

Plain `parse` drains pending documents. `--reparse` requires a source and selects
already parsed and failed documents; its limit applies **per status**, oldest
first. Repeating a limited reparse can select the same documents again because
successful documents remain `PARSED`. `--since-days` is a lower fetch-time bound,
not a cursor. Inspect the intended history before replaying it over current data.

For failures, inspect run status, `raw_document.parse_error`, and logs (default
`var/log/app.log`, JSON lines). `attempts` counts failed parses. Fix parser errors
and replay; recover interrupted parsing with plain `parse`. CLI parse output
reports upsert counts, so consult document state/logs for parse failures. A
`PARTIAL` scrape may still exit zero: inspect its status and errors.

## Local database helpers

`scripts/db_up.sh` starts PostgreSQL and waits for health; `db_psql.sh` opens a
SQL shell; `migrate.sh` applies existing migrations (or initializes if none exist).
`db_down.sh` stops Compose; adding `--volumes` also deletes its database volume.
The archive and database must both be retained to preserve replayable provenance.
