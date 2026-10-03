"""OLX Egypt as archived by the Wayback Machine, 2010-2023.

The site went through at least five generations (city subdomains and
``-iid-`` adverts; an Arabic "OLX Dubizzle" platform; the OLX i2 platform with
``data-ninja`` JSON; the EMPG platform with ``window.state``) before
redirecting to dubizzle.com.eg. See ``docs/historical-sources-plan.md``.

Two things are fixed here rather than induced, because an induced mistake in
either passes every plausibility check while corrupting the data:

* the identity policy -- which part of an advert URL is its id;
* curated templates (``templates.json``) for the designs already understood,
  installed by ``rules seed``. Induction still learns designs none matches.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any, ClassVar

from realestate.domain.archive import IdentityPolicy
from realestate.domain.rules import ExtractionSeed, TemplateSeed
from realestate.infrastructure.sources.wayback.source import WaybackDataSource

_TEMPLATES = Path(__file__).with_name("templates.json")

#: Advert URL -> id, per generation. i2-era URLs (``-IDa2hfM.html``) carry a
#: base62 token that is *not* the advert's numeric id, so only numeric ``-ID``
#: forms are trusted; those pages take their id from embedded JSON instead.
OLX_EG_IDENTITY = IdentityPolicy(
    id_patterns=(
        r"-iid-(\d+)",
        r"/iid-(\d+)",
        r"/listing/([0-9a-z]+-listings-[0-9a-f]+)/show",
        r"-ID(\d+)\.html",
    ),
    reject_patterns=(
        r"-cat-\d+(?:-p-\d+)?(?:-ig)?/?(?:\?|$)",
        r"/search/?(?:\?|$)",
        r"/sitemap/",
        r"/(?:login|register|post-classifieds|myolx)",
    ),
)


@cache
def _load_seed() -> ExtractionSeed:
    data: dict[str, Any] = json.loads(_TEMPLATES.read_text(encoding="utf-8"))
    templates = tuple(
        TemplateSeed(key=f"olx.{name}", condition=entry["condition"], action=entry["template"])
        for name, entry in data.items()
        if not name.startswith("_") and name != "vocab"
    )
    return ExtractionSeed(templates=templates, vocab=data.get("vocab", {}))


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

    def identity_policy(self) -> IdentityPolicy:
        return OLX_EG_IDENTITY

    def extraction_seed(self) -> ExtractionSeed:
        return _load_seed()
