"""Shared test fixtures.

Most tests run with no database at all: the API is exercised through fake
repositories injected with ``dependency_overrides``, which is the payoff of
depending on ports rather than on Tortoise directly.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from realestate.domain.enums import ListingType, PriceType, PropertyType, SortOrder
from realestate.domain.exceptions import ConflictError
from realestate.domain.models import (
    GeoPoint,
    Listing,
    ListingDraft,
    Location,
    Page,
    Price,
    Session,
    UpsertResult,
    User,
)
from realestate.domain.ports.log_provider import LogProvider
from realestate.domain.ports.repositories import (
    ListingRepository,
    SessionRepository,
    UserRepository,
)
from realestate.domain.ports.security import PasswordHasher
from realestate.domain.query import ListingQuery
from realestate.infrastructure.db.geo import haversine_km

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "var" / "fixtures"

#: Set by the integration tests to reach a real database.
DATABASE_URL_ENV = "REALESTATE_TEST_DB_URL"


class NullLogProvider(LogProvider):
    """Captures records instead of writing them anywhere."""

    def __init__(self, context: dict[str, object] | None = None) -> None:
        self.context = context or {}
        self.records: list[tuple[str, str, dict[str, object]]] = []

    def bind(self, **context: object) -> NullLogProvider:
        child = NullLogProvider({**self.context, **context})
        child.records = self.records  # share the sink, like the real providers
        return child

    async def log(self, level, message: str, **fields: object) -> None:  # type: ignore[no-untyped-def]
        self.records.append((str(level), message, {**self.context, **fields}))


class InMemoryListingRepository(ListingRepository):
    """Enough of the repository to drive the API without a database."""

    def __init__(self, listings: Sequence[Listing] = ()) -> None:
        self.listings: dict[UUID, Listing] = {item.id: item for item in listings}

    async def upsert_many(self, drafts, *, source_key, raw_document_id=None) -> UpsertResult:  # type: ignore[no-untyped-def]
        created = 0
        for draft in drafts:
            listing = make_listing(source_key=source_key, draft=draft)
            self.listings[listing.id] = listing
            created += 1
        return UpsertResult(created=created)

    async def search(self, query: ListingQuery) -> Page[Listing]:
        matches = [item for item in self.listings.values() if _matches(item, query)]
        if query.geo is not None:
            matches = _with_distance(matches, query)
        matches.sort(key=lambda item: _sort_key(item, query.sort))
        window = matches[query.offset : query.offset + query.limit]
        return Page(items=window, total=len(matches), limit=query.limit, offset=query.offset)

    async def get(self, listing_id: UUID) -> Listing | None:
        return self.listings.get(listing_id)

    async def mark_stale(self, source_key: str, *, not_seen_since: datetime) -> int:
        return 0

    async def count(self, source_key: str | None = None) -> int:
        if source_key is None:
            return len(self.listings)
        return sum(1 for item in self.listings.values() if item.source_key == source_key)

    async def list_for_source(self, source_key: str) -> list[Listing]:
        return sorted(
            (item for item in self.listings.values() if item.source_key == source_key),
            key=lambda item: item.external_id,
        )

    async def delete_source(self, source_key: str) -> int:
        doomed = [key for key, item in self.listings.items() if item.source_key == source_key]
        for key in doomed:
            del self.listings[key]
        return len(doomed)


def _matches(listing: Listing, query: ListingQuery) -> bool:
    if query.listing_type is not None and listing.listing_type is not query.listing_type:
        return False
    if query.property_types and listing.property_type not in query.property_types:
        return False
    if query.source_keys and listing.source_key not in query.source_keys:
        return False
    if query.city and (listing.location.city or "").lower() != query.city.lower():
        return False
    if query.currency and listing.price.currency != query.currency.upper():
        return False
    if query.price_max is not None and (
        listing.price.amount is None or listing.price.amount > query.price_max
    ):
        return False
    if query.price_min is not None and (
        listing.price.amount is None or listing.price.amount < query.price_min
    ):
        return False
    if query.is_active is not None and listing.is_active is not query.is_active:
        return False
    return True


def _with_distance(listings: list[Listing], query: ListingQuery) -> list[Listing]:
    from dataclasses import replace

    assert query.geo is not None
    out: list[Listing] = []
    for listing in listings:
        point = listing.location.point
        if point is None:
            continue
        distance = haversine_km(
            query.geo.latitude, query.geo.longitude, point.latitude, point.longitude
        )
        if distance <= query.geo.radius_km:
            out.append(replace(listing, distance_km=distance))
    return out


def _sort_key(listing: Listing, sort: SortOrder):  # type: ignore[no-untyped-def]
    match sort:
        case SortOrder.PRICE_ASC:
            return (listing.price.amount or Decimal(0),)
        case SortOrder.PRICE_DESC:
            return (-(listing.price.amount or Decimal(0)),)
        case SortOrder.DISTANCE:
            return (listing.distance_km or 0.0,)
        case _:
            return (-(listing.listed_at or datetime.min.replace(tzinfo=UTC)).timestamp(),)


def make_listing(
    *,
    source_key: str = "fixture",
    draft: ListingDraft | None = None,
    **overrides: object,
) -> Listing:
    """Build a persisted-looking listing for tests."""
    now = datetime.now(UTC)
    draft = draft or make_draft()
    base = {
        "id": uuid4(),
        "source_key": source_key,
        "external_id": draft.external_id,
        "title": draft.title,
        "listing_type": draft.listing_type,
        "property_type": draft.property_type,
        "location": draft.location,
        "price": draft.price,
        "url": draft.url,
        "description": draft.description,
        "area_sqm": draft.area_sqm,
        "bedrooms": draft.bedrooms,
        "bathrooms": draft.bathrooms,
        "listed_at": draft.listed_at or now,
        "is_active": draft.is_active,
        "attributes": draft.attributes,
        "content_hash": "0" * 64,
        "first_seen_at": now,
        "last_seen_at": now,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return Listing(**base)  # type: ignore[arg-type]


def make_draft(
    *,
    external_id: str = "x-1",
    title: str = "Test listing",
    listing_type: ListingType = ListingType.RENT,
    property_type: PropertyType = PropertyType.APARTMENT,
    amount: Decimal | None = Decimal("1000"),
    currency: str = "EGP",
    price_type: PriceType = PriceType.PER_MONTH,
    latitude: float | None = 30.0444,
    longitude: float | None = 31.2357,
    city: str = "Cairo",
    **overrides: object,
) -> ListingDraft:
    """Build a draft with sensible defaults."""
    point = (
        GeoPoint(latitude=Decimal(str(latitude)), longitude=Decimal(str(longitude)))
        if latitude is not None and longitude is not None
        else None
    )
    base = {
        "external_id": external_id,
        "title": title,
        "listing_type": listing_type,
        "property_type": property_type,
        "location": Location(name=f"{city} centre", country_code="EG", city=city, point=point),
        "price": Price(amount=amount, currency=currency, price_type=price_type),
    }
    base.update(overrides)
    return ListingDraft(**base)  # type: ignore[arg-type]


@pytest.fixture
def null_log() -> NullLogProvider:
    return NullLogProvider()


@pytest.fixture
def blob_root(tmp_path: Path) -> Path:
    root = tmp_path / "blob"
    root.mkdir()
    return root


@pytest.fixture
def integration_db_url() -> str:
    """Database URL for integration tests, or skip when none is configured."""
    url = os.environ.get(DATABASE_URL_ENV)
    if not url:
        pytest.skip(f"set {DATABASE_URL_ENV} to run database integration tests")
    return url


@pytest.fixture
async def initialised_db(integration_db_url: str) -> AsyncIterator[None]:
    """Create the schema in a real database and drop it again afterwards."""
    from tortoise import Tortoise

    from realestate.config.tortoise import build_tortoise_config

    await Tortoise.init(config=build_tortoise_config(integration_db_url))
    await Tortoise.generate_schemas(safe=True)
    try:
        yield
    finally:
        from realestate.infrastructure.db.models import (
            CrawlCursorModel,
            CrawlFrontierModel,
            ListingModel,
            LlmDecisionModel,
            RawDocumentModel,
            RuleGapModel,
            RuleGraphModel,
            ScrapeRunModel,
            SessionModel,
            UserModel,
        )

        archive_models = (
            CrawlFrontierModel,
            CrawlCursorModel,
            RuleGraphModel,
            RuleGapModel,
            LlmDecisionModel,
        )
        for model in archive_models:
            await model.all().delete()
        await ListingModel.all().delete()
        await RawDocumentModel.all().delete()
        await ScrapeRunModel.all().delete()
        await SessionModel.all().delete()
        await UserModel.all().delete()
        await Tortoise.close_connections()


class InMemoryRawDocumentRepository:
    """Raw document tracking without a database."""

    def __init__(self) -> None:
        self.documents: dict[UUID, object] = {}

    async def create(self, *, source_key, payload, blob, scrape_run_id=None):  # type: ignore[no-untyped-def]
        from realestate.domain.enums import RawDocumentStatus
        from realestate.domain.models import RawDocument

        document = RawDocument(
            id=uuid4(),
            source_key=source_key,
            kind=payload.kind,
            status=RawDocumentStatus.PENDING,
            blob_key=blob.key,
            blob_uri=blob.uri,
            content_type=blob.content_type,
            size_bytes=blob.size_bytes,
            sha256=blob.sha256,
            fetched_at=datetime.now(UTC),
            external_id=payload.external_id,
            source_url=payload.source_url,
            meta=payload.meta,
            scrape_run_id=scrape_run_id,
        )
        self.documents[document.id] = document
        return document

    async def get(self, document_id):  # type: ignore[no-untyped-def]
        return self.documents.get(document_id)

    async def list_by_status(  # type: ignore[no-untyped-def]
        self,
        *,
        source_key=None,
        status=None,
        limit=100,
        fetched_after=None,
        graph_version_below=None,
    ):
        from realestate.domain.enums import RawDocumentStatus

        status = status or RawDocumentStatus.PENDING
        return [
            document
            for document in self.documents.values()
            if document.status is status
            and (source_key is None or document.source_key == source_key)
            and (
                graph_version_below is None
                or document.graph_version is None
                or document.graph_version < graph_version_below
            )
        ][:limit]

    async def mark_parsed(self, document_id, *, graph_version=None, report=None):  # type: ignore[no-untyped-def]
        from dataclasses import replace

        from realestate.domain.enums import RawDocumentStatus

        document = self.documents[document_id]
        self.documents[document_id] = replace(
            document,
            status=RawDocumentStatus.PARSED,
            parsed_at=datetime.now(UTC),
            graph_version=graph_version,
            parse_report=dict(report) if report is not None else None,
        )

    async def mark_failed(self, document_id, error):  # type: ignore[no-untyped-def]
        from dataclasses import replace

        from realestate.domain.enums import RawDocumentStatus

        document = self.documents[document_id]
        self.documents[document_id] = replace(
            document,
            status=RawDocumentStatus.FAILED,
            parse_error=error,
            attempts=document.attempts + 1,
        )

    async def mark_unrecognised(self, document_id, reason, *, graph_version=None, report=None):  # type: ignore[no-untyped-def]
        from dataclasses import replace

        from realestate.domain.enums import RawDocumentStatus

        document = self.documents[document_id]
        self.documents[document_id] = replace(
            document,
            status=RawDocumentStatus.UNRECOGNISED,
            parse_error=reason,
            graph_version=graph_version,
            parse_report=dict(report) if report is not None else None,
        )

    async def reset_status(self, source_key, *, from_statuses, to_status=None):  # type: ignore[no-untyped-def]
        from dataclasses import replace

        from realestate.domain.enums import RawDocumentStatus

        moved = 0
        for document_id, document in list(self.documents.items()):
            if document.source_key == source_key and document.status in from_statuses:
                self.documents[document_id] = replace(
                    document, status=to_status or RawDocumentStatus.PENDING, parse_error=None
                )
                moved += 1
        return moved

    async def find_by_sha256(self, source_key, sha256):  # type: ignore[no-untyped-def]
        for document in self.documents.values():
            if document.source_key == source_key and document.sha256 == sha256:
                return document
        return None


class InMemoryScrapeRunRepository:
    """Run history without a database."""

    def __init__(self) -> None:
        self.runs: dict[UUID, object] = {}

    async def start(self, source_key, trigger):  # type: ignore[no-untyped-def]
        from realestate.domain.enums import RunStatus
        from realestate.domain.models import ScrapeRun

        run = ScrapeRun(
            id=uuid4(),
            source_key=source_key,
            trigger=trigger,
            status=RunStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        self.runs[run.id] = run
        return run

    async def finish(  # type: ignore[no-untyped-def]
        self,
        run_id,
        *,
        status,
        documents_fetched=0,
        listings_created=0,
        listings_updated=0,
        errors=0,
        error_message=None,
        stats=None,
    ):
        from dataclasses import replace

        run = replace(
            self.runs[run_id],
            status=status,
            finished_at=datetime.now(UTC),
            documents_fetched=documents_fetched,
            listings_created=listings_created,
            listings_updated=listings_updated,
            errors=errors,
            error_message=error_message,
            stats=dict(stats or {}),
        )
        self.runs[run_id] = run
        return run

    async def get(self, run_id):  # type: ignore[no-untyped-def]
        return self.runs.get(run_id)

    async def list(self, *, source_key=None, limit=20, offset=0):  # type: ignore[no-untyped-def]
        matches = [
            run
            for run in self.runs.values()
            if source_key is None or run.source_key == source_key
        ]
        matches.sort(key=lambda run: run.started_at, reverse=True)
        return Page(
            items=matches[offset : offset + limit],
            total=len(matches),
            limit=limit,
            offset=offset,
        )

    async def latest_per_source(self):  # type: ignore[no-untyped-def]
        latest: dict[str, object] = {}
        for run in sorted(self.runs.values(), key=lambda r: r.started_at, reverse=True):
            latest.setdefault(run.source_key, run)
        return latest


class FakePasswordHasher(PasswordHasher):
    """Reversible and instant, so auth tests do not pay for argon2.

    ``version`` lets a test simulate stronger parameters: hashes made with an
    older version report ``needs_rehash``.
    """

    def __init__(self, version: int = 1) -> None:
        self.version = version

    def hash(self, password: str) -> str:
        return f"fake${self.version}${password}"

    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash.split("$", 2)[-1] == password

    def needs_rehash(self, password_hash: str) -> bool:
        return not password_hash.startswith(f"fake${self.version}$")


class InMemoryUserRepository(UserRepository):
    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}

    async def add(self, user: User) -> User:
        if any(existing.email == user.email for existing in self.users.values()):
            raise ConflictError("an account with this email already exists")
        self.users[user.id] = user
        return user

    async def get(self, user_id: UUID) -> User | None:
        return self.users.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        return next((user for user in self.users.values() if user.email == email), None)

    async def update_password_hash(self, user_id: UUID, password_hash: str) -> None:
        self.users[user_id] = replace(self.users[user_id], password_hash=password_hash)


class InMemorySessionRepository(SessionRepository):
    def __init__(self) -> None:
        self.sessions: dict[UUID, Session] = {}

    async def add(self, session: Session) -> Session:
        self.sessions[session.id] = session
        return session

    async def get_by_token_hash(self, token_hash: str) -> Session | None:
        return next(
            (item for item in self.sessions.values() if item.token_hash == token_hash), None
        )

    async def touch(self, session_id: UUID, *, at: datetime) -> None:
        if session_id in self.sessions:
            self.sessions[session_id] = replace(self.sessions[session_id], last_used_at=at)

    async def delete(self, session_id: UUID) -> None:
        self.sessions.pop(session_id, None)

    async def delete_for_user(self, user_id: UUID) -> int:
        doomed = [key for key, item in self.sessions.items() if item.user_id == user_id]
        for key in doomed:
            del self.sessions[key]
        return len(doomed)
