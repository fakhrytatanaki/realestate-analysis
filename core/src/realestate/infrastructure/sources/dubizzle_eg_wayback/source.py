"""Dubizzle Egypt captures, isolated from live Dubizzle and archived OLX."""

from __future__ import annotations

from typing import ClassVar

from realestate.infrastructure.sources.wayback.source import WaybackDataSource


class DubizzleEgWaybackDataSource(WaybackDataSource):
    key: ClassVar[str] = "dubizzle_eg_wayback"
    display_name: ClassVar[str] = "Dubizzle Egypt (archived)"
    country_code: ClassVar[str] = "EG"

    default_domain: ClassVar[str] = "dubizzle.com.eg"
    default_from_year: ClassVar[int] = 2023
    default_to_year: ClassVar[int] = 2026
