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
| `blob` | Backend (`local_fs` or `s3`), archive root, and `[blob.s3]` bucket/prefix/region/endpoint/credentials |
| `logging` | Level, console format, file sink/path |
| `scheduler` | API scheduling flags, timezone, misfire grace |
| `llm` | Rule-induction model: Ollama base URL, API key (or `OLLAMA_API_KEY`), model, concurrency, timeouts |
| `archive` | Captures fetched per URL by page kind, CDX page size, induction batch/repair/attempt limits |
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

## Archive sources

Archive sources (`olx_eg_wayback`, `dubizzle_eg_wayback`) are crawled, not scheduled.
`crawl` runs rounds of
route → induce navigation rules → fetch and parse → induce templates → re-parse,
within explicit budgets, enumerating the CDX index first when the frontier is empty:

```bash
./venv/bin/python -m realestate.cli crawl --source olx_eg_wayback --rounds 3 --max-fetches 60 --max-llm-calls 10
./venv/bin/python -m realestate.cli archive enumerate --source olx_eg_wayback --from-year 2013 --to-year 2013
./venv/bin/python -m realestate.cli archive route --source olx_eg_wayback
./venv/bin/python -m realestate.cli archive status --source olx_eg_wayback
./venv/bin/python -m realestate.cli rules induce --source olx_eg_wayback --domain navigation --max-calls 3
./venv/bin/python -m realestate.cli rules show --source olx_eg_wayback --domain extraction [--json]
./venv/bin/python -m realestate.cli rules gaps --source olx_eg_wayback
./venv/bin/python -m realestate.cli parse --source olx_eg_wayback --unrecognised
```

Install Dubizzle's reviewed initial graphs explicitly before enumeration/routing:

```bash
./venv/bin/python -m realestate.cli rules seed --source dubizzle_eg_wayback
./venv/bin/python -m realestate.cli rules show --source dubizzle_eg_wayback --domain extraction --json
```

`rules seed` validates both packaged graphs before persistence and saves version
1 only for domains without an active graph. Repeating it retains existing graphs,
including operator edits; it never calls the LLM or archive. It currently supports
only `dubizzle_eg_wayback`. Run it before `archive route` or `crawl`, because those
commands can create a generic navigation graph that seeding will then retain.
The packaged extraction scope includes complete category JSON lists, observed
2023 JSON detail and the 2023 English sales-card fallback. Remaining fixture
evidence, mapping audits and replay-provenance hardening are still required
before the [Dubizzle pilot](dubizzle-eg-wayback-plan.md). Updating packaged seeds
does not upgrade an installed graph: repeated seeding preserves its active
version, and successful old parses require explicit replay after a reviewed
graph change.

Enumeration resumes per year (`crawl_cursor`). Routing retries `UNROUTED` captures
each time, so new navigation rules apply to old misses. Documents no template
recognises are `UNRECOGNISED`, not `FAILED`; after induction, `parse --unrecognised`
re-parses them. Without an API key, induction reports "model unavailable" and the
rest of the crawl still runs on existing rules. Every model answer is in
`llm_decision` (tokens, validity, error); `rules gaps` shows what keeps failing.

**Ollama API key.** Read from, in order: `REALESTATE__LLM__API_KEY`, `[llm] api_key`
in `core/etc/settings.toml` (gitignored), then `OLLAMA_API_KEY`. `crawl` and
`rules induce` log `llm configured ... api_key=<where it came from|missing>` at
start, never the key itself.

**Long runs.** `--rounds 0` keeps crawling until `--max-fetches` is spent or a round
makes no progress (nothing queued and no new rules); `--fetches-per-round` sets the
batch (default 50 in that mode). Ctrl-C is safe: the frontier, rule graphs and
archived pages are persisted, an interrupted ingestion run is closed as `FAILED`
("interrupted before completion"), and the next `crawl` first parses payloads the
interrupted run archived but never parsed. Run it detached and watch from elsewhere:

```bash
nohup ./venv/bin/python -m realestate.cli crawl --source olx_eg_wayback \
  --rounds 0 --max-fetches 2000 --max-llm-calls 40 -v > var/log/crawl.out 2>&1 &
tail -f var/log/crawl.out                      # or var/log/app.log (file sink)
./venv/bin/python -m realestate.cli archive status --source olx_eg_wayback
```

**Logs.** INFO shows each round, routing totals, gap clusters, every LLM question
and its outcome (accepted rules with their regex, rejected proposals and why,
template repair attempts), each fetched capture with progress, and parse totals.
`-v` (or `--log-level DEBUG`, or `REALESTATE__LOGGING__LEVEL=DEBUG`) adds every
archive request (status, bytes, ms), CDX pages, LLM prompt size and raw reply,
Ollama mode (tool call / JSON) and per-document parse or unrecognised outcomes.
`--log-json` emits JSON lines. Warnings go to stderr.

## Local database helpers

`scripts/db_up.sh` starts PostgreSQL and waits for health; `db_psql.sh` opens a
SQL shell; `migrate.sh` applies existing migrations (or initializes if none exist).
`db_down.sh` stops Compose; adding `--volumes` also deletes its database volume.
The archive and database must both be retained to preserve replayable provenance.
