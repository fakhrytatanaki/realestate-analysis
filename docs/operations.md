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

## One-shot ingestion and cron

`scripts/ingest.sh` runs a batch once and exits, using `core/venv` and the usual
settings file/environment overrides. It can be called from any working directory.
The database must already be running and migrated; no API or worker is required.

```bash
# With no arguments, crawl/scrape enabled live and IA/Wayback sources.
./scripts/ingest.sh

# Choose one or several sources; repeated keys run only once.
./scripts/ingest.sh --source dubizzle_eg --max-items 2

# Preview enabled live and archive sources without database or network access.
./scripts/ingest.sh --all-enabled --dry-run

# Archive adapters use the crawl workflow; budgets apply per archive source.
./scripts/ingest.sh --source olx_eg_wayback --source dubizzle_eg_wayback \
  --rounds 3 --max-fetches 60 --max-llm-calls 10 --max-enumeration-pages 2
```

Live sources fetch and parse with their configured `max_items` and `params`;
`--max-items` overrides that live payload cap. Archive sources run the existing
crawler with separate fetch/model/enumeration/link budgets. `--max-llm-calls 0`
disables induction calls. Archive runs retain the crawler's `BACKFILL` trigger.
Prepare reviewed archive rules as described below before starting a new archive
source; this helper does not seed them. Use `--require-complete-enumeration` to
hold archive processing until all configured years have been enumerated.

Omitting source selection defaults to `--all-enabled`, which selects enabled,
implemented live and IA/Wayback sources. The helper excludes `fixture`, including
when explicitly requested with `--source fixture`. Enable at least one live or
archive source in `etc/settings.toml` to use the default selection.
An explicit `--source` allows a disabled live or archive implementation for a
manual run. `--scheduled` also requires every explicitly selected source to be
enabled and marks live runs `SCHEDULED`.
The script ignores source schedules because cron supplies the schedule.

For example, create the log directory once, then add this entry with `crontab -e`
(replace `/absolute/path/realestatepy` with your checkout):

```bash
mkdir -p /absolute/path/realestatepy/core/var/log
```

```cron
# Every day at 02:00 in the cron daemon's timezone.
0 2 * * * /absolute/path/realestatepy/core/scripts/ingest.sh --all-enabled --scheduled >> /absolute/path/realestatepy/core/var/log/ingest-cron.log 2>&1
```

Cron does not inherit your interactive shell's exports. Put persistent settings
in `etc/settings.toml`, or set `REALESTATE_SETTINGS_FILE` and required environment
overrides in the crontab. Avoid scheduling the same sources in the worker/API
scheduler at the same time.

Sources run sequentially and source failures do not stop the remaining batch.
Exit codes are `0` for success, `1` for runtime errors, partial/failed runs or
incomplete archive enumeration, `2` for invalid selection/options, `75` when
another helper batch is running, and `130` for Ctrl-C. Per-run counts and archive
stop reasons go to stdout; failure details go to stderr and application logs.

A nonblocking Unix file lock at `core/var/lock/ingest.lock` prevents overlapping
helper batches sharing that path. `--lock-file /path/to/lock` selects another
path. The lock is released on process exit; keep the lock file in place.
This does not coordinate workers, HTTP runs, direct CLI commands or other hosts.
Dry runs skip locking. No cron entry is installed automatically.

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
`scrape --max-items` counts payloads for live/fixture sources and capture attempts
(including failures) for Wayback sources. Source-level configured `max_items` is passed
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

Archive sources (`olx_eg_wayback`, `dubizzle_eg_wayback`) use the crawl workflow;
use the batch helper above to run that workflow from cron instead of assigning
them a worker scrape schedule.
`crawl` runs rounds of
route → induce navigation rules → fetch and parse → induce templates → re-parse,
within explicit budgets. Each crawl first releases captures claimed by a run that
died and continues CDX enumeration of any year in the source's range not yet
complete (up to `enumeration_pages_per_crawl` index pages):

```bash
./venv/bin/python -m realestate.cli crawl --source olx_eg_wayback --rounds 3 --max-fetches 60 --max-llm-calls 10
./venv/bin/python -m realestate.cli archive enumerate --source olx_eg_wayback --from-year 2013 --to-year 2013
./venv/bin/python -m realestate.cli archive route --source olx_eg_wayback
./venv/bin/python -m realestate.cli archive status --source olx_eg_wayback
./venv/bin/python -m realestate.cli rules induce --source olx_eg_wayback --domain navigation --max-calls 3
./venv/bin/python -m realestate.cli rules show --source olx_eg_wayback --domain extraction [--json]
./venv/bin/python -m realestate.cli rules gaps --source olx_eg_wayback
./venv/bin/python -m realestate.cli parse --source olx_eg_wayback --unrecognised
./venv/bin/python -m realestate.cli parse --source olx_eg_wayback --stale
./venv/bin/python -m realestate.cli archive coverage --source olx_eg_wayback
./venv/bin/python -m realestate.cli archive audit --source olx_eg_wayback
./venv/bin/python -m realestate.cli archive explore --source olx_eg_wayback --per-year 3
./venv/bin/python -m realestate.cli archive lookup-links --source olx_eg_wayback --limit 20
./venv/bin/python -m realestate.cli rules gaps --source olx_eg_wayback --reopen-failed
```

Several crawl processes may run at once: claims keep them off each other's
captures, and request spacing to the archive is shared through
`archive_rate_gate`. `crawl --max-link-lookups` bounds exact-URL lookups of
linked pages the enumeration missed (default `link_lookups_per_round` per round).

**Repairing an archive source.** Rule changes are measured offline before they
are applied: `rules seed --dry-run` (or `archive audit --graph-version N`) replays
every archived page and reports identity, completeness and a diff against stored
rows, writing JSON to `var/audit/`. `parse --stale` re-reads every document
parsed by an older graph than the active one. Merged identities and false histories need
`archive rebuild --source ... --yes`: it deletes the source's listings and
observations and re-parses every archived payload in capture order. The archived
payloads are the source of truth, so a rebuild is repeatable; an interrupted one
leaves documents `PENDING` for the next `parse` or `crawl`. See
[Measuring quality](wayback-crawler-state-machine.md#measuring-quality).

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
evidence and mapping audits are still required
before the [Dubizzle pilot](dubizzle-eg-wayback-plan.md). Updating packaged seeds
does not upgrade an installed graph: repeated seeding preserves its active
version, and successful old parses require explicit replay after a reviewed
graph change.

For a bounded pilot, add `--require-complete-enumeration` to `crawl`. It checks
the per-year cursors for every configured source year before parsing pending
documents, routing, induction or capture fetches. If a year is unfinished, the
crawl reports it and exits with status 2. It resumes incomplete years up to
`--max-enumeration-pages`, including when the frontier is nonempty.
Resume `archive enumerate` explicitly until `archive status` reports complete.
Completing enumeration does not replace the pilot's fixture and field-audit gates.

`crawl --max-fetches` bounds selected capture attempts across all rounds,
including failed requests and rejected replay provenance. Each attempt can
involve multiple throttled redirects/retries; the limit is not an HTTP request
count. `--fetches-per-round` uses the same attempt accounting. Crawl output and
logs distinguish attempts, failed captures and successfully archived documents.
Handled fetch failures increment the run's `errors` and produce `PARTIAL` status,
including runs that archive no payloads. Provenance rejections and permanent failures remain held for review; transient
failures return to the queue with backoff. Unattempted rows remain queued.

Configure the source's `params.from_year`/`params.to_year` for the intended crawl
scope as well as passing enumeration flags. Fetch validates the served capture
against the configured years; enumeration flags select CDX rows only. Review
`capture fetched` logs for requested/served URL and timestamp drift. New raw
metadata retains the redirect chain and timestamp proof. Out-of-range captures,
live-site redirects, unexpected original hosts and missing time proof become
failed frontier entries; they produce no observation. The error retains the
out-of-range replay URL and served timestamp for review. Expand the scope
deliberately before retrying those captures; successful fetches alone do not
demonstrate coverage of the requested CDX year. Legacy raw documents cannot gain
served provenance by offline replay when that evidence was never retained.

Enumeration resumes per domain and year (`crawl_cursor`). Routing retries
`UNROUTED` captures each time, so new navigation rules apply to old misses.
`--max-fetches` counts fetch attempts, failures included; transient failures are
retried with a backoff, permanent ones (404/410/403) become `FAILED`. Documents no template
recognises are `UNRECOGNISED`, not `FAILED`; after induction, `parse --unrecognised`
re-parses them. Without an API key, induction reports "model unavailable" and the
rest of the crawl still runs on existing rules. Every model answer is in
`llm_decision` (tokens, validity, error); `rules gaps` shows what keeps failing,
including `NEEDS_HUMAN` gaps (a curated template wins on those pages and
malfunctions, so only a `rules seed` fix helps) and how far each `FAILED` gap has
grown since it failed. `archive status` counts both.

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
