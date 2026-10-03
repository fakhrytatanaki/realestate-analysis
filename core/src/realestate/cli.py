"""Command line interface.

Runs the same services the API and scheduler use, which makes it the quickest
way to exercise a new data source end to end:

    python -m realestate.cli sources
    python -m realestate.cli scrape --source fixture
    python -m realestate.cli parse --source fixture --reparse
    python -m realestate.cli search --listing-type RENT --limit 5

Archive sources (rule graphs + LLM induction):

    python -m realestate.cli crawl --source olx_eg_wayback --max-fetches 60 --max-llm-calls 10
    python -m realestate.cli archive enumerate --source olx_eg_wayback --from-year 2013
    python -m realestate.cli archive route --source olx_eg_wayback
    python -m realestate.cli archive status --source olx_eg_wayback
    python -m realestate.cli rules induce --source olx_eg_wayback --domain navigation --max-calls 3
    python -m realestate.cli rules seed --source dubizzle_eg_wayback
    python -m realestate.cli rules show --source olx_eg_wayback --domain extraction
    python -m realestate.cli rules gaps --source olx_eg_wayback
    python -m realestate.cli parse --source olx_eg_wayback --unrecognised
    python -m realestate.cli parse --source olx_eg_wayback --stale

Data quality (offline, nothing written unless stated):

    python -m realestate.cli archive audit --source olx_eg_wayback
    python -m realestate.cli rules seed --source olx_eg_wayback --dry-run
    python -m realestate.cli rules seed --source olx_eg_wayback --retire tpl.v4.x
    python -m realestate.cli archive rebuild --source olx_eg_wayback --dry-run
    python -m realestate.cli archive rebuild --source olx_eg_wayback --yes   # rewrites rows
    python -m realestate.cli archive explore --source olx_eg_wayback --per-year 3
    python -m realestate.cli archive lookup-links --source olx_eg_wayback --limit 20
    python -m realestate.cli rules gaps --source olx_eg_wayback --reopen-failed

Long crawls: ``crawl --rounds 0`` keeps going until a budget is spent or a round
makes no progress; Ctrl-C stops it safely and re-running resumes. Add ``-v`` for
debug logs (every archive request, LLM prompt size and reply, parse outcome),
``--log-json`` for JSON lines. Logs also go to ``var/log/app.log``.

The Ollama API key is read from ``REALESTATE__LLM__API_KEY``, then ``[llm]
api_key`` in ``etc/settings.toml``, then ``OLLAMA_API_KEY``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from realestate.application.services.archive_audit_service import AuditReport
from realestate.bootstrap import Container
from realestate.config.paths import VAR_DIR, resolve
from realestate.config.settings import Settings, load_settings
from realestate.domain.enums import GapStatus, ListingType, RuleDomain, RunTrigger
from realestate.domain.exceptions import ConfigurationError
from realestate.domain.models import FetchContext
from realestate.domain.ports.data_source import ArchiveDataSource
from realestate.domain.query import ListingQuery


def _logging_options(*, suppress: bool) -> argparse.ArgumentParser:
    """Logging flags, accepted before or after the subcommand."""
    options = argparse.ArgumentParser(
        add_help=False, argument_default=argparse.SUPPRESS if suppress else None
    )
    options.add_argument(
        "-v", "--verbose", action="store_true", help="debug logs (same as --log-level DEBUG)"
    )
    options.add_argument(
        "--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], type=str.upper
    )
    options.add_argument("--log-json", action="store_true", help="log JSON lines to stdout")
    return options


def build_parser() -> argparse.ArgumentParser:
    common = _logging_options(suppress=True)
    parser = argparse.ArgumentParser(
        prog="realestate",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[_logging_options(suppress=False)],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("sources", parents=[common], help="list registered data sources")

    scrape = subparsers.add_parser("scrape", parents=[common], help="fetch and parse one source")
    scrape.add_argument("--source", required=True, help="source key, e.g. fixture")
    scrape.add_argument("--max-items", type=int, default=None, help="cap payloads fetched")

    parse = subparsers.add_parser("parse", parents=[common], help="parse archived payloads")
    parse.add_argument("--source", default=None, help="restrict to one source key")
    parse.add_argument("--limit", type=int, default=200)
    parse.add_argument(
        "--reparse",
        action="store_true",
        help="re-parse already-processed payloads instead of pending ones",
    )
    parse.add_argument(
        "--unrecognised",
        action="store_true",
        help="re-parse payloads no extraction rule recognised (after rule induction)",
    )
    parse.add_argument(
        "--stale",
        action="store_true",
        help="re-parse payloads parsed by an older extraction graph than the active one",
    )
    parse.add_argument(
        "--since-days",
        type=int,
        default=None,
        help="with --reparse, only replay payloads fetched in the last N days",
    )

    crawl = subparsers.add_parser(
        "crawl", parents=[common], help="crawl an archive source: route, induce rules, fetch, parse"
    )
    crawl.add_argument("--source", required=True)
    crawl.add_argument(
        "--rounds", type=int, default=3, help="rounds to run; 0 = until a budget or idle"
    )
    crawl.add_argument(
        "--max-fetches",
        type=int,
        default=60,
        help="capture attempts including failures, all rounds",
    )
    crawl.add_argument(
        "--require-complete-enumeration",
        action="store_true",
        help="hold the crawl until all configured source years have finished CDX enumeration",
    )
    crawl.add_argument(
        "--fetches-per-round",
        type=int,
        default=None,
        help="default: max-fetches / rounds (50 with --rounds 0)",
    )
    crawl.add_argument("--max-llm-calls", type=int, default=10, help="model calls, all rounds")
    crawl.add_argument(
        "--max-enumeration-pages",
        type=int,
        default=None,
        help="cap CDX pages enumerated this crawl across unfinished years (5000 captures each)",
    )
    crawl.add_argument(
        "--max-link-lookups",
        type=int,
        default=None,
        help="exact-URL index lookups of linked pages missing from the frontier, all rounds",
    )

    archive = subparsers.add_parser("archive", parents=[common], help="archive frontier operations")
    archive_commands = archive.add_subparsers(dest="archive_command", required=True)
    enumerate_ = archive_commands.add_parser(
        "enumerate", parents=[common], help="copy the CDX index into the frontier"
    )
    enumerate_.add_argument("--source", required=True)
    enumerate_.add_argument("--from-year", type=int, default=None)
    enumerate_.add_argument("--to-year", type=int, default=None)
    enumerate_.add_argument("--max-pages", type=int, default=None)
    route = archive_commands.add_parser("route", parents=[common], help="route discovered captures")
    route.add_argument("--source", required=True)
    route.add_argument(
        "--reopen-capped",
        action="store_true",
        help="first re-route captures skipped by the per-URL capture cap",
    )
    coverage = archive_commands.add_parser(
        "coverage", parents=[common], help="per year and quarter: enumeration and capture states"
    )
    coverage.add_argument("--source", required=True)
    gold_export = archive_commands.add_parser(
        "gold-export",
        parents=[common],
        help="write unverified gold labels pre-filled from real captures, to check by hand",
    )
    gold_export.add_argument("--source", required=True)
    gold_export.add_argument("--sample", type=int, default=12, help="documents to label")
    explore = archive_commands.add_parser(
        "explore",
        parents=[common],
        help="queue a random sample of skipped/deferred/unrouted captures per year",
    )
    explore.add_argument("--source", required=True)
    explore.add_argument("--per-year", type=int, default=3)
    explore.add_argument("--seed", default="explore", help="change to draw a different sample")
    lookup = archive_commands.add_parser(
        "lookup-links",
        parents=[common],
        help="look up linked pages the frontier has no capture of (one CDX query each)",
    )
    lookup.add_argument("--source", required=True)
    lookup.add_argument("--limit", type=int, default=20)
    status = archive_commands.add_parser(
        "status", parents=[common], help="frontier, graphs, gaps, LLM usage"
    )
    status.add_argument("--source", required=True)
    audit = archive_commands.add_parser(
        "audit", parents=[common], help="replay archived pages offline and measure quality"
    )
    audit.add_argument("--source", required=True)
    audit.add_argument("--graph-version", type=int, default=None, help="default: active")
    audit.add_argument("--out", default=None, help="report path (default var/audit/...)")
    rebuild = archive_commands.add_parser(
        "rebuild",
        parents=[common],
        help="delete a source's listings and re-parse every archived payload",
    )
    rebuild.add_argument("--source", required=True)
    rebuild.add_argument("--dry-run", action="store_true", help="audit and diff only")
    rebuild.add_argument("--yes", action="store_true", help="confirm deleting listings")
    rebuild.add_argument("--out", default=None, help="report path (default var/audit/...)")

    rules = subparsers.add_parser("rules", parents=[common], help="rule graphs and induction")
    rules_commands = rules.add_subparsers(dest="rules_command", required=True)
    induce = rules_commands.add_parser(
        "induce", parents=[common], help="collect gaps and ask the LLM for rules"
    )
    induce.add_argument("--source", required=True)
    induce.add_argument("--domain", choices=["navigation", "extraction", "all"], default="all")
    induce.add_argument("--max-calls", type=int, default=5)
    show = rules_commands.add_parser("show", parents=[common], help="print a rule graph version")
    show.add_argument("--source", required=True)
    show.add_argument("--domain", choices=["navigation", "extraction"], default="extraction")
    show.add_argument("--version", type=int, default=None)
    show.add_argument("--json", action="store_true", help="dump nodes and edges as JSON")
    seed = rules_commands.add_parser(
        "seed", parents=[common], help="install the source's curated extraction templates"
    )
    seed.add_argument("--source", required=True)
    seed.add_argument("--retire", nargs="*", default=[], help="induced template keys to remove")
    seed.add_argument("--dry-run", action="store_true", help="audit the candidate graph only")
    seed.add_argument("--out", default=None, help="audit report path (default var/audit/...)")
    gaps = rules_commands.add_parser("gaps", parents=[common], help="list open and failed gaps")
    gaps.add_argument("--source", required=True)
    gaps.add_argument("--domain", choices=["navigation", "extraction"], default=None)
    gaps.add_argument(
        "--reopen-failed",
        action="store_true",
        help="give FAILED gaps fresh attempts (after changing the model or prompts)",
    )

    search = subparsers.add_parser("search", parents=[common], help="query aggregated listings")
    search.add_argument("--listing-type", choices=[t.value for t in ListingType], default=None)
    search.add_argument("--city", default=None)
    search.add_argument("--limit", type=int, default=10)

    return parser


def _settings_for(args: argparse.Namespace) -> Settings:
    """Settings with the command line's logging flags applied on top."""
    settings = load_settings()
    level = "DEBUG" if args.verbose else args.log_level
    if level:
        settings.logging.level = level
    if args.log_json:
        settings.logging.stdout_json = True
    return settings


async def _announce_llm(container: Container, max_calls: int) -> None:
    """Say which model and key will be used, so a bad setup shows up at once."""
    config = container.settings.llm
    source = container.llm_api_key_source()
    await container.log.info(
        "llm configured",
        model=config.model,
        base_url=config.base_url,
        api_key=source or "missing",
        max_concurrency=config.max_concurrency,
    )
    if max_calls > 0 and source is None and "ollama.com" in config.base_url:
        await container.log.warning(
            "no Ollama API key: rule induction will fail; set OLLAMA_API_KEY or "
            "[llm] api_key in etc/settings.toml (routing and parsing with existing rules "
            "still run)"
        )


async def run(args: argparse.Namespace) -> int:
    container = Container(_settings_for(args))
    await container.init_db()
    try:
        match args.command:
            case "sources":
                for descriptor in container.registry.descriptors():
                    state = (
                        "enabled"
                        if descriptor.enabled
                        else ("disabled" if descriptor.implemented else "not implemented")
                    )
                    print(
                        f"{descriptor.key:<14} {descriptor.display_name:<20} "
                        f"{descriptor.country_code:<4} {state}"
                    )

            case "scrape":
                result = await container.ingestion.ingest(
                    args.source,
                    trigger=RunTrigger.MANUAL,
                    ctx=FetchContext(max_items=args.max_items),
                )
                print(
                    f"run {result.id} {result.status}: "
                    f"{result.documents_fetched} documents, "
                    f"{result.listings_created} created, "
                    f"{result.listings_updated} updated, "
                    f"{result.errors} errors"
                )
                if result.error_message:
                    print(f"error: {result.error_message}")
                    return 1

            case "parse":
                if args.stale:
                    if not args.source:
                        print("--stale requires --source")
                        return 2
                    graph = await container.audit.graph(args.source)
                    outcome = await container.ingestion.reparse_stale(
                        args.source, graph_version=graph.version
                    )
                elif args.unrecognised:
                    if not args.source:
                        print("--unrecognised requires --source")
                        return 2
                    outcome = await container.ingestion.reparse_unrecognised(
                        args.source, limit=args.limit
                    )
                elif args.reparse:
                    if not args.source:
                        print("--reparse requires --source")
                        return 2
                    since = (
                        datetime.now(UTC) - timedelta(days=args.since_days)
                        if args.since_days
                        else None
                    )
                    outcome = await container.ingestion.reparse(
                        args.source, since=since, limit=args.limit
                    )
                else:
                    outcome = await container.ingestion.parse_pending(args.source, limit=args.limit)
                print(
                    f"created {outcome.created}, updated {outcome.updated}, "
                    f"unchanged {outcome.unchanged}"
                )

            case "crawl":
                await _announce_llm(container, args.max_llm_calls)
                report = await container.crawler.crawl(
                    args.source,
                    rounds=args.rounds,
                    max_fetches=args.max_fetches,
                    max_llm_calls=args.max_llm_calls,
                    fetches_per_round=args.fetches_per_round,
                    max_enumeration_pages=args.max_enumeration_pages,
                    max_link_lookups=args.max_link_lookups,
                    require_complete_enumeration=args.require_complete_enumeration,
                )
                if report.enumeration:
                    print(
                        f"enumerated {report.enumeration.added} captures "
                        f"({report.enumeration.pages} index pages)"
                    )
                for round_ in report.rounds:
                    run = round_.run
                    created = (run.listings_created if run else 0) + round_.reparsed_created
                    print(
                        f"round {round_.number}: routed {round_.routed.url_keys} urls "
                        f"(queued {round_.routed.queued}, skipped {round_.routed.skipped}, "
                        f"deferred {round_.routed.deferred}, unrouted {round_.routed.unrouted}); "
                        f"nav rules +{round_.navigation.accepted}; "
                        f"fetched {run.documents_fetched if run else 0}, "
                        f"attempted {round_.fetch_attempts}, failed {round_.fetch_failures}; "
                        f"created {created}; "
                        f"templates +{round_.extraction.accepted}; "
                        f"linked pages found {round_.links.found}/{round_.links.looked_up}; "
                        f"llm calls {round_.navigation.llm_calls + round_.extraction.llm_calls}"
                    )
                    for note in round_.navigation.notes + round_.extraction.notes:
                        print(f"  note: {note}")
                print(f"stopped: {report.stopped}")
                await _print_status(container, args.source)
                if report.incomplete_years:
                    print(f"incomplete enumeration years: {report.incomplete_years}")
                    return 2

            case "archive":
                match args.archive_command:
                    case "enumerate":
                        enumeration = await container.crawler.enumerate(
                            args.source,
                            from_year=args.from_year,
                            to_year=args.to_year,
                            max_pages=args.max_pages,
                        )
                        print(
                            f"added {enumeration.added} captures from {enumeration.pages} pages; "
                            f"years completed: {enumeration.years_completed or 'none'}"
                        )
                    case "route":
                        routed = await container.crawler.route(
                            args.source, reopen_capped=args.reopen_capped
                        )
                        print(
                            f"routed {routed.url_keys} urls: queued {routed.queued}, "
                            f"skipped {routed.skipped}, deferred {routed.deferred}, "
                            f"unrouted {routed.unrouted}"
                        )
                    case "status":
                        await _print_status(container, args.source)
                    case "coverage":
                        await _print_coverage(container, args.source)
                    case "explore":
                        explored = await container.crawler.explore(
                            args.source, per_year=args.per_year, seed=args.seed
                        )
                        print(
                            "queued exploration samples: "
                            + (", ".join(f"{y} {n}" for y, n in explored.queued.items()) or "none")
                            + "; fetch them with `crawl`, then `archive audit` reports how many "
                            "held adverts"
                        )
                    case "lookup-links":
                        found = await container.crawler.resolve_links(args.source, limit=args.limit)
                        print(
                            f"looked up {found.looked_up}: {found.found} archived "
                            f"({found.captures_added} new captures), {found.not_archived} not "
                            f"archived, {found.failed} failed"
                        )
                    case "gold-export":
                        labels = await container.audit.gold_candidates(
                            args.source, sample=args.sample
                        )
                        directory = resolve(container.settings.archive.gold_dir) / args.source
                        directory.mkdir(parents=True, exist_ok=True)
                        for label in labels:
                            name = label["document"]["raw_document_id"]
                            path = directory / f"{name}.json"
                            if path.exists():
                                print(f"  kept existing {path.name}")
                                continue
                            path.write_text(
                                json.dumps(label, ensure_ascii=False, indent=2), encoding="utf-8"
                            )
                            print(f"  wrote {path.name}: {len(label['items'])} items")
                        print(
                            f"{len(labels)} candidate labels in {directory}; check each against "
                            'its page, correct it, and set "verified": true'
                        )
                    case "audit":
                        graph = await container.audit.graph(args.source, args.graph_version)
                        audited = await container.audit.audit(args.source, graph=graph)
                        _print_report(audited, args.out, label="audit")
                    case "rebuild":
                        audited = await container.audit.audit(args.source)
                        _print_report(audited, args.out, label="rebuild-dry-run")
                        if args.dry_run:
                            return 0
                        if not args.yes:
                            print(
                                "rebuild deletes and re-creates this source's listings; "
                                "re-run with --yes to proceed"
                            )
                            return 2
                        before = await container.listings.count(args.source)
                        rebuilt = await container.ingestion.rebuild(args.source)
                        after = await container.listings.count(args.source)
                        print(
                            f"rebuilt: {before} rows before, {after} after "
                            f"(created {rebuilt.created}, updated {rebuilt.updated}, "
                            f"unchanged {rebuilt.unchanged})"
                        )
                        final = await container.audit.audit(args.source)
                        _print_report(final, None, label="rebuild-after")

            case "rules":
                match args.rules_command:
                    case "induce":
                        await _announce_llm(container, args.max_calls)
                        domain = _domain(args.domain)
                        if domain in (None, RuleDomain.NAVIGATION):
                            shapes = await container.induction.collect_navigation_gaps(args.source)
                            print(f"navigation gaps: {shapes} url shapes unrouted")
                        if domain in (None, RuleDomain.EXTRACTION):
                            clusters = await container.induction.collect_extraction_gaps(
                                args.source
                            )
                            print(f"extraction gaps: {clusters} page designs unrecognised")
                        induced = await container.induction.induce(
                            args.source,
                            domain=domain,
                            max_calls=args.max_calls,
                            domain_name=container.crawler.scope(args.source).domain,
                        )
                        print(
                            f"llm calls {induced.llm_calls} (cache hits {induced.cache_hits}); "
                            f"accepted {induced.accepted}, rejected {induced.rejected}; "
                            f"new versions {induced.versions or 'none'}"
                        )
                        for note in induced.notes:
                            print(f"  note: {note}")
                        if induced.versions and domain in (None, RuleDomain.EXTRACTION):
                            print("run `parse --source ... --unrecognised` to apply new templates")
                    case "show":
                        await _print_graph(container, args)
                    case "seed":
                        source = container.registry.create(args.source)
                        try:
                            initial = (
                                isinstance(source, ArchiveDataSource)
                                and source.extraction_seed() is None
                            )
                        finally:
                            await source.aclose()
                        if initial:
                            if args.retire or args.out:
                                raise ConfigurationError(
                                    "initial graph seeding retains active graphs; "
                                    "--retire and --out require curated extraction templates"
                                )
                            seeded = await container.rule_seeds.seed(
                                args.source, dry_run=args.dry_run
                            )
                            seed_label = "would install" if args.dry_run else "installed"
                            print(
                                f"{seed_label}: {', '.join(seeded.installed) or 'none'}; "
                                f"retained active graphs: {', '.join(seeded.retained) or 'none'}"
                            )
                            return 0
                        plan = await container.seeder.plan(args.source, retire=args.retire)
                        print(
                            f"extraction graph v{plan.current.version} -> "
                            f"v{plan.candidate.version}: added {plan.added or 'none'}, "
                            f"replaced {plan.replaced or 'none'}, "
                            f"retired {plan.retired or 'none'}, "
                            f"unchanged {len(plan.unchanged)}"
                        )
                        if not plan.changes:
                            print("nothing to install")
                            return 0
                        audited = await container.audit.audit(args.source, graph=plan.candidate)
                        _print_report(audited, args.out, label="seed-candidate")
                        if args.dry_run:
                            return 0
                        saved = await container.seeder.apply(plan)
                        print(
                            f"saved extraction graph v{saved.version}; run "
                            "`parse --source ... --stale` (or `archive rebuild`) to apply it"
                        )
                    case "gaps":
                        if args.reopen_failed:
                            reopened = await container.rule_gaps.reopen_failed(
                                args.source, _domain(args.domain)
                            )
                            print(f"reopened {reopened} failed gaps")
                        for status_ in (GapStatus.OPEN, GapStatus.FAILED):
                            listed = await container.rule_gaps.list(
                                args.source, _domain(args.domain), status=status_, limit=30
                            )
                            print(f"{status_} gaps: {len(listed)}")
                            for gap in listed:
                                last = gap.last_error
                                error = f"  last error: {last[:160]}" if last else ""
                                print(
                                    f"  [{gap.domain}] {gap.fingerprint} x{gap.occurrences} "
                                    f"attempts={gap.attempts}{error}"
                                )

            case "search":
                page = await container.queries.search(
                    ListingQuery(
                        listing_type=ListingType(args.listing_type) if args.listing_type else None,
                        city=args.city,
                        limit=args.limit,
                    )
                )
                print(f"{page.total} matching listings")
                for listing in page.items:
                    price = (
                        f"{listing.price.amount} {listing.price.currency} "
                        f"({listing.price.price_type})"
                        if listing.price.amount is not None
                        else "on request"
                    )
                    print(
                        f"  [{listing.source_key}] {listing.title} -- {price} "
                        f"-- {listing.location.name}"
                    )
    finally:
        await container.aclose()
    return 0


async def _print_coverage(container: Container, source_key: str) -> None:
    """What exists, what was decided, what was fetched -- per year and quarter.

    Keeps the reasons a period has no data apart: never enumerated, no
    captures indexed, captures not selected, fetch failures, unextractable.
    """
    for year in await container.crawler.coverage(source_key):
        total = sum(sum(statuses.values()) for statuses in year.quarters.values())
        if year.enumeration == "not enumerated":
            print(f"{year.year}: not enumerated")
            continue
        if not total:
            print(f"{year.year}: enumeration {year.enumeration}, no captures indexed")
            continue
        print(f"{year.year}: enumeration {year.enumeration}, {total} captures")
        for quarter, statuses in sorted(year.quarters.items()):
            states = ", ".join(
                f"{status.lower()} {count}" for status, count in sorted(statuses.items())
            )
            print(f"  {quarter}: {states}")


def _print_report(report: AuditReport, out: str | None, *, label: str) -> None:
    for line in report.summary_lines():
        print(line)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    path = (
        resolve(out)
        if out
        else VAR_DIR / "audit" / f"{report.source_key}-{label}-v{report.graph_version}-{stamp}.json"
    )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(f"report: {path}")


def _domain(name: str | None) -> RuleDomain | None:
    return {"navigation": RuleDomain.NAVIGATION, "extraction": RuleDomain.EXTRACTION}.get(
        name or ""
    )


async def _print_status(container: Container, source_key: str) -> None:
    missing = await container.crawler.incomplete_years(source_key)
    print(f"enumeration: incomplete years {missing}" if missing else "enumeration: complete")
    counts = await container.frontier.counts(source_key)
    print(
        "frontier: " + ", ".join(f"{status.value.lower()} {n}" for status, n in counts.items() if n)
    )
    for domain in (RuleDomain.NAVIGATION, RuleDomain.EXTRACTION):
        graph = await container.rule_graphs.active(source_key, domain)
        if graph is None:
            print(f"{domain.value.lower()} graph: none yet")
            continue
        vocab_entries = sum(len(entries) for entries in graph.vocab.values())
        print(
            f"{domain.value.lower()} graph: v{graph.version}, "
            f"{len(graph.terminals())} rules, {vocab_entries} vocab entries"
        )
        gaps = container.rule_gaps
        open_gaps = await gaps.list(source_key, domain, status=GapStatus.OPEN, limit=1000)
        failed = await gaps.list(source_key, domain, status=GapStatus.FAILED, limit=1000)
        print(f"  gaps: {len(open_gaps)} open, {len(failed)} failed")
    totals = await container.llm_decisions.totals(source_key=source_key)
    print(
        f"llm ledger (this source): {totals['calls']} calls, {totals['valid']} valid, "
        f"{totals['tokens_in']} tokens in, {totals['tokens_out']} out"
    )
    overall = await container.llm_decisions.totals()
    unattributed = overall["calls"] - sum(
        [
            (await container.llm_decisions.totals(source_key=d.key))["calls"]
            for d in container.registry.descriptors()
        ]
    )
    if unattributed:
        print(f"  plus {unattributed} older calls recorded without a source")
    requests = await container.link_requests.counts(source_key)
    print(
        "linked pages to look up: "
        + (", ".join(f"{s.value.lower()} {n}" for s, n in requests.items() if n) or "none")
    )
    print(f"listings: {await container.listings.count(source_key)}")


async def _print_graph(container: Container, args: argparse.Namespace) -> None:
    domain = _domain(args.domain) or RuleDomain.EXTRACTION
    graph = (
        await container.rule_graphs.get(args.source, domain, args.version)
        if args.version
        else await container.rule_graphs.active(args.source, domain)
    )
    if graph is None:
        print("no such graph")
        return
    if args.json:
        print(
            json.dumps(
                {
                    "version": graph.version,
                    "vocab": graph.vocab,
                    "nodes": [
                        {"key": n.key, "kind": n.kind, "origin": n.origin, "action": n.action}
                        for n in graph.nodes
                    ],
                    "edges": [
                        {
                            "from": e.from_key,
                            "to": e.to_key,
                            "priority": e.priority,
                            "condition": e.condition,
                        }
                        for e in graph.edges
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    print(
        f"{graph.source_key} {graph.domain} v{graph.version} ({graph.status}) {graph.notes or ''}"
    )
    for edge in graph.outgoing("root"):
        node = graph.node(edge.to_key) if graph.has_node(edge.to_key) else None
        if node is None:
            continue
        action = node.action
        summary = (
            f"{action.get('decision')} {action.get('page_kind') or ''} "
            f"p{action.get('priority', '')}"
            if action.get("decision")
            else f"{action.get('page_kind')} template"
        )
        print(f"  [{edge.priority}] {node.key} ({node.origin}): {summary}")
        print(f"      when {json.dumps(edge.condition, ensure_ascii=False)[:200]}")


def main() -> None:
    args = build_parser().parse_args()
    try:
        code = asyncio.run(run(args))
    except KeyboardInterrupt:
        print(
            "\ninterrupted: progress is saved (frontier, rules, archived pages); "
            "re-run the same command to resume",
            file=sys.stderr,
        )
        code = 130
    raise SystemExit(code)


if __name__ == "__main__":
    main()
