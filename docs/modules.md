# Module map

[Guide index](README.md) · [Architecture](architecture.md)

Paths below are relative to `core/src/realestate/`. Each implementation module is
listed once. Package `__init__.py` files are empty markers with no runtime logic;
the same applies to `tests/__init__.py`.

## Entrypoints and configuration

| Module | Owns |
|---|---|
| [bootstrap.py](../core/src/realestate/bootstrap.py) | `Container`: lazy dependency graph, DB lifecycle, scheduled-job registration, teardown |
| [main.py](../core/src/realestate/main.py) | `create_app()`: FastAPI factory, lifespan, routes, optional API scheduler |
| [cli.py](../core/src/realestate/cli.py) | Argument parsing and `sources`, `scrape`, `parse`, `search`, `crawl`, `archive`, `rules` service calls |
| [worker.py](../core/src/realestate/worker.py) | Standalone scheduler loop and SIGINT/SIGTERM shutdown |
| [config/paths.py](../core/src/realestate/config/paths.py) | `CORE_DIR`, `etc/`, `var/`, and resolution of relative configured paths |
| [config/settings.py](../core/src/realestate/config/settings.py) | Typed settings, environment/TOML precedence, per-source defaults |
| [config/tortoise.py](../core/src/realestate/config/tortoise.py) | Shared model registration, DB connection/timezone config, Aerich entrypoint |

## Domain

Domain values and ports are framework-free; start here to understand contracts.

| Module | Owns |
|---|---|
| [domain/models.py](../core/src/realestate/domain/models.py) | Price/location, draft/listing, payload/document, blob reference, run, source descriptor, fetch context with mutable progress counters, page, upsert counts |
| [domain/enums.py](../core/src/realestate/domain/enums.py) | Persisted/shared vocabulary for listing, property, price, document/run status, triggers, sorting, logging |
| [domain/exceptions.py](../core/src/realestate/domain/exceptions.py) | Typed failures without HTTP status knowledge |
| [domain/query.py](../core/src/realestate/domain/query.py) | `ListingQuery`, `GeoFilter`, maximum page size |
| [domain/hashing.py](../core/src/realestate/domain/hashing.py) | Stable business-field fingerprint and SHA-256 change detection |
| [domain/blob_keys.py](../core/src/realestate/domain/blob_keys.py) | Source/date/UUID archive naming, format extensions, and the backend-independent key policy (`validate_blob_key`) |
| [domain/ports/data_source.py](../core/src/realestate/domain/ports/data_source.py) | Collection/parsing contract and source health/cleanup hooks |
| [domain/ports/source_registry.py](../core/src/realestate/domain/ports/source_registry.py) | Source lookup, creation, descriptors, enabled keys |
| [domain/ports/repositories.py](../core/src/realestate/domain/ports/repositories.py) | Listing search/upserts, raw-document lifecycle, scrape-run history interfaces |
| [domain/ports/blob_provider.py](../core/src/realestate/domain/ports/blob_provider.py) | Blob put/get/stream/delete/locator contract that every backend keeps |
| [domain/ports/log_provider.py](../core/src/realestate/domain/ports/log_provider.py) | Structured log binding, severity helpers, exception tracebacks |
| [domain/archive.py](../core/src/realestate/domain/archive.py) | Captures, frontier entries, discovered links, archived documents; CDX-compatible `surt_key` and `url_shape` clustering |
| [domain/rules.py](../core/src/realestate/domain/rules.py) | Rule graphs (states, guarded edges, vocab), the priority/fall-through walk, engine outcomes, gaps, LLM decisions |
| [domain/ports/archive.py](../core/src/realestate/domain/ports/archive.py) | Archive index, crawl frontier, enumeration cursor and link-sink contracts |
| [domain/ports/rules.py](../core/src/realestate/domain/ports/rules.py) | Rule-graph versions, gaps, LLM ledger repositories; the rule engine contract |
| [domain/ports/llm.py](../core/src/realestate/domain/ports/llm.py) | Structured-output LLM contract (messages in, one JSON object out) |
| [domain/ports/job_scheduler.py](../core/src/realestate/domain/ports/job_scheduler.py) | Coroutine job contract, interval/cron registration, introspection and lifecycle |

## Application

Services depend on domain ports. DTOs own transport validation and serialization.

| Module | Owns |
|---|---|
| [application/services/ingestion_service.py](../core/src/realestate/application/services/ingestion_service.py) | Run validation, archive-before-parse workflow, pending recovery, replay, error accounting |
| [application/services/listing_query_service.py](../core/src/realestate/application/services/listing_query_service.py) | Search cap, listing/provenance lookup, missing-record errors, counts |
| [application/services/archive_crawl_service.py](../core/src/realestate/application/services/archive_crawl_service.py) | Enumerate CDX into the frontier, optional enumeration gate, route captures, capture selection, attempt budgets across crawl rounds |
| [application/services/rule_induction_service.py](../core/src/realestate/application/services/rule_induction_service.py) | Gap collection (URL shapes, DOM fingerprints), LLM questions, validation against samples, compiling answers into graph versions |
| [application/services/initial_rule_seed_service.py](../core/src/realestate/application/services/initial_rule_seed_service.py) | Explicit initial-graph installation through ports; retains each domain's active operator graph |
| [application/services/frontier_link_sink.py](../core/src/realestate/application/services/frontier_link_sink.py) | Links from recognised pages to frontier evidence |
| [application/rules/proposals.py](../core/src/realestate/application/rules/proposals.py) | Pydantic models validating LLM navigation rules and extraction templates |
| [application/rules/prompts.py](../core/src/realestate/application/rules/prompts.py) | Prompt text, tool schemas and prompt versions (part of the LLM cache key) |
| [application/rules/seeds.py](../core/src/realestate/application/rules/seeds.py) | The single generic seed rule: follow links from recognised pages |
| [application/jobs/scrape_source_job.py](../core/src/realestate/application/jobs/scrape_source_job.py) | Zero-argument scheduled scrape closure with source context and exception logging |
| [application/jobs/parse_pending_job.py](../core/src/realestate/application/jobs/parse_pending_job.py) | Bounded pending-document catch-up closure and logging |
| [application/dto/common.py](../core/src/realestate/application/dto/common.py) | HTTP page envelope and generic acknowledgement model |
| [application/dto/listing.py](../core/src/realestate/application/dto/listing.py) | Listing, price, location responses; explicit domain conversion and rounded distance |
| [application/dto/query.py](../core/src/realestate/application/dto/query.py) | Search parameters, range/geo validation, conversion to domain query |
| [application/dto/source.py](../core/src/realestate/application/dto/source.py) | Source, scrape-run, and raw-document metadata responses |

## Infrastructure: persistence

| Module | Owns |
|---|---|
| [infrastructure/db/models.py](../core/src/realestate/infrastructure/db/models.py) | ORM tables: listings and observations, raw documents, runs, crawl frontier/cursors, rule graphs/nodes/edges/gaps, LLM decisions |
| [infrastructure/db/mappers.py](../core/src/realestate/infrastructure/db/mappers.py) | ORM-to-domain conversion, including price and location reconstruction |
| [infrastructure/db/geo.py](../core/src/realestate/infrastructure/db/geo.py) | Conservative bounding boxes, haversine reference calculation and SQL expression |
| [infrastructure/db/repositories/listing.py](../core/src/realestate/infrastructure/db/repositories/listing.py) | Draft-to-column mapping, hash-aware upserts, filters, geo search, stable sorting, stale marking |
| [infrastructure/db/repositories/raw_document.py](../core/src/realestate/infrastructure/db/repositories/raw_document.py) | Archive metadata, status queries, failure attempts, payload-hash lookup |
| [infrastructure/db/repositories/crawl.py](../core/src/realestate/infrastructure/db/repositories/crawl.py) | Frontier rows (URL-key hash index), evidence merging, cursors |
| [infrastructure/db/repositories/rules.py](../core/src/realestate/infrastructure/db/repositories/rules.py) | Versioned rule graphs (nodes/edges/vocab), gaps, LLM decision ledger |
| [infrastructure/db/repositories/scrape_run.py](../core/src/realestate/infrastructure/db/repositories/scrape_run.py) | Run start/finish, paginated history, latest run per source |
| [infrastructure/blob/local_fs.py](../core/src/realestate/infrastructure/blob/local_fs.py) | Root containment (including symlinks), atomic file replacement, streaming and checksums |
| [infrastructure/blob/s3.py](../core/src/realestate/infrastructure/blob/s3.py) | S3-compatible backend (AWS, MinIO, R2, GCS interop) via aiobotocore: key prefix, default credential chain, lazy client |
| [infrastructure/blob/factory.py](../core/src/realestate/infrastructure/blob/factory.py) | Configured blob backend selection (`local_fs` or `s3`; the S3 module is imported only when selected) |

## Infrastructure: sources, logging, scheduling

| Module | Owns |
|---|---|
| [infrastructure/sources/base.py](../core/src/realestate/infrastructure/sources/base.py) | Shared HTTP client/retries/throttling and unimplemented browser lifecycle hooks |
| [infrastructure/sources/registry.py](../core/src/realestate/infrastructure/sources/registry.py) | Code registrations, factory `SourceContext`, configured descriptors |
| [infrastructure/sources/defaults.py](../core/src/realestate/infrastructure/sources/defaults.py) | Registration and constructor wiring for every shipped source |
| [infrastructure/sources/fixture/source.py](../core/src/realestate/infrastructure/sources/fixture/source.py) | Sorted local JSON collection and tolerant generic `results` parser |
| [infrastructure/sources/dubizzle_eg/source.py](../core/src/realestate/infrastructure/sources/dubizzle_eg/source.py) | Dubizzle query construction, category pagination, response normalization, endpoint healthcheck |
| [infrastructure/sources/wayback/source.py](../core/src/realestate/infrastructure/sources/wayback/source.py) | Generic archive source: frontier-driven fetch, pinned-graph parse and link discovery |
| [infrastructure/sources/olx_eg_wayback/source.py](../core/src/realestate/infrastructure/sources/olx_eg_wayback/source.py) | OLX Egypt 2010-2023 archive: domain and year range only |
| [infrastructure/sources/dubizzle_eg_wayback/source.py](../core/src/realestate/infrastructure/sources/dubizzle_eg_wayback/source.py) | Separate Dubizzle Egypt 2023-2026 archive; packaged reviewed rules alongside the adapter |
| [infrastructure/extraction/seeds.py](../core/src/realestate/infrastructure/extraction/seeds.py) | Packaged JSON loading, proposal/condition validation and compilation into version-one graphs |
| [infrastructure/archive/wayback.py](../core/src/realestate/infrastructure/archive/wayback.py) | Polite Wayback client, validated/throttled redirects, original-byte replay with requested/served provenance and time proof, CDX paging |
| [infrastructure/llm/ollama.py](../core/src/realestate/infrastructure/llm/ollama.py) | Ollama Cloud/self-hosted chat: tool-call structure, JSON fallback, retries, concurrency cap |
| [infrastructure/extraction/engine.py](../core/src/realestate/infrastructure/extraction/engine.py) | `HtmlRuleEngine`: graph walks for routing/extraction, candidate evaluation, condition checks |
| [infrastructure/extraction/conditions.py](../core/src/realestate/infrastructure/extraction/conditions.py) | Condition types (DOM, regex, JSON path, spaCy, fingerprint, evidence, capture range) |
| [infrastructure/extraction/template.py](../core/src/realestate/infrastructure/extraction/template.py) | Template execution: items, field alternatives, vocabulary, drafts and links |
| [infrastructure/extraction/normalisers.py](../core/src/realestate/infrastructure/extraction/normalisers.py) | Deterministic price/currency/area/rooms/date parsing, Arabic and English, capture-anchored dates |
| [infrastructure/extraction/jsondata.py](../core/src/realestate/infrastructure/extraction/jsondata.py) | Embedded JSON discovery and decode diagnostics, paths/array selection, advert-first summaries |
| [infrastructure/extraction/privacy.py](../core/src/realestate/infrastructure/extraction/privacy.py) | Contact/runtime redaction for induction prompt copies; captured data remains independent |
| [infrastructure/extraction/prompt_view.py](../core/src/realestate/infrastructure/extraction/prompt_view.py) | Compact DOM rendering for prompts: collapsed repeats, trimmed attributes, budget |
| [infrastructure/extraction/fingerprint.py](../core/src/realestate/infrastructure/extraction/fingerprint.py) | Structural simhash of page designs and Hamming distance |
| [infrastructure/extraction/document.py](../core/src/realestate/infrastructure/extraction/document.py) | Lazily parsed document views and safe CSS selection |
| [infrastructure/extraction/text.py](../core/src/realestate/infrastructure/extraction/text.py) | Charset decoding, digit/whitespace normalisation, URL resolution and encoding |
| [infrastructure/extraction/nlp.py](../core/src/realestate/infrastructure/extraction/nlp.py) | Optional spaCy blank-pipeline token-pattern matching |
| [infrastructure/sources/zillow/source.py](../core/src/realestate/infrastructure/sources/zillow/source.py) | Explicit unimplemented source placeholder |
| [infrastructure/logging/std_stream.py](../core/src/realestate/infrastructure/logging/std_stream.py) | Text/JSON console logs; warnings and higher to stderr |
| [infrastructure/logging/file.py](../core/src/realestate/infrastructure/logging/file.py) | Queued JSON-lines file writer shared by bound loggers |
| [infrastructure/logging/composite.py](../core/src/realestate/infrastructure/logging/composite.py) | Concurrent fan-out with sink failure isolation |
| [infrastructure/logging/factory.py](../core/src/realestate/infrastructure/logging/factory.py) | Log-level validation and configured sink assembly |
| [infrastructure/scheduling/apscheduler_scheduler.py](../core/src/realestate/infrastructure/scheduling/apscheduler_scheduler.py) | AsyncIOScheduler adapter, interval/cron triggers, coalescing and per-job concurrency |

## API

| Module | Owns |
|---|---|
| [api/deps.py](../core/src/realestate/api/deps.py) | Container-backed FastAPI dependency aliases and constant-time admin-key check |
| [api/errors.py](../core/src/realestate/api/errors.py) | Domain exception to HTTP status/JSON response mapping |
| [api/v1/router.py](../core/src/realestate/api/v1/router.py) | `/api/v1` router assembly |
| [api/v1/health.py](../core/src/realestate/api/v1/health.py) | Liveness and database/blob readiness checks |
| [api/v1/listings.py](../core/src/realestate/api/v1/listings.py) | Search, single listing, provenance routes |
| [api/v1/sources.py](../core/src/realestate/api/v1/sources.py) | Source metadata and authenticated background-run trigger |
| [api/v1/runs.py](../core/src/realestate/api/v1/runs.py) | Run listing and lookup routes |

## Tests and supporting files

| File | Coverage or purpose |
|---|---|
| [tests/conftest.py](../core/tests/conftest.py) | Domain builders, in-memory repositories, log capture, optional database lifecycle |
| [test_api.py](../core/tests/test_api.py) | HTTP filters, pagination, lookup, auth and source/run behavior using fakes |
| [test_query_params.py](../core/tests/test_query_params.py) | Validation and domain query conversion |
| [test_ingestion_service.py](../core/tests/test_ingestion_service.py) | Archive/parse order, run outcomes, resource cleanup, replay |
| [test_fixture_source.py](../core/tests/test_fixture_source.py) | Generic fixture collection, normalization, malformed input and determinism |
| [test_dubizzle_eg_source.py](../core/tests/test_dubizzle_eg_source.py) | Dubizzle mapping, pagination/caps, mocked requests and health checks |
| [test_repositories_integration.py](../core/tests/test_repositories_integration.py) | Real DB upserts/timestamps, geo filtering/counts, paging and document/run lifecycle |
| [test_geo.py](../core/tests/test_geo.py) | Distance calculations and bounding-box inclusion, poles and antimeridian |
| [test_hashing.py](../core/tests/test_hashing.py) | Deterministic hashes and meaningful field changes |
| [test_blob_provider.py](../core/tests/test_blob_provider.py) | One contract suite run against local FS and S3 (moto server): round trips, streaming, not-found, key safety, overwrite; plus backend specifics, factory, key naming |
| [test_logging.py](../core/tests/test_logging.py) | Routing, levels/context, shared file writer, sink isolation, tracebacks |
| [archive_fakes.py](../core/tests/archive_fakes.py) | In-memory archive/rule repositories, scripted LLM, fixture index and loaders |
| [test_archive_domain.py](../core/tests/test_archive_domain.py) | SURT keys vs live CDX, URL shapes, graph walks, versions, seed graph |
| [test_extraction_engine.py](../core/tests/test_extraction_engine.py) | Normalisers; templates for every archived OLX generation; fall-through; conditions; fingerprints; prompt view |
| [test_extraction_jsondata.py](../core/tests/test_extraction_jsondata.py) | Typed array selection, malformed-state diagnostics, advert-prioritized summaries and prompt redaction |
| [test_wayback_and_llm_clients.py](../core/tests/test_wayback_and_llm_clients.py) | Wayback 429/404/replay/CDX paging, archive source pinning, Ollama tools/fallback/retries |
| [test_wayback_replay.py](../core/tests/test_wayback_replay.py) | Redirect confinement before I/O, nearest-capture provenance/time proof, throttled hops and served-year scope |
| [test_rule_induction_and_crawl.py](../core/tests/test_rule_induction_and_crawl.py) | Navigation and template induction, repair feedback, cache reuse, end-to-end crawl with evidence |
| [test_archive_pilot_limits.py](../core/tests/test_archive_pilot_limits.py) | Failed-attempt budgets across rounds/batches, zero limits, enumeration completion and resume, source-scoped cursors |
| [test_archive_repositories_integration.py](../core/tests/test_archive_repositories_integration.py) | Real DB frontier, graphs, gaps, ledger; observation-ordered upserts |
| [fixtures/wayback_olx_eg/](../core/tests/fixtures/wayback_olx_eg/) | Gzipped real captures (2011-2023) and hand-written reference templates |
| [Initial migration](../core/migrations/models/0_20260918095340_init.py) | Aerich initial schema and reverse migration |
| [Historical sources migration](../core/migrations/models/1_20261002152156_historical_sources.py) | Frontier, cursors, rule graphs, gaps, LLM ledger, observations, observation timestamps |
| [pyproject.toml](../core/pyproject.toml) | Package/dependencies, console script, test/lint/type/migration configuration |
| [settings.example.toml](../core/etc/settings.example.toml) | Copyable application configuration |
| [scripts/](../core/scripts/) | Local API, worker, migration and Compose helpers; [usage](operations.md) |
| [docker-compose.yml](../docker-compose.yml) | Local PostgreSQL container, healthcheck and persistent volume |
| [.env.example](../.env.example) | Compose environment template |
| [var/fixtures/](../core/var/fixtures/) | Cairo rental/sale and North America generic samples; Dubizzle response sample |
| [var/blob/](../core/var/blob/), [var/log/](../core/var/log/) | Runtime archive/log directories; only placeholder files are versioned |

When adding a module, add its owner/purpose here and link the relevant behavioral
guide. Keep function signatures and field-level details in the source docstrings.
