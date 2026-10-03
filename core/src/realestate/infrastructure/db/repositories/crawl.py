"""Tortoise-backed crawl frontier and enumeration cursors."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime

from tortoise.expressions import Q

from realestate.domain.archive import (
    Capture,
    FrontierEntry,
    LinkRequest,
    capture_quarter,
    stratified_order,
)
from realestate.domain.enums import CrawlStatus, LinkRel, LinkRequestStatus, PageKind
from realestate.domain.ports.archive import (
    CrawlCursorRepository,
    CrawlFrontierRepository,
    LinkRequestRepository,
)
from realestate.infrastructure.db.models import (
    CrawlCursorModel,
    CrawlFrontierModel,
    CrawlLinkRequestModel,
)

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

    async def claim_queued(
        self,
        source_key: str,
        *,
        limit: int,
        now: datetime,
        per_quarter: int | None = None,
    ) -> list[FrontierEntry]:
        candidates = await CrawlFrontierModel.filter(
            Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now),
            source_key=source_key,
            status=CrawlStatus.QUEUED,
        ).values_list("id", "timestamp", "priority")
        taken: Counter[str] = Counter()
        if per_quarter is not None:
            for (timestamp,) in await CrawlFrontierModel.filter(
                source_key=source_key,
                status__in=[CrawlStatus.FETCHED, CrawlStatus.FETCHING],
            ).values_list("timestamp"):
                taken[capture_quarter(str(timestamp))] += 1
        ordered = stratified_order(
            [(int(i), str(t), int(p)) for i, t, p in candidates],
            taken=taken,
            per_stratum=per_quarter,
        )[:limit]
        if not ordered:
            return []
        # The status condition makes a concurrent claimer skip rows taken here.
        await CrawlFrontierModel.filter(id__in=ordered, status=CrawlStatus.QUEUED).update(
            status=CrawlStatus.FETCHING, claimed_at=now
        )
        rows = await CrawlFrontierModel.filter(
            id__in=ordered, status=CrawlStatus.FETCHING, claimed_at=now
        )
        position = {entry_id: index for index, entry_id in enumerate(ordered)}
        return [_to_entry(row) for row in sorted(rows, key=lambda row: position[row.id])]

    async def release_stale_claims(self, source_key: str, *, claimed_before: datetime) -> int:
        return await CrawlFrontierModel.filter(
            Q(claimed_at__isnull=True) | Q(claimed_at__lt=claimed_before),
            source_key=source_key,
            status=CrawlStatus.FETCHING,
        ).update(status=CrawlStatus.QUEUED, claimed_at=None)

    async def mark_fetched(self, entry_id: int) -> None:
        await CrawlFrontierModel.filter(id=entry_id).update(
            status=CrawlStatus.FETCHED, error=None, claimed_at=None, next_retry_at=None
        )

    async def mark_failed(
        self, entry_id: int, error: str, *, retry_at: datetime | None = None
    ) -> None:
        row = await CrawlFrontierModel.get_or_none(id=entry_id)
        if row is None:
            return
        row.status = CrawlStatus.QUEUED if retry_at is not None else CrawlStatus.FAILED
        row.next_retry_at = retry_at  # type: ignore[assignment]  # nullable column
        row.claimed_at = None  # type: ignore[assignment]
        row.error = error[:4000]
        row.attempts += 1
        await row.save()

    async def known_url_keys(self, source_key: str, url_keys: Sequence[str]) -> set[str]:
        by_hash = {url_key_hash(key): key for key in url_keys}
        found: set[str] = set()
        hashes = list(by_hash)
        for start in range(0, len(hashes), 1000):
            for (key_hash,) in await CrawlFrontierModel.filter(
                source_key=source_key, url_key_hash__in=hashes[start : start + 1000]
            ).values_list("url_key_hash"):
                found.add(by_hash[str(key_hash)])
        return found

    async def captures_with_status(
        self, source_key: str, statuses: Sequence[CrawlStatus]
    ) -> list[tuple[int, str]]:
        rows = await CrawlFrontierModel.filter(
            source_key=source_key, status__in=list(statuses)
        ).values_list("id", "timestamp")
        return [(int(entry_id), str(timestamp)) for entry_id, timestamp in rows]

    async def reopen_capped(self, source_key: str) -> int:
        return await CrawlFrontierModel.filter(
            source_key=source_key,
            status=CrawlStatus.SKIPPED,
            route_node__endswith="#capture-cap",
        ).update(status=CrawlStatus.DISCOVERED, route_node=None)

    async def status_by_quarter(self, source_key: str) -> dict[tuple[str, CrawlStatus], int]:
        counts: Counter[tuple[str, CrawlStatus]] = Counter()
        for timestamp, status in await CrawlFrontierModel.filter(
            source_key=source_key
        ).values_list("timestamp", "status"):
            counts[(capture_quarter(str(timestamp)), CrawlStatus(status))] += 1
        return dict(counts)

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


class TortoiseLinkRequestRepository(LinkRequestRepository):
    async def request(self, requests: Sequence[LinkRequest]) -> int:
        if not requests:
            return 0
        rows = [
            CrawlLinkRequestModel(
                source_key=request.source_key,
                url_key=request.url_key,
                url_key_hash=url_key_hash(request.url_key),
                url=request.url,
                rel=request.rel,
                parent_timestamp=request.parent_timestamp,
                status=LinkRequestStatus.PENDING,
            )
            for request in requests
        ]
        before = await CrawlLinkRequestModel.filter(source_key=requests[0].source_key).count()
        await CrawlLinkRequestModel.bulk_create(rows, ignore_conflicts=True)
        after = await CrawlLinkRequestModel.filter(source_key=requests[0].source_key).count()
        return after - before

    async def pending(self, source_key: str, *, limit: int) -> list[LinkRequest]:
        rows = await CrawlLinkRequestModel.filter(
            source_key=source_key, status=LinkRequestStatus.PENDING
        ).order_by("id")
        rank = {LinkRel.DETAIL: 0, LinkRel.LIST: 1, LinkRel.PAGINATION: 2}
        rows = sorted(rows, key=lambda row: (rank.get(row.rel, 3), row.id))[:limit]
        return [
            LinkRequest(
                id=row.id,
                source_key=row.source_key,
                url_key=row.url_key,
                url=row.url,
                rel=row.rel,
                parent_timestamp=row.parent_timestamp,
                status=row.status,
                attempts=row.attempts,
            )
            for row in rows
        ]

    async def resolve(
        self,
        request_id: int,
        *,
        status: LinkRequestStatus,
        captures_found: int = 0,
        error: str | None = None,
    ) -> None:
        row = await CrawlLinkRequestModel.get_or_none(id=request_id)
        if row is None:
            return
        row.status = status
        row.captures_found = captures_found
        row.attempts += 1
        row.error = error[:2000] if error else None  # type: ignore[assignment]
        await row.save()

    async def counts(self, source_key: str) -> dict[LinkRequestStatus, int]:
        return {
            status: await CrawlLinkRequestModel.filter(
                source_key=source_key, status=status
            ).count()
            for status in LinkRequestStatus
        }


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
