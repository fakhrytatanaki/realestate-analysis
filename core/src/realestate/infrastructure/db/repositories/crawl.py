"""Tortoise-backed crawl frontier and enumeration cursors."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from realestate.domain.archive import Capture, FrontierEntry
from realestate.domain.enums import CrawlStatus, LinkRel, PageKind
from realestate.domain.ports.archive import CrawlCursorRepository, CrawlFrontierRepository
from realestate.infrastructure.db.models import CrawlCursorModel, CrawlFrontierModel

#: Statuses evidence may pull back into routing; queued/fetched work is left alone.
_REROUTABLE = (CrawlStatus.UNROUTED, CrawlStatus.DEFERRED, CrawlStatus.SKIPPED)


def url_key_hash(url_key: str) -> str:
    return hashlib.sha1(url_key.encode("utf-8")).hexdigest()


def _to_entry(row: CrawlFrontierModel) -> FrontierEntry:
    return FrontierEntry(
        id=row.id,
        source_key=row.source_key,
        url_key=row.url_key,
        timestamp=row.timestamp,
        original_url=row.original_url,
        digest=row.digest,
        status=row.status,
        priority=row.priority,
        page_kind=row.page_kind,
        route_node=row.route_node,
        evidence=row.evidence or {},
        attempts=row.attempts,
        error=row.error,
    )


class TortoiseCrawlFrontierRepository(CrawlFrontierRepository):
    async def add_captures(self, source_key: str, captures: Sequence[Capture]) -> int:
        if not captures:
            return 0
        unique = {(url_key_hash(c.url_key), c.timestamp): c for c in captures}
        existing = {
            (row[0], row[1])
            for row in await CrawlFrontierModel.filter(
                source_key=source_key,
                url_key_hash__in=list({key for key, _ in unique}),
            ).values_list("url_key_hash", "timestamp")
        }
        rows = [
            CrawlFrontierModel(
                source_key=source_key,
                url_key=capture.url_key,
                url_key_hash=key_hash,
                timestamp=capture.timestamp,
                original_url=capture.original_url,
                digest=capture.digest,
            )
            for (key_hash, timestamp), capture in unique.items()
            if (key_hash, timestamp) not in existing
        ]
        if rows:
            await CrawlFrontierModel.bulk_create(rows, batch_size=1000, ignore_conflicts=True)
        return len(rows)

    async def url_keys_with_status(
        self, source_key: str, statuses: Sequence[CrawlStatus], *, limit: int
    ) -> list[str]:
        rows = (
            await CrawlFrontierModel.filter(source_key=source_key, status__in=list(statuses))
            .order_by("id")
            .limit(limit * 4)
            .values_list("url_key", flat=True)
        )
        seen: dict[str, None] = {}
        for key in rows:
            seen.setdefault(str(key), None)
            if len(seen) >= limit:
                break
        return list(seen)

    async def captures_for(self, source_key: str, url_keys: Sequence[str]) -> list[FrontierEntry]:
        if not url_keys:
            return []
        rows = await CrawlFrontierModel.filter(
            source_key=source_key, url_key_hash__in=[url_key_hash(key) for key in url_keys]
        ).order_by("timestamp")
        return [_to_entry(row) for row in rows]

    async def set_route(
        self,
        entry_ids: Sequence[int],
        *,
        status: CrawlStatus,
        route_node: str | None = None,
        priority: int = 0,
        page_kind: PageKind | None = None,
    ) -> None:
        for start in range(0, len(entry_ids), 1000):
            await CrawlFrontierModel.filter(id__in=list(entry_ids[start : start + 1000])).update(
                status=status, route_node=route_node, priority=priority, page_kind=page_kind
            )

    async def next_queued(self, source_key: str, *, limit: int) -> list[FrontierEntry]:
        rows = (
            await CrawlFrontierModel.filter(source_key=source_key, status=CrawlStatus.QUEUED)
            .order_by("-priority", "timestamp", "id")
            .limit(limit)
        )
        return [_to_entry(row) for row in rows]

    async def mark_fetched(self, entry_id: int) -> None:
        await CrawlFrontierModel.filter(id=entry_id).update(status=CrawlStatus.FETCHED, error=None)

    async def mark_failed(self, entry_id: int, error: str) -> None:
        row = await CrawlFrontierModel.get_or_none(id=entry_id)
        if row is None:
            return
        row.status = CrawlStatus.FAILED
        row.error = error[:4000]
        row.attempts += 1
        await row.save()

    async def add_evidence(self, source_key: str, rels_by_url_key: Mapping[str, LinkRel]) -> int:
        if not rels_by_url_key:
            return 0
        by_hash = {url_key_hash(key): rel for key, rel in rels_by_url_key.items()}
        rows = await CrawlFrontierModel.filter(
            source_key=source_key, url_key_hash__in=list(by_hash)
        )
        matched: set[str] = set()
        for row in rows:
            matched.add(row.url_key_hash)
            rel = by_hash[row.url_key_hash].value
            evidence = dict(row.evidence or {})
            rels = list(evidence.get("linked_as") or [])
            if rel in rels:
                continue
            evidence["linked_as"] = sorted({*rels, rel})
            row.evidence = evidence
            if row.status in _REROUTABLE:
                row.status = CrawlStatus.DISCOVERED
            await row.save()
        return len(matched)

    async def reset_status(
        self, source_key: str, *, from_status: CrawlStatus, to_status: CrawlStatus
    ) -> int:
        return await CrawlFrontierModel.filter(source_key=source_key, status=from_status).update(
            status=to_status
        )

    async def counts(self, source_key: str) -> dict[CrawlStatus, int]:
        out: dict[CrawlStatus, int] = {}
        for status in CrawlStatus:
            out[status] = await CrawlFrontierModel.filter(
                source_key=source_key, status=status
            ).count()
        return out

    async def sample_urls(self, source_key: str, status: CrawlStatus, *, limit: int) -> list[str]:
        rows = (
            await CrawlFrontierModel.filter(source_key=source_key, status=status)
            .order_by("id")
            .limit(limit * 4)
            .values_list("url_key_hash", "original_url")
        )
        seen: dict[str, str] = {}
        for key_hash, original in rows:
            seen.setdefault(str(key_hash), str(original))
            if len(seen) >= limit:
                break
        return list(seen.values())


class TortoiseCrawlCursorRepository(CrawlCursorRepository):
    async def get(self, source_key: str, scope: str) -> tuple[str | None, bool]:
        row = await CrawlCursorModel.get_or_none(source_key=source_key, scope=scope)
        return (row.resume_key, row.done) if row else (None, False)

    async def save(
        self, source_key: str, scope: str, *, resume_key: str | None, done: bool
    ) -> None:
        row = await CrawlCursorModel.get_or_none(source_key=source_key, scope=scope)
        if row is None:
            await CrawlCursorModel.create(
                source_key=source_key, scope=scope, resume_key=resume_key, done=done
            )
            return
        row.resume_key = resume_key  # type: ignore[assignment]  # nullable column
        row.done = done
        await row.save()
