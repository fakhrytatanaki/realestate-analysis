"""Command line interface.

Runs the same services the API and scheduler use, which makes it the quickest
way to exercise a new data source end to end:

    python -m realestate.cli sources
    python -m realestate.cli scrape --source fixture
    python -m realestate.cli parse --source fixture --reparse
    python -m realestate.cli search --listing-type RENT --limit 5
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from realestate.bootstrap import Container
from realestate.domain.enums import ListingType, RunTrigger
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
        "--since-days",
        type=int,
        default=None,
        help="with --reparse, only replay payloads fetched in the last N days",
    )

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
                if args.reparse:
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


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
