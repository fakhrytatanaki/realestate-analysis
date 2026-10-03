"""Offline audit of an archive source: replay archived pages, measure, write nothing.

Workflow status says whether a rule *matched*; it says nothing about whether
the result is right. The audit replays every archived payload of a source
through a chosen extraction graph (the active one, an older version, or a
candidate built in memory) and reports what a run would never show:

* blob integrity (size and sha256 against the recorded metadata);
* page kinds, list/detail-routed pages that came out ``OTHER``, items dropped
  and why;
* identity health -- detail pages for different adverts emitting one id, ids
  that are hashes or bare site roots;
* field completeness over distinct adverts, and pages whose raw HTML carries
  a labelled area that no draft extracted;
* a diff against the stored listings: ids added or lost, fields that change,
  stored nulls the replay fills.

It is the gate for every rule change (``rules seed --dry-run``) and the dry run
of ``archive rebuild``. Same bytes and graph always give the same report.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from realestate.domain.archive import (
    EXPLORE_NODE,
    ArchivedDocument,
    IdentityPolicy,
    capture_quarter,
    is_site_root,
    surt_key,
)
from realestate.domain.enums import PageKind, RawDocumentStatus, RuleDomain
from realestate.domain.exceptions import ConfigurationError
from realestate.domain.gold import GoldScore, GoldSet, score
from realestate.domain.models import Listing, ListingDraft, RawDocument
from realestate.domain.ports.blob_provider import BlobProvider
from realestate.domain.ports.data_source import ArchiveDataSource
from realestate.domain.ports.log_provider import LogProvider
from realestate.domain.ports.repositories import ListingRepository, RawDocumentRepository
from realestate.domain.ports.rules import RuleEngine, RuleGraphRepository
from realestate.domain.ports.source_registry import SourceRegistry
from realestate.domain.rules import ExtractionOutcome, RuleGraph

#: Fields whose completeness is reported, over distinct adverts.
COMPLETENESS_FIELDS = (
    "price",
    "area",
    "bedrooms",
    "bathrooms",
    "description",
    "city",
    "listed_at",
    "property_type",
    "url",
)
#: Fields compared between the replay and the stored rows.
DIFF_FIELDS = (
    "title",
    "listing_type",
    "property_type",
    "price",
    "currency",
    "area",
    "bedrooms",
    "bathrooms",
    "city",
)
#: Labelled area in raw HTML (the structured "Square Meters:" row, not prose
#: that mentions an area -- often a whole mall's): a page with one should
#: yield an area on some advert.
_AREA_LABEL = re.compile(rb"Square Meters\s*:")
_ITEM_PREFIX = re.compile(r"^item \d+:\s*")


@dataclass(slots=True)
class _Sighting:
    captured_at: datetime | None
    document_id: str
    page_kind: PageKind
    draft: ListingDraft


@dataclass(slots=True)
class AuditReport:
    source_key: str
    graph_version: int
    documents: int = 0
    blob_missing: int = 0
    blob_mismatch: int = 0
    page_kinds: Counter[str] = field(default_factory=Counter)
    hint_mismatch: Counter[str] = field(default_factory=Counter)
    templates: Counter[str] = field(default_factory=Counter)
    items_total: int = 0
    items_valid: int = 0
    drop_reasons: Counter[str] = field(default_factory=Counter)
    drafts: int = 0
    unique_ids: int = 0
    hashed_ids: int = 0
    site_root_urls: int = 0
    detail_documents: int = 0
    detail_page_ids: int = 0
    detail_emitted_ids: int = 0
    #: Emitted ids claimed by detail pages of more than one advert.
    identity_collisions: dict[str, list[str]] = field(default_factory=dict)
    completeness: dict[str, int] = field(default_factory=dict)
    price_unparsed_with_text: int = 0
    area_label_documents: int = 0
    area_label_documents_without_area: int = 0
    diff: dict[str, Any] = field(default_factory=dict)
    gold: dict[str, Any] | None = None
    #: quarter -> documents, documents with adverts, adverts first seen there.
    yield_by_quarter: dict[str, dict[str, int]] = field(default_factory=dict)
    #: Captures fetched as exploration samples of SKIP/DEFER/unrouted decisions.
    exploration: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key,
            "graph_version": self.graph_version,
            "documents": self.documents,
            "blob": {"missing": self.blob_missing, "mismatch": self.blob_mismatch},
            "page_kinds": dict(self.page_kinds),
            "hint_mismatch": dict(self.hint_mismatch),
            "templates": dict(self.templates.most_common()),
            "items": {
                "total": self.items_total,
                "valid": self.items_valid,
                "dropped": self.items_total - self.items_valid,
                "drop_reasons": dict(self.drop_reasons.most_common()),
            },
            "drafts": self.drafts,
            "identity": {
                "unique_ids": self.unique_ids,
                "hashed_ids": self.hashed_ids,
                "site_root_urls": self.site_root_urls,
                "detail_documents": self.detail_documents,
                "detail_page_ids": self.detail_page_ids,
                "detail_emitted_ids": self.detail_emitted_ids,
                "collisions": len(self.identity_collisions),
                "collision_examples": dict(list(self.identity_collisions.items())[:10]),
            },
            "completeness": self.completeness,
            "price_unparsed_with_text": self.price_unparsed_with_text,
            "area_label_documents": self.area_label_documents,
            "area_label_documents_without_area": self.area_label_documents_without_area,
            "diff": self.diff,
            "gold": self.gold,
            "yield_by_quarter": {
                quarter: {
                    **counts,
                    "new_ids_per_100_documents": round(
                        100 * counts["new_ids"] / counts["documents"], 1
                    )
                    if counts["documents"]
                    else 0.0,
                }
                for quarter, counts in sorted(self.yield_by_quarter.items())
            },
            "exploration": self.exploration,
        }

    def summary_lines(self) -> list[str]:
        unique = self.unique_ids or 1
        lines = [
            f"{self.source_key} extraction graph v{self.graph_version}: "
            f"{self.documents} documents (blob missing {self.blob_missing}, "
            f"mismatch {self.blob_mismatch})",
            "page kinds: "
            + _fmt(self.page_kinds)
            + (
                f"; routed list/detail but got: {_fmt(self.hint_mismatch)}"
                if self.hint_mismatch
                else ""
            ),
            f"items: {self.items_valid}/{self.items_total} kept; dropped: "
            + (_fmt(self.drop_reasons) or "none"),
            f"identity: {self.unique_ids} unique ids, {self.hashed_ids} hashed, "
            f"{self.site_root_urls} site-root urls; detail pages for "
            f"{self.detail_page_ids} adverts emit {self.detail_emitted_ids} ids, "
            f"{len(self.identity_collisions)} collisions",
            "completeness: "
            + ", ".join(
                f"{name} {count} ({100 * count / unique:.0f}%)"
                for name, count in self.completeness.items()
            ),
            f"area label in {self.area_label_documents} list/detail documents, "
            f"{self.area_label_documents_without_area} of them with no area extracted; "
            f"{self.price_unparsed_with_text} adverts unpriced despite price text "
            "(placeholders such as ج.م1, 'Free', or unparsed)",
        ]
        if self.diff:
            diff = self.diff
            lines.append(
                f"vs stored ({diff['stored']} rows): {diff['added']} ids added, "
                f"{diff['lost']} lost, {diff['stored_hashed_ids']} stored hashed ids; "
                f"stored nulls filled: price {diff['fills']['price']}, "
                f"area {diff['fills']['area']}; changed: " + _fmt(Counter(diff["changed"]))
            )
        if self.yield_by_quarter:
            lines.append(
                "new adverts per 100 documents: "
                + ", ".join(
                    f"{quarter} {100 * c['new_ids'] / c['documents']:.0f}"
                    for quarter, c in sorted(self.yield_by_quarter.items())
                    if c["documents"]
                )
            )
        if self.exploration.get("documents"):
            lines.append(
                f"exploration samples: {self.exploration['documents']} fetched, "
                f"{self.exploration['with_adverts']} held adverts "
                f"({self.exploration['adverts']} adverts) -- routing false negatives"
            )
        if self.gold:
            lines.append(
                f"gold: {self.gold['exact']}/{self.gold['items']} items exact "
                f"({self.gold['rate']:.1%}); misses: {_fmt(Counter(self.gold['misses']))}"
            )
        return lines


def _fmt(counter: Counter[str]) -> str:
    return ", ".join(f"{key} {count}" for key, count in counter.most_common())


class ArchiveAuditService:
    def __init__(
        self,
        *,
        registry: SourceRegistry,
        documents: RawDocumentRepository,
        blob: BlobProvider,
        engine: RuleEngine,
        graphs: RuleGraphRepository,
        listings: ListingRepository,
        log: LogProvider,
        gold: GoldSet | None = None,
    ) -> None:
        self._gold = gold
        self._registry = registry
        self._documents = documents
        self._blob = blob
        self._engine = engine
        self._graphs = graphs
        self._listings = listings
        self._log = log

    async def graph(self, source_key: str, version: int | None = None) -> RuleGraph:
        graph = (
            await self._graphs.get(source_key, RuleDomain.EXTRACTION, version)
            if version is not None
            else await self._graphs.active(source_key, RuleDomain.EXTRACTION)
        )
        if graph is None:
            if version is not None:
                raise ConfigurationError(f"no extraction graph v{version} for '{source_key}'")
            graph = RuleGraph.empty(source_key, RuleDomain.EXTRACTION)
        return graph

    async def audit(
        self,
        source_key: str,
        *,
        graph: RuleGraph | None = None,
        compare: bool = True,
    ) -> AuditReport:
        """Replay every archived payload of ``source_key`` through ``graph``."""
        source = self._registry.create(source_key)
        try:
            if not isinstance(source, ArchiveDataSource):
                raise ConfigurationError(f"source '{source_key}' is not an archive source")
            identity = source.identity_policy()
            country = source.country_code
        finally:
            await source.aclose()
        graph = graph or await self.graph(source_key)

        report = AuditReport(source_key=source_key, graph_version=graph.version)
        sightings: dict[str, list[_Sighting]] = defaultdict(list)
        detail_ids: dict[str, set[str]] = defaultdict(set)
        detail_pages: set[str] = set()
        seen_ids: set[str] = set()

        for raw in await self._all_documents(source_key):
            try:
                content = await self._blob.get(raw.blob_key)
            except Exception:
                report.blob_missing += 1
                continue
            report.documents += 1
            if len(content) != raw.size_bytes or hashlib.sha256(content).hexdigest() != raw.sha256:
                report.blob_mismatch += 1
            document = ArchivedDocument.from_payload(
                content, content_type=raw.content_type, source_url=raw.source_url, meta=raw.meta
            )
            outcome = self._engine.extract(graph, document, country_code=country, identity=identity)
            self._count_document(report, raw, outcome)
            drafts = outcome.drafts if outcome is not None else []
            quarter = capture_quarter(str(raw.meta.get("timestamp") or "0000"))
            counts = report.yield_by_quarter.setdefault(
                quarter, {"documents": 0, "with_adverts": 0, "new_ids": 0}
            )
            counts["documents"] += 1
            counts["with_adverts"] += 1 if drafts else 0
            fresh = {draft.external_id for draft in drafts} - seen_ids
            counts["new_ids"] += len(fresh)
            seen_ids |= fresh
            if raw.meta.get("route_node") == EXPLORE_NODE:
                report.exploration["documents"] = report.exploration.get("documents", 0) + 1
                report.exploration["with_adverts"] = report.exploration.get(
                    "with_adverts", 0
                ) + (1 if drafts else 0)
                report.exploration["adverts"] = report.exploration.get("adverts", 0) + len(drafts)
            if outcome is None:
                continue
            for draft in outcome.drafts:
                sightings[draft.external_id].append(
                    _Sighting(document.captured_at, str(raw.id), outcome.page_kind, draft)
                )
            if outcome.page_kind is PageKind.DETAIL:
                page_id = _page_identity(document.url, identity)
                detail_pages.add(page_id)
                for draft in outcome.drafts:
                    detail_ids[draft.external_id].add(page_id)
            if outcome.page_kind is not PageKind.OTHER and _AREA_LABEL.search(content):
                report.area_label_documents += 1
                if not any(draft.area_sqm is not None for draft in outcome.drafts):
                    report.area_label_documents_without_area += 1

        report.detail_page_ids = len(detail_pages)
        report.detail_emitted_ids = len(detail_ids)
        report.identity_collisions = {
            external_id: sorted(pages)[:5]
            for external_id, pages in detail_ids.items()
            if len(pages) > 1
        }
        latest = {external_id: _latest(seen) for external_id, seen in sightings.items()}
        report.unique_ids = len(latest)
        report.hashed_ids = sum(1 for external_id in latest if external_id.startswith("u:"))
        report.site_root_urls = sum(
            1 for draft in latest.values() if draft.url and is_site_root(draft.url)
        )
        report.completeness = completeness(list(latest.values()))
        if compare:
            report.diff = diff_against_stored(
                latest, await self._listings.list_for_source(source_key)
            )
        if self._gold is not None:
            gold_documents = await self._gold.documents(source_key)
            if gold_documents:
                total = GoldScore()
                for gold in gold_documents:
                    outcome = self._engine.extract(
                        graph, gold.document, country_code=country, identity=identity
                    )
                    total.add(score(outcome.drafts if outcome else [], gold.items))
                report.gold = total.as_dict()
        await self._log.info(
            "archive audit complete",
            source_key=source_key,
            graph_version=graph.version,
            documents=report.documents,
            unique_ids=report.unique_ids,
        )
        return report

    async def gold_candidates(self, source_key: str, *, sample: int) -> list[dict[str, Any]]:
        """Unverified gold labels pre-filled by the active graph, for a person to correct.

        Picks up to ``sample`` documents that produce adverts, rotating through
        the templates that recognised them so every page design is covered,
        and spread over capture time within each. The label points at the
        blob, so the page's HTML never has to leave the blob store.
        """
        source = self._registry.create(source_key)
        try:
            if not isinstance(source, ArchiveDataSource):
                raise ConfigurationError(f"source '{source_key}' is not an archive source")
            identity = source.identity_policy()
            country = source.country_code
        finally:
            await source.aclose()
        graph = await self.graph(source_key)
        by_template: dict[str, list[tuple[RawDocument, ExtractionOutcome]]] = defaultdict(list)
        for raw in await self._all_documents(source_key):
            try:
                content = await self._blob.get(raw.blob_key)
            except Exception:
                continue
            document = ArchivedDocument.from_payload(
                content, content_type=raw.content_type, source_url=raw.source_url, meta=raw.meta
            )
            outcome = self._engine.extract(graph, document, country_code=country, identity=identity)
            if outcome is not None and outcome.drafts:
                by_template[outcome.template_key].append((raw, outcome))
        picked: list[tuple[RawDocument, ExtractionOutcome]] = []
        queues = {key: _spread_over(found, sample) for key, found in sorted(by_template.items())}
        while len(picked) < sample and any(queues.values()):
            for key in list(queues):
                if queues[key] and len(picked) < sample:
                    picked.append(queues[key].pop(0))
        return [
            {
                "verified": False,
                "note": f"pre-filled by {outcome.template_key} (graph v{graph.version}); "
                "check every item against the page, fix values, then set verified",
                "document": {
                    "blob_key": raw.blob_key,
                    "content_type": raw.content_type,
                    "source_url": raw.source_url,
                    "meta": raw.meta,
                    "raw_document_id": str(raw.id),
                },
                "items": [_gold_item(draft) for draft in outcome.drafts],
            }
            for raw, outcome in picked
        ]

    async def _all_documents(self, source_key: str) -> list[RawDocument]:
        documents: list[RawDocument] = []
        for status in RawDocumentStatus:
            documents.extend(
                await self._documents.list_by_status(
                    source_key=source_key, status=status, limit=1_000_000
                )
            )
        return sorted(documents, key=lambda doc: (doc.captured_at or doc.fetched_at, str(doc.id)))

    @staticmethod
    def _count_document(
        report: AuditReport, raw: RawDocument, outcome: ExtractionOutcome | None
    ) -> None:
        kind = outcome.page_kind.value if outcome is not None else "MISS"
        report.page_kinds[kind] += 1
        hint = str(raw.meta.get("page_kind_hint") or "").upper()
        if hint in (PageKind.LIST.value, PageKind.DETAIL.value) and kind != hint:
            report.hint_mismatch[f"{hint}->{kind}"] += 1
        if outcome is None:
            return
        report.templates[outcome.template_key] += 1
        report.items_total += outcome.items_total
        report.items_valid += outcome.items_valid
        report.drafts += len(outcome.drafts)
        for problem in outcome.problems:
            report.drop_reasons[problem_reason(problem)] += 1
        for draft in outcome.drafts:
            raw_price = (draft.attributes.get("_raw") or {}).get("price")
            if draft.price.amount is None and raw_price and re.search(r"\d", str(raw_price)):
                report.price_unparsed_with_text += 1


def _gold_item(draft: ListingDraft) -> dict[str, Any]:
    priced = draft.price.amount is not None
    return {
        "external_id": draft.external_id,
        "title": draft.title,
        "amount": str(draft.price.amount) if priced else None,
        "currency": draft.price.currency if priced else None,
        "listing_type": draft.listing_type.value,
    }


def _spread_over[T](items: list[T], count: int) -> list[T]:
    """Up to ``count`` items evenly spaced over ``items`` (in capture order)."""
    if len(items) <= count:
        return list(items)
    step = len(items) / count
    return [items[int(index * step)] for index in range(count)]


def problem_reason(problem: str) -> str:
    """``"item 3: identity conflict: url says 1, ..."`` -> ``"identity conflict"``."""
    return _ITEM_PREFIX.sub("", problem).split(":")[0].split(" (")[0].strip()


def _page_identity(url: str, identity: IdentityPolicy | None) -> str:
    """Which advert a detail page is, judged from its own archived URL."""
    if identity is not None:
        found = identity.canonical_id(url)
        if found:
            return found
    return surt_key(url)


def _latest(seen: list[_Sighting]) -> ListingDraft:
    """The newest capture's draft, preferring a detail page at the same moment."""
    return max(
        seen,
        key=lambda s: (
            s.captured_at.timestamp() if s.captured_at else 0.0,
            s.page_kind is PageKind.DETAIL,
            s.document_id,
        ),
    ).draft


def _value(draft: ListingDraft, name: str) -> Any:
    match name:
        case "price":
            return draft.price.amount
        case "currency":
            return draft.price.currency if draft.price.amount is not None else None
        case "area":
            return draft.area_sqm
        case "city":
            return draft.location.city
        case "property_type":
            return draft.property_type.value if draft.property_type.value != "OTHER" else None
        case "listing_type":
            return draft.listing_type.value
        case _:
            return getattr(draft, name)


def _stored(listing: Listing, name: str) -> Any:
    match name:
        case "price":
            return listing.price.amount
        case "currency":
            return listing.price.currency if listing.price.amount is not None else None
        case "area":
            return listing.area_sqm
        case "city":
            return listing.location.city
        case "property_type":
            return listing.property_type.value if listing.property_type.value != "OTHER" else None
        case "listing_type":
            return listing.listing_type.value
        case _:
            return getattr(listing, name)


def completeness(drafts: list[ListingDraft]) -> dict[str, int]:
    """How many distinct adverts have each field."""
    return {
        name: sum(1 for draft in drafts if _value(draft, name) not in (None, ""))
        for name in COMPLETENESS_FIELDS
    }


def diff_against_stored(latest: dict[str, ListingDraft], stored: list[Listing]) -> dict[str, Any]:
    by_id = {listing.external_id: listing for listing in stored}
    common = latest.keys() & by_id.keys()
    changed: Counter[str] = Counter()
    fills = {"price": 0, "area": 0}
    for external_id in common:
        draft, listing = latest[external_id], by_id[external_id]
        for name in DIFF_FIELDS:
            if _value(draft, name) != _stored(listing, name):
                changed[name] += 1
        if listing.price.amount is None and draft.price.amount is not None:
            fills["price"] += 1
        if listing.area_sqm is None and draft.area_sqm is not None:
            fills["area"] += 1
    return {
        "stored": len(by_id),
        "replayed": len(latest),
        "common": len(common),
        "added": len(latest.keys() - by_id.keys()),
        "lost": len(by_id.keys() - latest.keys()),
        "lost_examples": sorted(by_id.keys() - latest.keys())[:20],
        "stored_hashed_ids": sum(1 for external_id in by_id if external_id.startswith("u:")),
        "changed": dict(changed),
        "fills": fills,
    }
