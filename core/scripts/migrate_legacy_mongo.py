"""One-time import of the legacy Dubizzle scrape (MongoDB) into the listing tables.

The old scraper kept one collection per category -- ``sale``, ``rent``,
``sale_vacation``, ``rent_vacation`` -- with one document per advert, keyed by
Dubizzle's own advert id (the number in ``...-ID500391584.html``). Each document
becomes a :class:`ListingDraft` written through ``TortoiseListingRepository.
upsert_many``, so content hashes, observations and snapshots are exactly what an
ingestion run would produce.

Decisions, each backed by the data (see the investigation notes in the PR/chat):

- **Own source key** (``dubizzle_eg_legacy``). Not ``dubizzle_eg_wayback``:
  ``archive rebuild`` deletes every listing of an archive source. Not
  ``dubizzle_eg``: only ~100 ids overlap the live scrape, and keeping the import
  apart makes it one ``DELETE`` to undo.
- **Historical rows.** ``approx_ad_creation_time`` is both ``listed_at`` and the
  observation time, so each advert counts in market trends at its listing date.
  ``is_active`` is false, as for the other historical sources.
- **Rent copies of sale adverts are dropped.** ~50k ids appear in both ``sale``
  and ``rent``; the ``rent`` copies are sales (median 7M EGP, sale titles), so a
  rent-collection document whose id is also in a sale collection is skipped.
- **Repeat sightings are kept.** An id in ``sale`` and ``sale_vacation`` (or
  ``rent`` and ``rent_vacation``) carries two capture dates and often two prices;
  each collection is its own batch, so both become observations of one listing.
- **Placeholders are skipped**: the old scraper's synthetic rows (title
  ``Title``) and adverts titled ``test``.
- **Sparse documents are kept.** From mid-2024 the old scraper lost the detail
  panel: no type, price or area. They import as ``OTHER`` with an ``UNKNOWN``
  price, which keeps them out of price statistics -- unless the same advert has
  a full document in another collection: a newer blank capture would win the
  row and erase the type and price the full one shows.
- **No guessing.** Property types follow the live ``dubizzle_eg`` mapping of
  the same Dubizzle labels; outlier prices and areas are stored as found, as
  every other source does. Every original field except title and description
  (which are columns) is kept verbatim in ``attributes._raw``.

Run from ``core/``; pymongo is deliberately not a project dependency:

    ./venv/bin/python -m pip install pymongo
    export LEGACY_MONGO_URL='mongodb://USER:PASSWORD@localhost:27017/'
    ./venv/bin/python scripts/migrate_legacy_mongo.py --dry-run
    ./venv/bin/python scripts/migrate_legacy_mongo.py --db-url postgres://.../scratch --yes
    ./venv/bin/python scripts/migrate_legacy_mongo.py --yes   # [db] url from settings

Re-running is safe: ``(source_key, external_id)`` is the upsert key and an
observation is unique per ``(listing, observed_at)``. To undo:

    DELETE FROM listing WHERE source_key = 'dubizzle_eg_legacy';  -- observations cascade
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from realestate.domain.enums import ListingType, PriceType, PropertyType
from realestate.domain.models import GeoPoint, ListingDraft, Location, Price, UpsertResult

DEFAULT_SOURCE_KEY = "dubizzle_eg_legacy"

#: Processing order matters only for the skip rule: sale ids are known up front.
COLLECTIONS: dict[str, ListingType] = {
    "sale": ListingType.SALE,
    "sale_vacation": ListingType.SALE,
    "rent": ListingType.RENT,
    "rent_vacation": ListingType.RENT,
}
SALE_COLLECTIONS = tuple(name for name, kind in COLLECTIONS.items() if kind is ListingType.SALE)

#: Dubizzle's type labels, mapped as the live ``dubizzle_eg`` source maps their
#: codes (Twin House and iVilla are villas; Duplex, Penthouse, Room and Roof are
#: apartments). Chalets have no domain type, as live vacation homes do not.
PROPERTY_TYPES: dict[str, PropertyType] = {
    "Apartment": PropertyType.APARTMENT,
    "Duplex": PropertyType.APARTMENT,
    "Penthouse": PropertyType.APARTMENT,
    "Hotel Apartment": PropertyType.APARTMENT,
    "Room": PropertyType.APARTMENT,
    "Roof": PropertyType.APARTMENT,
    "Studio": PropertyType.STUDIO,
    "Stand Alone Villa": PropertyType.VILLA,
    "Standalone Villa": PropertyType.VILLA,
    "Twin House": PropertyType.VILLA,
    "Twin house": PropertyType.VILLA,
    "iVilla": PropertyType.VILLA,
    "Town House": PropertyType.TOWNHOUSE,
    "Chalet": PropertyType.OTHER,
}

#: Without a frequency, rent is monthly -- the live source's default too.
RENTAL_FREQUENCIES: dict[str, PriceType] = {
    "Daily": PriceType.PER_NIGHT,
    "Weekly": PriceType.PER_WEEK,
    "Monthly": PriceType.PER_MONTH,
    "Yearly": PriceType.PER_YEAR,
}

#: ``location`` holds only the two most specific levels ("5th Settlement, New
#: Cairo"). The parent is either a governorate -- the live source's ``city`` --
#: or one of these districts, each resolved from the data's own
#: "district, governorate" pairs.
GOVERNORATES = frozenset(
    {
        "Alexandria", "Aswan", "Asyut", "Beheira", "Beni Suef", "Cairo", "Dakahlia",
        "Damietta", "Fayoum", "Gharbia", "Giza", "Ismailia", "Kafr al-Sheikh", "Luxor",
        "Matruh", "Minya", "Monufia", "New Valley", "North Sinai", "Port Said",
        "Qalyubia", "Qena", "Red Sea", "Sharqia", "Sohag", "South Sinai", "Suez",
    }
)  # fmt: skip
DISTRICT_GOVERNORATE: dict[str, str] = {
    "6th of October": "Giza",
    "Abasiya": "Cairo",
    "Agami": "Alexandria",
    "Ain Sukhna": "Suez",
    "Alamein": "Matruh",
    "Amreya": "Alexandria",
    "Badr City": "Cairo",
    "Banha": "Qalyubia",
    "Borg al-Arab": "Alexandria",
    "Dahab": "South Sinai",
    "Dokki": "Giza",
    "El Fostat": "Cairo",
    "Giza District": "Giza",
    "Gouna": "Red Sea",
    "Hadayek October": "Giza",
    "Heliopolis": "Cairo",
    "Hurghada": "Red Sea",
    "Katameya": "Cairo",
    "Maadi": "Cairo",
    "Madinaty": "Cairo",
    "Makadi Bay": "Red Sea",
    "Marsa Matrouh": "Matruh",
    "Moharam Bik": "Alexandria",
    "Mokattam": "Cairo",
    "Mostakbal City": "Cairo",
    "Nasr City": "Cairo",
    "New Cairo": "Cairo",
    "New Capital City": "Cairo",
    "New Damietta": "Damietta",
    "New Heliopolis": "Cairo",
    "New Mansoura": "Dakahlia",
    "New Minya": "Minya",
    "North Coast": "Matruh",
    "Obour City": "Cairo",
    "Ras Sedr": "South Sinai",
    "Sahl Hasheesh": "Red Sea",
    "Sharq District": "Port Said",
    "Sheikh Zayed": "Giza",
    "Sheraton": "Cairo",
    "Shorouk City": "Cairo",
    "Smoha": "Alexandria",
    "Soma Bay": "Red Sea",
    "Zahraa Al Maadi": "Cairo",
    "Zohour District": "Port Said",
}

#: Generous box around Egypt; a point outside it is dropped, not guessed at.
_LAT_RANGE = (Decimal("21.5"), Decimal("32.0"))
_LON_RANGE = (Decimal("24.5"), Decimal("37.5"))

#: Column limits: DECIMAL(14,2) price, DECIMAL(12,2) area, SMALLINT rooms.
_MAX_PRICE = Decimal("1e12")
_MAX_AREA = Decimal("1e10")
_MAX_ROOMS = 32767
_MAX_TITLE = 512

_PLACEHOLDER_TITLES = frozenset({"title", "test"})
_DESCRIPTION_LABEL = "Description"
_ADVERT_ID = re.compile(r"\d+")

#: Fields carried straight into ``attributes`` under the live source's names.
_PASSTHROUGH = {
    "type": "formatted_type",
    "ownership": "ownership",
    "payment_option": "payment_option",
    "completion_status": "completion_status",
    "delivery_term": "delivery_term",
    "delivery_date": "delivery_date",
    "furnished": "furnished",
    "level": "floor_level",
    "compound": "compound",
    "rental_frequency": "rental_frequency",
}
_AMOUNTS = ("down_payment", "deposit", "insurance")


@dataclass(slots=True)
class Stats:
    """Everything the run saw, for the closing report."""

    read: Counter[str] = field(default_factory=Counter)
    skipped: Counter[str] = field(default_factory=Counter)
    mapped: Counter[str] = field(default_factory=Counter)
    kinds: Counter[tuple[str, str]] = field(default_factory=Counter)
    property_types: Counter[str] = field(default_factory=Counter)
    unknown_labels: Counter[str] = field(default_factory=Counter)
    notes: Counter[str] = field(default_factory=Counter)
    unresolved_places: Counter[str] = field(default_factory=Counter)
    written: UpsertResult = field(default_factory=UpsertResult)


@dataclass(frozen=True, slots=True)
class Mapped:
    draft: ListingDraft
    #: Data-quality observations ("no_point", "price_outlier", ...), for stats.
    notes: tuple[str, ...] = ()
    unresolved_place: str | None = None


def map_document(
    collection: str, doc: Mapping[str, Any], *, sale_ids: set[str], full_ids: set[str]
) -> Mapped | str:
    """One Mongo document -> a draft, or the reason it is skipped.

    ``sale_ids`` are the ids in sale collections; ``full_ids`` the ids with an
    imported document that has the structured fields (see :func:`index_ids`).
    """
    external_id = str(doc["_id"])
    title = str(doc.get("title") or "").strip()
    if not _ADVERT_ID.fullmatch(external_id):
        return "non_numeric_id"
    if title.casefold() in _PLACEHOLDER_TITLES:
        return "placeholder_title"
    if not title:
        return "empty_title"
    listing_type = COLLECTIONS[collection]
    if listing_type is ListingType.RENT and external_id in sale_ids:
        return "rent_copy_of_sale"
    label = doc.get("type")
    if label is None and external_id in full_ids:
        return "sparse_repeat"

    notes: list[str] = []
    created = doc["approx_ad_creation_time"]
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)

    if label is None:
        property_type = PropertyType.OTHER
        notes.append("sparse")
    else:
        property_type = PROPERTY_TYPES.get(label, PropertyType.OTHER)
        if label not in PROPERTY_TYPES:
            notes.append(f"unknown_type:{label}")

    price = _price(doc, listing_type, notes)
    location, unresolved = _location(
        str(doc.get("location") or ""), str(doc.get("location_coordinates") or ""), notes
    )

    area = _number(doc.get("area_meter_square"))
    if area is not None and area >= _MAX_AREA:
        notes.append("area_overflow")
        area = None
    elif area is not None and area > 100_000:
        notes.append("area_outlier")

    draft = ListingDraft(
        external_id=external_id,
        title=title[:_MAX_TITLE],
        listing_type=listing_type,
        property_type=property_type,
        location=location,
        price=price,
        url=None,  # the slug was not scraped, and a slug-less URL would be a guess
        description=_description(doc.get("description")),
        area_sqm=area,
        bedrooms=_count(doc.get("bedrooms")),
        bathrooms=_count(doc.get("bathrooms")),
        listed_at=created,
        is_active=False,
        attributes=_attributes(collection, doc),
        observed_at=created,
    )
    return Mapped(draft=draft, notes=tuple(notes), unresolved_place=unresolved)


def _price(doc: Mapping[str, Any], listing_type: ListingType, notes: list[str]) -> Price:
    amount = _number(doc.get("price"))
    if amount is not None and amount >= _MAX_PRICE:
        notes.append("price_overflow")
        amount = None
    if amount is None:
        return Price(amount=None, currency="EGP", price_type=PriceType.UNKNOWN)
    if amount >= Decimal("1e10") or amount < 100:
        notes.append("price_outlier")

    if listing_type is ListingType.RENT:
        frequency = doc.get("rental_frequency")
        price_type = RENTAL_FREQUENCIES.get(frequency or "Monthly", PriceType.PER_MONTH)
        return Price(amount=amount, currency="EGP", price_type=price_type)

    # As in the live source: only "Installment" is an instalment price;
    # "Cash or Installment" quotes the full price.
    if doc.get("payment_option") == "Installment":
        down_payment = _json_number(_number(doc.get("down_payment")))
        plan = {"down_payment": down_payment} if down_payment is not None else None
        return Price(
            amount=amount, currency="EGP", price_type=PriceType.INSTALLMENT, installment_plan=plan
        )
    return Price(amount=amount, currency="EGP", price_type=PriceType.TOTAL)


def _location(label: str, coordinates: str, notes: list[str]) -> tuple[Location, str | None]:
    parts = [part.strip() for part in label.split(",") if part.strip()]
    city = district = None
    unresolved = None
    if len(parts) >= 2:
        name, parent = parts[0], parts[-1]
        if parent in GOVERNORATES:
            city = parent
            district = name if name != parent else None
        elif parent in DISTRICT_GOVERNORATE:
            city, district = DISTRICT_GOVERNORATE[parent], parent
        else:
            district, unresolved = parent, parent
    elif parts and parts[0] != "Egypt":
        name = parts[0]
        city = name if name in GOVERNORATES else None
        if city is None:
            unresolved = name
    else:
        name = "Egypt"
        notes.append("no_place")
    return (
        Location(
            name=name,
            country_code="EG",
            city=city,
            district=district,
            point=_point(coordinates, notes),
        ),
        unresolved,
    )


def _point(text: str, notes: list[str]) -> GeoPoint | None:
    """``"longitude,latitude"`` -- the old scraper stored GeoJSON order."""
    if not text.strip():
        notes.append("no_point")
        return None
    try:
        longitude, latitude = (Decimal(part.strip()) for part in text.split(","))
    except (ValueError, InvalidOperation):
        notes.append("bad_point")
        return None
    if not (_LAT_RANGE[0] <= latitude <= _LAT_RANGE[1]) or not (
        _LON_RANGE[0] <= longitude <= _LON_RANGE[1]
    ):
        notes.append("point_outside_egypt")
        return None
    return GeoPoint(latitude=latitude, longitude=longitude)


def _description(value: Any) -> str | None:
    """The scraper glued the section label on: ``"Description<text>"``."""
    text = str(value or "")
    if text.startswith(_DESCRIPTION_LABEL):
        text = text[len(_DESCRIPTION_LABEL) :]
    return text.strip() or None


def _attributes(collection: str, doc: Mapping[str, Any]) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        target: doc[source] for source, target in _PASSTHROUGH.items() if doc.get(source)
    }
    for name in _AMOUNTS:
        amount = _json_number(_number(doc.get(name)))
        if amount is not None:
            attributes[name] = amount
    if doc.get("price_type"):
        attributes["negotiable"] = doc["price_type"] == "Negotiable"
    for name in ("video", "virtual_tour"):
        if doc.get(name):
            attributes[name] = doc[name] == "Available"
    if collection.endswith("_vacation"):
        attributes["vacation_home"] = True
    # `_`-prefixed: provenance, excluded from the content hash.
    attributes["_import"] = {"store": "mongodb", "collection": collection}
    attributes["_raw"] = {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in doc.items()
        if key not in ("title", "description")
    }
    return attributes


def _number(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _json_number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def _count(value: Any) -> int | None:
    """``"3"`` -> 3; ``"10+"`` -> 10, as the live source reads it."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not digits:
        return None
    count = int(digits)
    return count if count <= _MAX_ROOMS else None


# -- driver ----------------------------------------------------------------


async def index_ids(mongo: Any) -> tuple[set[str], set[str]]:
    """``(sale_ids, full_ids)``: the skip rules need every collection's ids up front.

    A full rent document only counts if it is imported, i.e. is not a copy of a
    sale advert -- otherwise the advert's sparse sale document would be lost too.
    """
    sale_ids: set[str] = set()
    full_ids: set[str] = set()
    for name in SALE_COLLECTIONS:
        async for doc in mongo[name].find({}, {"_id": 1, "type": 1}):
            sale_ids.add(str(doc["_id"]))
            if doc.get("type") is not None:
                full_ids.add(str(doc["_id"]))
    for name in COLLECTIONS:
        if name in SALE_COLLECTIONS:
            continue
        async for doc in mongo[name].find({"type": {"$exists": True}}, {"_id": 1}):
            if str(doc["_id"]) not in sale_ids:
                full_ids.add(str(doc["_id"]))
    return sale_ids, full_ids


def _redact(url: str) -> str:
    parts = urlsplit(url)
    if parts.password is None:
        return url
    netloc = parts.netloc.replace(f":{parts.password}@", ":***@")
    return urlunsplit(parts._replace(netloc=netloc))


async def run(args: argparse.Namespace) -> int:
    from pymongo import AsyncMongoClient

    stats = Stats()
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(args.mongo_url, tz_aware=True)
    mongo = client[args.mongo_db]

    container = None
    listings = None
    if not args.dry_run:
        from realestate.bootstrap import Container
        from realestate.config.settings import DbSettings, load_settings

        settings = load_settings()
        if args.db_url:
            settings = settings.model_copy(update={"db": DbSettings(url=args.db_url)})
        print(f"target: {_redact(settings.db.url)}  source_key={args.source_key}", flush=True)
        container = Container(settings)
        await container.init_db()
        listings = container.listings
    else:
        print("dry run: nothing is written", flush=True)

    try:
        sale_ids, full_ids = await index_ids(mongo)
        print(
            f"{len(sale_ids):,} ids in sale collections, {len(full_ids):,} with full fields",
            flush=True,
        )

        started = time.monotonic()
        for collection in COLLECTIONS:
            batch: list[ListingDraft] = []
            cursor = mongo[collection].find({}, sort=[("_id", 1)], limit=args.limit or 0)
            async for doc in cursor:
                stats.read[collection] += 1
                outcome = map_document(collection, doc, sale_ids=sale_ids, full_ids=full_ids)
                if isinstance(outcome, str):
                    stats.skipped[outcome] += 1
                    continue
                _record(stats, collection, outcome)
                batch.append(outcome.draft)
                if len(batch) >= args.batch_size:
                    await _flush(listings, batch, args.source_key, stats)
                    batch = []
                    _progress(stats, started)
            await _flush(listings, batch, args.source_key, stats)
            _progress(stats, started)
    finally:
        await client.close()
        if container is not None:
            await container.close_db()

    _report(stats, dry_run=args.dry_run)
    return 0


def _record(stats: Stats, collection: str, mapped: Mapped) -> None:
    draft = mapped.draft
    stats.mapped[collection] += 1
    stats.kinds[(draft.listing_type.value, draft.price.price_type.value)] += 1
    stats.property_types[draft.property_type.value] += 1
    for note in mapped.notes:
        if note.startswith("unknown_type:"):
            stats.unknown_labels[note.split(":", 1)[1]] += 1
        else:
            stats.notes[note] += 1
    if mapped.unresolved_place:
        stats.unresolved_places[mapped.unresolved_place] += 1


async def _flush(
    listings: Any, batch: list[ListingDraft], source_key: str, stats: Stats
) -> None:
    if not batch or listings is None:
        return
    from tortoise.transactions import in_transaction

    # One transaction per batch: a crash never leaves listings without their
    # observations, and a re-run simply redoes the unfinished batch.
    async with in_transaction():
        result = await listings.upsert_many(batch, source_key=source_key)
    stats.written = stats.written + result


def _progress(stats: Stats, started: float) -> None:
    read = sum(stats.read.values())
    rate = read / max(time.monotonic() - started, 1e-6)
    w = stats.written
    print(
        f"  read {read:,}  mapped {sum(stats.mapped.values()):,}  "
        f"skipped {sum(stats.skipped.values()):,}  "
        f"created {w.created:,} updated {w.updated:,} unchanged {w.unchanged:,}  "
        f"({rate:,.0f} docs/s)",
        flush=True,
    )


def _report(stats: Stats, *, dry_run: bool) -> None:
    def table(title: str, counter: Mapping[Any, int]) -> None:
        print(f"\n{title}")
        for key, count in sorted(counter.items(), key=lambda item: -item[1]):
            label = " / ".join(key) if isinstance(key, tuple) else str(key)
            print(f"  {label:<40} {count:>9,}")

    print("\n=== report" + (" (dry run)" if dry_run else ""))
    table("read per collection", stats.read)
    table("mapped per collection", stats.mapped)
    table("skipped", stats.skipped)
    table("listing type / price type", stats.kinds)
    table("property type", stats.property_types)
    table("data-quality notes (rows still imported)", stats.notes)
    if stats.unknown_labels:
        table("unmapped type labels (imported as OTHER)", stats.unknown_labels)
    if stats.unresolved_places:
        table("places without a governorate", stats.unresolved_places)
    if not dry_run:
        w = stats.written
        print(f"\nwritten: created {w.created:,}  updated {w.updated:,}  unchanged {w.unchanged:,}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--mongo-url", default=os.environ.get("LEGACY_MONGO_URL"))
    parser.add_argument("--mongo-db", default="dubizzle")
    parser.add_argument("--db-url", help="target PostgreSQL; defaults to [db] url in settings")
    parser.add_argument("--source-key", default=DEFAULT_SOURCE_KEY)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--limit", type=int, default=0, help="per collection, for trials")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="map and report, write nothing")
    mode.add_argument("--yes", action="store_true", help="write to the target database")
    args = parser.parse_args()
    if not args.mongo_url:
        parser.error("set LEGACY_MONGO_URL or pass --mongo-url")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
