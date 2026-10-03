"""OLX Egypt as archived by the Wayback Machine, 2010-2023.

The site went through at least five generations (city subdomains and
``-iid-`` adverts; an Arabic "OLX Dubizzle" platform; the OLX i2 platform with
``data-ninja`` JSON; the EMPG platform with ``window.state``) before
redirecting to dubizzle.com.eg. None of that is encoded here: the rules for
every generation are induced by the LLM loop and stored as rule graphs. See
``docs/historical-sources-plan.md``.
"""

from __future__ import annotations

from typing import ClassVar

from realestate.infrastructure.sources.wayback.source import WaybackDataSource


class OlxEgWaybackDataSource(WaybackDataSource):
    key: ClassVar[str] = "olx_eg_wayback"
    display_name: ClassVar[str] = "OLX Egypt (archived)"
    country_code: ClassVar[str] = "EG"

    default_domain: ClassVar[str] = "olx.com.eg"
    default_from_year: ClassVar[int] = 2010
    default_to_year: ClassVar[int] = 2023
    # City hosts are a confirmed part of the older OLX layouts. Permit only
    # this capture's requested subdomain plus apex/www, not arbitrary redirects.
    allow_requested_subdomains: ClassVar[bool] = True
