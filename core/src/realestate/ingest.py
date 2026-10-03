"""Run a batch of live and archive sources once, from a shell or cron."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from realestate.bootstrap import Container
from realestate.config.paths import VAR_DIR
from realestate.domain.enums import RunStatus, RunTrigger
from realestate.domain.exceptions import ConfigurationError
from realestate.domain.models import FetchContext, ScrapeRun
from realestate.domain.ports.data_source import ArchiveDataSource


def _nonnegative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return number


def _positive(value: str) -> int:
    number = _nonnegative(value)
    if number == 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--source", action="append", help="source key; repeat for a batch")
    selection.add_argument(
        "--all-enabled",
        action="store_true",
        help="enabled live and archive sources (default; excludes fixture)",
    )
    parser.add_argument("--scheduled", action="store_true", help="require enabled sources (cron)")
    parser.add_argument(
        "--dry-run", action="store_true", help="show the plan without DB/network I/O"
    )
    parser.add_argument(
        "--max-items", type=_positive, help="live payload cap; defaults to settings"
    )
    parser.add_argument(
        "--rounds", type=_nonnegative, default=3, help="archive rounds; 0 = until idle"
    )
    parser.add_argument(
        "--max-fetches", type=_nonnegative, default=60, help="archive attempt budget"
    )
    parser.add_argument(
        "--max-llm-calls", type=_nonnegative, default=10, help="archive model budget"
    )
    parser.add_argument("--max-enumeration-pages", type=_positive, help="archive CDX page budget")
    parser.add_argument("--max-link-lookups", type=_nonnegative, help="archive linked-URL budget")
    parser.add_argument("--require-complete-enumeration", action="store_true")
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=VAR_DIR / "lock" / "ingest.lock",
        help="shared local lock file (default: core/var/lock/ingest.lock)",
    )
    return parser


class BatchAlreadyRunning(Exception):
    """Another helper process holds the local batch lock."""


@contextmanager
def batch_lock(path: Path) -> Iterator[None]:
    """Hold an advisory lock for the batch; process exit releases it automatically.

    Keep the file in place: unlinking it can let processes lock different inodes.
    This coordinates helpers sharing this path, not workers or remote hosts.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BatchAlreadyRunning(f"ingestion already running (lock: {path})") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _print_run(run: ScrapeRun) -> bool:
    print(
        f"{run.source_key}: run {run.id} {run.status}; "
        f"{run.documents_fetched} documents, {run.listings_created} created, "
        f"{run.listings_updated} updated, {run.errors} errors",
        flush=True,
    )
    if run.error_message:
        print(f"{run.source_key}: {run.error_message}", file=sys.stderr, flush=True)
    return run.status is RunStatus.SUCCESS and run.errors == 0


async def run(args: argparse.Namespace) -> int:
    container = Container()
    try:
        keys = (
            args.source
            if args.source is not None
            else [key for key in container.registry.enabled_keys() if key != "fixture"]
        )
        keys = list(dict.fromkeys(keys))
        if not keys:
            raise ConfigurationError(
                "no enabled live or archive sources selected; "
                "enable a live or archive source in etc/settings.toml or pass --source KEY"
            )
        # Validate the entire selection before writing anything.
        for key in keys:
            if key == "fixture":
                raise ConfigurationError(
                    "fixture ingestion is excluded; select a live or archive source"
                )
            if not container.registry.has(key):
                raise ConfigurationError(f"unknown source: {key}")
            descriptor = container.registry.descriptor(key)
            if not descriptor.implemented:
                raise ConfigurationError(f"source is not implemented: {key}")
            if args.scheduled and not descriptor.enabled:
                raise ConfigurationError(f"scheduled source is disabled: {key}")

        if not args.dry_run:
            await container.init_db()
        failed = False
        for key in keys:
            try:
                source = container.registry.create(key)
                try:
                    archive = isinstance(source, ArchiveDataSource)
                finally:
                    await source.aclose()
                config = container.settings.source(key)
                if archive:
                    options = dict(
                        rounds=args.rounds,
                        max_fetches=args.max_fetches,
                        max_llm_calls=args.max_llm_calls,
                        max_enumeration_pages=args.max_enumeration_pages,
                        max_link_lookups=args.max_link_lookups,
                        require_complete_enumeration=args.require_complete_enumeration,
                    )
                    print(f"{key}: crawl {options}", flush=True)
                    if args.dry_run:
                        continue
                    report = await container.crawler.crawl(key, **options)
                    for round_ in report.rounds:
                        if round_.run is not None:
                            failed = not _print_run(round_.run) or failed
                        failed = bool(round_.fetch_failures or round_.links.failed) or failed
                        for note in round_.navigation.notes + round_.extraction.notes:
                            print(f"{key}: {note}", flush=True)
                    print(f"{key}: stopped: {report.stopped}", flush=True)
                    if report.incomplete_years:
                        print(f"{key}: incomplete enumeration: {report.incomplete_years}")
                        failed = True
                else:
                    max_items = args.max_items if args.max_items is not None else config.max_items
                    trigger = RunTrigger.SCHEDULED if args.scheduled else RunTrigger.MANUAL
                    print(f"{key}: scrape max_items={max_items} trigger={trigger}", flush=True)
                    if args.dry_run:
                        continue
                    result = await container.ingestion.ingest(
                        key,
                        trigger=trigger,
                        ctx=FetchContext(max_items=max_items, params=dict(config.params)),
                    )
                    failed = not _print_run(result) or failed
            except Exception as exc:
                failed = True
                print(f"{key}: failed: {exc}", file=sys.stderr, flush=True)
                await container.log.exception("batch source failed", exc, source_key=key)
        return 1 if failed else 0
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        await container.aclose()


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.dry_run:
            code = asyncio.run(run(args))
        else:
            with batch_lock(args.lock_file):
                code = asyncio.run(run(args))
    except BatchAlreadyRunning as exc:
        print(str(exc), file=sys.stderr)
        code = 75
    except KeyboardInterrupt:
        print("ingestion interrupted; re-run to resume archived progress", file=sys.stderr)
        code = 130
    except Exception as exc:
        print(f"ingestion failed: {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
