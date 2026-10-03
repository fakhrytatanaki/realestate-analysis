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
    python -m realestate.cli rules show --source olx_eg_wayback --domain extraction
    python -m realestate.cli rules gaps --source olx_eg_wayback
    python -m realestate.cli parse --source olx_eg_wayback --unrecognised
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

from realestate.bootstrap import Container
from realestate.domain.enums import GapStatus, ListingType, RuleDomain, RunTrigger
from realestate.domain.models import FetchContext
from realestate.domain.query import ListingQuery


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="realestate", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("sources", help="list registered data sources")

    scrape = subparsers.add_parser("scrape", help="fetch and parse one source")
    scrape.add_argument("--source", required=True, help="source key, e.g. fixture")
    scrape.add_argument("--max-items", type=int, default=None, help="cap payloads fetched")

    parse = subparsers.add_parser("parse", help="parse archived payloads")
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
        "--since-days",
        type=int,
        default=None,
        help="with --reparse, only replay payloads fetched in the last N days",
    )

    crawl = subparsers.add_parser(
        "crawl", help="crawl an archive source: route, induce rules, fetch, parse"
    )
    crawl.add_argument("--source", required=True)
    crawl.add_argument("--rounds", type=int, default=3)
    crawl.add_argument("--max-fetches", type=int, default=60, help="captures fetched, all rounds")
    crawl.add_argument("--max-llm-calls", type=int, default=10, help="model calls, all rounds")
    crawl.add_argument(
        "--max-enumeration-pages",
        type=int,
        default=None,
        help="cap CDX pages when the frontier is empty (each holds up to 5000 captures)",
    )

    archive = subparsers.add_parser("archive", help="archive frontier operations")
    archive_commands = archive.add_subparsers(dest="archive_command", required=True)
    enumerate_ = archive_commands.add_parser(
        "enumerate", help="copy the CDX index into the frontier"
    )
    enumerate_.add_argument("--source", required=True)
    enumerate_.add_argument("--from-year", type=int, default=None)
    enumerate_.add_argument("--to-year", type=int, default=None)
    enumerate_.add_argument("--max-pages", type=int, default=None)
    route = archive_commands.add_parser("route", help="route discovered captures")
    route.add_argument("--source", required=True)
    status = archive_commands.add_parser("status", help="frontier, graphs, gaps, LLM usage")
    status.add_argument("--source", required=True)

    rules = subparsers.add_parser("rules", help="rule graphs and induction")
    rules_commands = rules.add_subparsers(dest="rules_command", required=True)
    induce = rules_commands.add_parser("induce", help="collect gaps and ask the LLM for rules")
    induce.add_argument("--source", required=True)
    induce.add_argument("--domain", choices=["navigation", "extraction", "all"], default="all")
    induce.add_argument("--max-calls", type=int, default=5)
    show = rules_commands.add_parser("show", help="print a rule graph version")
    show.add_argument("--source", required=True)
    show.add_argument("--domain", choices=["navigation", "extraction"], default="extraction")
    show.add_argument("--version", type=int, default=None)
    show.add_argument("--json", action="store_true", help="dump nodes and edges as JSON")
    gaps = rules_commands.add_parser("gaps", help="list open and failed gaps")
    gaps.add_argument("--source", required=True)
    gaps.add_argument("--domain", choices=["navigation", "extraction"], default=None)

    search = subparsers.add_parser("search", help="query aggregated listings")
    search.add_argument("--listing-type", choices=[t.value for t in ListingType], default=None)
    search.add_argument("--city", default=None)
    search.add_argument("--limit", type=int, default=10)

    return parser


async def run(args: argparse.Namespace) -> int:
    container = Container()
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
                if args.unrecognised:
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
                    outcome = await container.ingestion.parse_pending(
                        args.source, limit=args.limit
                    )
                print(
                    f"created {outcome.created}, updated {outcome.updated}, "
                    f"unchanged {outcome.unchanged}"
                )

            case "crawl":
                report = await container.crawler.crawl(
                    args.source,
                    rounds=args.rounds,
                    max_fetches=args.max_fetches,
                    max_llm_calls=args.max_llm_calls,
                    max_enumeration_pages=args.max_enumeration_pages,
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
                        f"created {created}; "
                        f"templates +{round_.extraction.accepted}; "
                        f"llm calls {round_.navigation.llm_calls + round_.extraction.llm_calls}"
                    )
                    for note in round_.navigation.notes + round_.extraction.notes:
                        print(f"  note: {note}")
                await _print_status(container, args.source)

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
                        routed = await container.crawler.route(args.source)
                        print(
                            f"routed {routed.url_keys} urls: queued {routed.queued}, "
                            f"skipped {routed.skipped}, deferred {routed.deferred}, "
                            f"unrouted {routed.unrouted}"
                        )
                    case "status":
                        await _print_status(container, args.source)

            case "rules":
                match args.rules_command:
                    case "induce":
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
                    case "gaps":
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
                        listing_type=ListingType(args.listing_type)
                        if args.listing_type
                        else None,
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
                    print(f"  [{listing.source_key}] {listing.title} -- {price} "
                          f"-- {listing.location.name}")
    finally:
        await container.aclose()
    return 0


def _domain(name: str | None) -> RuleDomain | None:
    return {"navigation": RuleDomain.NAVIGATION, "extraction": RuleDomain.EXTRACTION}.get(
        name or ""
    )


async def _print_status(container: Container, source_key: str) -> None:
    counts = await container.frontier.counts(source_key)
    print(
        "frontier: "
        + ", ".join(f"{status.value.lower()} {n}" for status, n in counts.items() if n)
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
    totals = await container.llm_decisions.totals()
    print(
        f"llm ledger: {totals['calls']} calls, {totals['valid']} valid, "
        f"{totals['tokens_in']} tokens in, {totals['tokens_out']} out"
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
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
