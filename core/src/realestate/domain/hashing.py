"""Change detection for listings.

Re-scraping a portal returns the same ads over and over. Hashing the fields we
actually care about lets an unchanged ad bump only ``last_seen_at``, leaving
``updated_at`` meaningful as "when did this ad genuinely change".
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from realestate.domain.models import ListingDraft

#: Fields whose change constitutes a real change to the ad. Deliberately excludes
#: anything the scrape itself generates (timestamps, blob keys, ordering).
_HASHED_FIELDS = (
    "title",
    "description",
    "url",
    "listing_type",
    "property_type",
    "price_amount",
    "price_currency",
    "price_type",
    "installment_plan",
    "area_sqm",
    "bedrooms",
    "bathrooms",
    "location_name",
    "country_code",
    "city",
    "district",
    "latitude",
    "longitude",
    "listed_at",
    "is_active",
    "attributes",
)


def draft_fingerprint(draft: ListingDraft) -> dict[str, Any]:
    """Flatten a draft into the plain mapping that gets hashed."""
    point = draft.location.point
    return {
        "title": draft.title,
        "description": draft.description,
        "url": draft.url,
        "listing_type": str(draft.listing_type),
        "property_type": str(draft.property_type),
        "price_amount": str(draft.price.amount) if draft.price.amount is not None else None,
        "price_currency": draft.price.currency,
        "price_type": str(draft.price.price_type),
        "installment_plan": draft.price.installment_plan,
        "area_sqm": str(draft.area_sqm) if draft.area_sqm is not None else None,
        "bedrooms": draft.bedrooms,
        "bathrooms": draft.bathrooms,
        "location_name": draft.location.name,
        "country_code": draft.location.country_code,
        "city": draft.location.city,
        "district": draft.location.district,
        "latitude": str(point.latitude) if point else None,
        "longitude": str(point.longitude) if point else None,
        "listed_at": draft.listed_at.isoformat() if draft.listed_at else None,
        "is_active": draft.is_active,
        # `_`-prefixed keys are provenance (rule versions, raw snippets), not
        # advert content: a new extraction rule must not look like an edit.
        "attributes": {
            key: value for key, value in draft.attributes.items() if not key.startswith("_")
        },
    }


def compute_content_hash(draft: ListingDraft) -> str:
    """Stable sha256 over a draft's business fields.

    ``sort_keys`` makes the result independent of dict ordering, so a source that
    reshuffles its JSON does not look like a content change.
    """
    fingerprint = draft_fingerprint(draft)
    payload = json.dumps(
        {key: fingerprint[key] for key in _HASHED_FIELDS},
        sort_keys=True,
        default=str,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
