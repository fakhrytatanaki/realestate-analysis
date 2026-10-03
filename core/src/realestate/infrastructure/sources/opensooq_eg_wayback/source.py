"""OpenSooq Egypt captures, including the older ``/view/`` portal.

Domain-wide CDX enumeration includes both ``/view/`` and ``/ar/`` directly:
enumerating only the homepage misses captures of its redirect destinations.
The shared replay client preserves the served URL and time after redirects.
Reviewed rules are installed explicitly with ``rules seed``; unsupported
designs remain extraction gaps rather than guessed listings.
"""

from __future__ import annotations

from typing import ClassVar

from realestate.domain.archive import IdentityPolicy
from realestate.infrastructure.sources.wayback.source import WaybackDataSource

# Both the 2008 portal and the 2016/2025 Arabic site use numeric /search/ IDs.
# Anchor the host and path so category, city, and pagination numbers cannot
# become advert identities, and an off-site advert link cannot be accepted.
OPENSOOQ_EG_IDENTITY = IdentityPolicy(
    id_patterns=(
        r"^https?://(?:www\.)?eg\.opensooq\.com(?::(?:80|443))?"
        r"/(?:ar/)?search/([0-9]+)(?:/[^?#]*)?(?:[?#].*)?$",
    ),
    reject_patterns=(
        r"/(?:view|ar)(?:/[0-9]+)?/?(?:[?#]|$)",
        r"/(?:ar/)?search/?(?:[?#]|$)",
    ),
)


class OpensooqEgWaybackDataSource(WaybackDataSource):
    key: ClassVar[str] = "opensooq_eg_wayback"
    display_name: ClassVar[str] = "OpenSooq Egypt (archived)"
    country_code: ClassVar[str] = "EG"

    default_domain: ClassVar[str] = "eg.opensooq.com"
    default_from_year: ClassVar[int] = 2008
    default_to_year: ClassVar[int] = 2026

    def identity_policy(self) -> IdentityPolicy:
        return OPENSOOQ_EG_IDENTITY
