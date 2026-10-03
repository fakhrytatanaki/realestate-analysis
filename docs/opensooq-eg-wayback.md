# OpenSooq Egypt archive

[Guide index](README.md) · [Sources](sources.md)

`opensooq_eg_wayback` covers the Egypt host `eg.opensooq.com`, with configurable
years defaulting to 2008–2026. It is disabled for scheduled ingestion and uses
the existing archive frontier, replay provenance, observations and rule graphs.

The homepage is not the complete archive entry point. Older captures redirect
to `/view/`; later captures redirect to `/ar`. The CDX index is queried with
`url=eg.opensooq.com`, `matchType=domain`, per year, so successful captures of
both destinations and their child/property URLs are enumerated directly. HTTP,
HTTPS, explicit port 80, and trailing-slash variants use the shared canonical
URL key; `/`, `/view/`, and `/ar/` remain distinct paths. Redirect rows filtered
out by `statuscode:200` are therefore not required to discover their destinations.
The shared replay client follows in-host archive redirects and records the
served URL/time separately from the request. A capture outside the selected
years is held for review.

Install the reviewed graphs explicitly, then enumerate and crawl:

```bash
cd core
./venv/bin/python -m realestate.cli rules seed --source opensooq_eg_wayback --dry-run
./venv/bin/python -m realestate.cli rules seed --source opensooq_eg_wayback
./venv/bin/python -m realestate.cli archive enumerate --source opensooq_eg_wayback --from 2008 --to 2009
./venv/bin/python -m realestate.cli crawl --source opensooq_eg_wayback --max-fetches 20 --max-llm-calls 0
```

For a pilot limited to 2008–2009, also set `params.from_year` and `to_year` in
local source settings: the explicit enumeration flags do not change the source's
scope for subsequent crawl commands. Seeding is offline and retains active
operator graphs; it never overwrites another source's rules or frontier.

The first reviewed templates support:

- The 2008 Windows-1256 property sale/rent tables. The observed category codes
  are `sc2id=1` for property sales and `sc2id=14` for rentals. Both URL and each
  row's property-category link must agree. IDs, titles, cities and publication
  dates are captured; these lists have no price column, so amounts remain unknown.
- The observed 2025 Arabic apartment sale/rent HTML cards, under
  `/ar/عقارات/شقق-للبيع` and `/ar/عقارات/شقق-للايجار`. Literal and percent-encoded
  URLs match. Cards supply amounts, area, location, numeric room counts and
  capture-relative dates. Explicit rental periods are read from card metadata;
  otherwise the amount's basis remains unknown. Existing placeholder thresholds
  apply; titles cannot supply missing prices.
- The observed `/view/` and `/ar` portals, recognised as `OTHER` pages. They
  yield property-category links without turning mixed homepage adverts into
  property observations. `OTHER` extraction templates now honour explicit link
  recipes; templates without link recipes still yield no links.

Advert identity is a numeric `/search/<id>/` or `/ar/search/<id>[/slug]` URL
on the Egypt host. Homepage, city, search-category, pagination and foreign-host
numbers cannot supply an identity. Detail URLs are deferred until a recognised
property list supplies link evidence. Detail extraction and unverified list
designs remain gaps for later capture-backed rules or induction.

Investigation evidence includes the [2008 portal](https://web.archive.org/web/20080326171937id_/http://eg.opensooq.com:80/view/),
[2008 sale list](https://web.archive.org/web/20080327153723id_/http://eg.opensooq.com/search/?sc2id=1),
[2008 rental list](https://web.archive.org/web/20080327153744id_/http://eg.opensooq.com/search/?sc2id=14),
and [2016 portal](https://web.archive.org/web/20160115172713id_/https://eg.opensooq.com/ar).
Requesting the 2016 apartment sales path returned a capture from 2025-07-14;
the default parser uses that served date. See the
[fixture notes](../core/tests/fixtures/wayback_opensooq_eg/README.md) for retained
evidence and limitations. No full production backfill has been run.
