# Dubizzle Egypt historical source: investigation and implementation plan

[Guide index](README.md) · [Existing archive pipeline](wayback-crawler-state-machine.md) · [OLX quality assessment](wayback-performance-assessment.md)

Investigation date: **2026-10-03**. Status: **initial implementation in progress;
no pilot ingestion or database changes made**.

Implemented starting slice:

- Sanitized compressed fixtures for all six investigated responses, recorded
  expected IDs/amounts/types/areas and capture times, and explicitly synthetic
  rejection/mixed-category/large-payload cases. See the
  [fixture evidence notes](../core/tests/fixtures/wayback_dubizzle_eg/README.md).
- Separate registered `dubizzle_eg_wayback` source, 2023–2026 defaults, disabled
  example configuration, and explicit idempotent `rules seed` installation.
- Reviewed navigation plus complete category JSON-list rules. Minimal opt-in
  template support filters per-item taxonomy, requires identity fields, preserves
  unknown rental basis and disables title-price substitution. Subtype codes remain
  `OTHER`; raw codes and down payment are retained without asserting their meaning.
- Fixed shared Wayback extraction caching to retain the payload object, preventing
  reused Python object IDs from returning another document's extraction result.
- Added 2023 JSON-detail rules with URL/ID agreement, per-level category/location
  selection, valid coordinates and explicit daily-period text independent of amount.
- Added the observed 2023 English sales-card fallback: all 45 fixture IDs, prices
  and areas match. Decoded list arrays always take precedence, including empty or
  rejected arrays; broader HTML layouts still need evidence.
- Added a narrow explicit empty-result guard, malformed-JSON and rejection
  diagnostics in structured source logs, and advert-first induction summaries
  with contact/runtime redaction in the prompt copy. Existing OLX/live behavior
  passes regression checks; parsing remains offline and graph-pinned.
- Hardened shared replay collection: validate every Wayback/original-host redirect
  before requesting it, throttle each hop, retain requested/served URLs and times,
  record timestamp proof and drift, and hold out-of-scope served years as failed
  frontier entries. Missing served-time proof cannot fall back to the requested
  timestamp. Existing raw metadata is not retroactively corrected.
  OLX's documented city-host layouts retain the requested in-domain subdomain;
  Dubizzle permits only its configured apex/`www` hosts.
- Added bounded-pilot controls: failed capture attempts consume the fetch budget
  across rounds and increment run errors; crawl output separates attempts,
  failures and archived payloads. An explicit enumeration-completion gate holds
  the crawl before parsing/routing/induction/fetching if any configured year is
  unfinished. These controls are tested with mocked HTTP; no pilot was run.

The step 1 evidence gate remains open for a complete 2023 sales capture, a real
matching list/detail pair, period/payment examples and broader field auditing.
Step 2's source/seed infrastructure is implemented. Step 3 now handles the
observed detail and truncated-list shapes, but its acceptance gate remains open:
independent held-out captures, validated subtype/period/payment codes and broader
field auditing are still required. Replay-provenance hardening before step 4 is
implemented and covered by mocked-HTTP tests. Pilot budget and enumeration
controls are implemented; fixture evidence and field audits remain prerequisites.
No bounded pilot has been run.

Integration with `main` preserves its crawl reliability and quality machinery:
atomic claims and archive acknowledgements, retry backoff, domain/year cursors,
per-year capture allocation, shared rate gates, parse reports, full observation
snapshots and audited curated-rule updates. Dubizzle's initial graph installation
remains separate and retains active graphs; both workflows use `rules seed`.
Known city aliases use the shared canonical names (for example `Alexandria`),
with captured city/district labels retained in `_raw`. The existing migration
sequence and operational scripts are unchanged by the Dubizzle integration.

## Recommendation

Add **`dubizzle_eg_wayback`**, a separate archive source for `dubizzle.com.eg`
from 2023 onward. Reuse `WaybackDataSource`, CDX enumeration, the frontier,
versioned rule graphs, raw-document storage, and historical observations.
Keep `dubizzle_eg` for live collection and `olx_eg_wayback` for the old domain.

The embedded-JSON hypothesis is confirmed: complete archived list pages contain
`window.state.algolia.content.hits`; an inspected detail page contains
`window.state.ad.data`. These values exist in the captured HTML response and can
be decoded without executing JavaScript or requesting the live API. The existing
`script_json()` reader already understands them.

Registration alone is insufficient. The initial implementation also needs
validated extraction rules, accurate price-basis handling, and an HTML fallback
for truncated captures. Start with a bounded 2023 pilot, then expand through
2024–2026 after checking coverage and extraction quality.

## Investigation and evidence

### Method and limits

Used Firefox MCP on the existing Xorg server; both the MCP process and its
Firefox process had `DISPLAY=:0`. Opened the supplied Wayback calendar, queried
CDX from that origin, and fetched exact `id_` replay URLs as text. Parsed that
text offline using this checkout's `script_json()` and `run_template()` with
the existing project virtual environment. A normal Firefox replay provided a
visual/text cross-check of the 2023 rental cards, including price and area.

Five capture responses were inspected through Firefox; a sixth, from 2026, was
read with HTTPX. HTTPX independently reproduced the truncated 2023 response.
All six returned HTTP 200 and matching `Memento-Datetime` timestamps. This is a
small feasibility sample, **not a domain-wide coverage census or field-accuracy
evaluation**. It does not establish when every template changed.

Downloaded investigation responses remain outside the repository, under the
Firefox MCP output directory and `/tmp`. They are unsanitized research material,
not committed test fixtures. No ingestion, induction calls or production database
writes were performed. No live API response was used as historical evidence.

The [supplied domain calendar](https://web.archive.org/web/20260000000000*/dubizzle.com.eg)
reported 286 saves from May 15, 2023 through September 24, 2026. That is the
calendar's root-URL history, **not** a count of property pages or adverts.
The earliest root capture does not prove the earliest capture of every URL.

### Captures inspected

Sizes below are UTF-8 response-text sizes for Firefox samples and HTTP response
bytes for the 2026 sample, not compressed transfer sizes.

| Capture, UTC | Page | Evidence in captured response |
|---|---|---|
| [2023-05-21 20:11:25](https://web.archive.org/web/20230521201125id_/https://www.dubizzle.com.eg/properties/apartments-duplex-for-rent/) | Arabic apartment rentals | 1,205,733 bytes; complete `state`; 45 hits with 45 distinct external IDs |
| [2023-06-07 09:59:54](https://web.archive.org/web/20230607095954id_/https://www.dubizzle.com.eg/ad/اجمل-الاماكن-بخالد-ابن-الوليد-شقة-مفروشة-للايجار-اليومي-ID196521164.html) | Arabic rental detail, ID `196521164` | 749,264 bytes; `state.ad.data` plus Product JSON-LD |
| [2023-09-30 15:02:02](https://web.archive.org/web/20230930150202id_/https://www.dubizzle.com.eg/en/properties/apartments-duplex-for-sale/) | English apartment sales | Exactly 1,048,576 bytes; cuts off inside `window.state`; invalid JSON, but 45 distinct advert links remain in HTML |
| [2024-04-07 13:47:53](https://web.archive.org/web/20240407134753id_/https://www.dubizzle.com.eg/en/properties/apartments-duplex-for-sale/) | English apartment sales | 1,537,838 bytes; complete `state`; 50 hits with 50 distinct IDs |
| [2025-01-14 15:54:21](https://web.archive.org/web/20250114155421id_/https://www.dubizzle.com.eg/en/properties/apartments-duplex-for-sale/) | English apartment sales | 1,613,251 bytes; complete `state`; 45 hits with 45 distinct IDs |
| [2026-01-20 15:52:34](https://web.archive.org/web/20260120155234id_/https://www.dubizzle.com.eg/en/properties/apartments-duplex-for-sale/) | English apartment sales | 5,167,456 bytes; complete `state`; 45 hits with 45 distinct IDs |

The truncated response was identical through Firefox and HTTPX, lacked closing
HTML, and ended inside a JSON string. The cause of truncation was not established.
HTTP 200 and CDX `mimetype:text/html` therefore do not establish parseability.

### Confirmed field semantics and traps

The 2023 rental hit with `externalID="196804372"` contains:

```json
{
  "price": 0,
  "purpose": "for-sale",
  "extraFields": {"price": 2000, "ft": 200, "rooms": "3", "bathrooms": "3"},
  "category.lvl1": {"slug": "apartments-duplex-for-rent"},
  "createdAt": 1684487874.059311
}
```

The captured cards show EGP 2,000 and 200 sqm. All 45 hits in that capture have
top-level `price=0`, `purpose="for-sale"`, and no `extraFields.rental_period`.
The complete sales samples also have zero top-level prices. Consequently:

- Read the amount from **`extraFields.price`**, not top-level `price`.
- Resolve sale/rent from the advert's property category before `purpose` or page
  context. A rental page is not sufficient evidence that every possible promoted
  or recommended item is a rental; validate each candidate's category.
- Do not default a missing rental period to monthly. The first rental title
  describes short stays by the day; the detail sample explicitly says daily.
- `extraFields.ft` is the area field; its name does not imply square feet. The
  inspected rental card labels 200 as sqm. Verify units on further categories.
- Detail `ad.data` has category and location **arrays**, whereas list hits also
  have dotted keys such as `category.lvl1` and `location.lvl2`. Select array
  members by `level`, not an assumed position.
- Product JSON-LD on the inspected rental detail reports an Offer price of zero
  and a selling business function. Its `sku` matches the advert ID, but JSON-LD
  is not an authoritative price or sale/rent fallback for this template.
- `createdAt` is fractional Unix seconds. It is the source's advert date;
  `Memento-Datetime` is the observation date. Keep both.

No cross-domain OLX/Dubizzle identity continuity was proven. Identical numeric
IDs are useful evidence for later reconciliation, not sufficient grounds for
merging source histories now.

### Offline compatibility check

A temporary, uncommitted template used `items.script="state"`,
`items.path="algolia.content.hits"`, `externalID`, category slugs,
`extraFields`, location dotted keys, and `createdAt`. Existing `run_template()`
produced **45 / 50 / 45 drafts** for the complete 2023 / 2024 / 2025 list samples,
with no item problems, capture-derived `observed_at`, and `is_active=false`.

This proves structural compatibility, not a production-ready mapping. The
temporary template simplified property types and generated Arabic advert paths;
it was not a gold-set evaluation. It also exposed an existing behavior:
`parse_price()` assigns `PER_MONTH` to a numeric rental amount with no period.
That is unsafe for this source. The truncated response exposed no decoded state;
running the JSON template directly produced zero items, while its HTML still
contained adverts. The production graph must fall back or report an extraction
gap, never interpret that as confirmed empty inventory.

## Fit with the current implementation

Use executable code as the baseline; the earlier historical-source proposal
contains planned behavior that differs from the implemented system.

| Existing component | Reuse and relevant limitation |
|---|---|
| `infrastructure/sources/wayback/source.py` | Frontier-backed collection, captured metadata, graph pinning, offline parsing and discovery already exist. |
| `infrastructure/archive/wayback.py` | CDX uses `matchType=domain`, HTML/200 filters and resume keys. Replay uses `id_` and reads the served timestamp. Delay/lock are per client instance. |
| `infrastructure/sources/olx_eg_wayback/source.py` | A small domain/year subclass is the model for the new source. Do not repoint this existing key to the new domain. |
| `infrastructure/sources/defaults.py` | Register the new subclass with frontier, graph repository and `HtmlRuleEngine`, following OLX's existing factory. |
| `infrastructure/extraction/jsondata.py` | Already decodes literal assignments without `eval`, supports dotted keys and array paths. Does not expose malformed-state diagnostics or select array objects by field value. |
| `infrastructure/extraction/template.py` | Already maps JSON items to drafts and builds URLs with field interpolation. Needs optional structured price/location handling for this source's semantics. |
| `infrastructure/extraction/engine.py` | Chooses the richest successful template; edge priority only breaks ties. Ordering a JSON rule first does not guarantee it wins over HTML. |
| `application/rules/seeds.py` | Only seeds generic link-evidence navigation. Dubizzle installs reviewed initial graphs without replacing active graphs; OLX can audit and update curated extraction templates. |
| `application/services/archive_crawl_service.py` | Enumeration, routing, budgeted crawling and induction already work by source key. Automatic enumeration resumes every unfinished domain/year cursor. Capture caps allocate each year separately and queued fetches rotate by quarter. |
| Listing repository and observations | Archive writes preserve observation ordering. Identity is `(source_key, external_id)`; observations store each capture's complete normalized snapshot and extraction provenance; aggregate rows can still combine partial captures. |

The live `dubizzle_eg/source.py` mapping is useful reference material, but cannot
be reused wholesale: it expects Elasticsearch `_source`, defaults missing rental
periods to monthly, reads dotted locations, and generates English URLs. Extract
small pure helpers only where fixture evidence proves equal semantics; preserve
the live adapter's tested behavior while introducing archive-specific policies.

## Proposed design

### Source and configuration

Create `infrastructure/sources/dubizzle_eg_wayback/{__init__,source}.py` with
`DubizzleEgWaybackDataSource(WaybackDataSource)`:

- Key `dubizzle_eg_wayback`, display name `Dubizzle Egypt (archived)`, country `EG`.
- Domain `dubizzle.com.eg`; default years 2023–2026 for this implementation.
  Keep the existing explicit `from_year`/`to_year` overrides. Extending the end
  year is an operational choice, not a claim of complete coverage.
- Disabled scheduling by default; run bounded archive jobs manually.
- No live endpoint fallback during either fetch or parse. Firefox is an
  investigation tool, not a runtime dependency.

Proposed example settings:

```toml
[sources.dubizzle_eg_wayback]
enabled = false
params = { from_year = 2023, to_year = 2026, min_delay_seconds = 1.5, user_agent = "realestatepy-archive-research/0.1 (contact: you@example.com)" }
```

A new source does not require a new listing table or a migration. Source keys
already isolate frontier rows, cursors, rules, raw documents and listing identity.
Avoid introducing a new source-kind model just for this integration;
`ArchiveDataSource` and capture metadata already provide the needed distinction.

### Discovery and capture selection

1. Enumerate explicitly by year using the existing CDX client. Its domain match
   covers apex/`www` and subdomains; route only the allowed site hosts. Keep
   Arabic `/properties/…` and English `/en/properties/…`, including city paths
   and recorded `?page=N` captures. Preserve original URLs and CDX timestamps.
2. Prefer property-category pages; inspect category identity before extracting
   adverts. Defer generic `/ad/…-ID<n>.html` URLs until a recognized property
   page links to them or independently validated property evidence exists.
   Skip assets, account flows and unrelated marketplace categories.
3. Discover detail and pagination links from captured bytes. Match them to
   existing frontier rows. Unmatched links can receive budgeted exact-URL CDX lookups via the shared
   link-request queue; keep `--max-link-lookups 0` for a fixed enumerated pilot.
4. Preserve `?page=` during URL normalization. Do not invent captures for every
   `nbPages` value or call today's API to fill missing historic pages.
5. Enumerate the entire selected pilot scope before routing. Capture selection
   now allocates per year, and fetch claims rotate by quarter. Evaluate the
   resulting distribution before claiming multi-year coverage; routing can
   explicitly reopen prior `#capture-cap` skips with `--reopen-capped`.
6. Retain existing backoff, atomic claims and archive acknowledgements. Both
   sources share the configured IA rate gate, including every replay redirect
   hop. Serial pilot jobs still make the reviewed selection easier to audit.

Replay provenance hardening is implemented before the pilot: preserve both
requested and served original URLs/timestamps, validate every redirect before
requesting it, and reject unexpected original hosts and rewritten replay modes.
Nearest-capture drift and timestamp proof are recorded. Captures served outside
the configured source year range are held as failed frontier entries for review,
with no raw payload or observation emitted. An aware Memento header takes
precedence over the full final URL timestamp; both are retained when available.
Neither a short URL prefix nor a missing header can justify silently assigning
the requested capture time. See the [source metadata contract](sources.md).

For investigation, use explicit CDX `matchType=prefix` with paths, for example:

```text
https://web.archive.org/cdx/search/cdx?url=www.dubizzle.com.eg/properties/&matchType=prefix&from=2023&to=2023&filter=statuscode:200&filter=mimetype:text/html&output=json&fl=timestamp,original,digest&limit=12
```

Do not assume `*.dubizzle.com.eg/properties/*` constrains the path: the initial
wildcard research query returned unrelated domain paths. The production client
already enumerates the domain deliberately and routes afterward. Research
`collapse=urlkey` queries select examples, not observation history; do not copy
that collapse into production enumeration.

### Versioned extraction rules

Keep the graph engine as the single parsing path. Supply reviewed initial
navigation/extraction graph JSON for this source, including vocabulary, instead
of requiring an LLM to rediscover these confirmed layouts on the first run.
This is a deliberate extension of the current induction-only startup policy.

Add an explicit **`rules seed --source dubizzle_eg_wayback`** operation (new, not
currently available). It should validate the packaged graphs, install each
domain's version 1 through `RuleGraphRepository.save_version()` only when that
domain has no active graph, and be idempotent. It must not replace an operator's
existing rules. Keep loading/validation in infrastructure and persistence
orchestration behind application services/ports, wired by `bootstrap.py`.
Include the generic link-evidence navigation rule. This provides a reproducible
zero-LLM pilot while leaving induction available for unknown layouts later.

Implement these extraction paths:

| Path | Selection and behavior |
|---|---|
| Complete JSON list | `state.algolia.content.hits`; filter each hit by property taxonomy, deduplicate `externalID`, and map fields deterministically. |
| Complete JSON detail | `state.ad.data`; require property taxonomy and agreement between its external ID and the advert URL. Do not ingest recommendations as the main advert. |
| HTML list fallback | When the relevant JSON payload is absent, malformed or unusable, use captured listing cards; observed selector `li[aria-label="Listing"]` and item-scoped `/ad/` links are starting evidence, not universal selectors. |
| HTML detail fallback | Only after obtaining and validating a matching fixture; prefer canonical/detail URL identity and labelled fields. JSON-LD may corroborate identity/currency but not override the observed price/category conflict. |
| Unknown/challenge/broken response | Produce an extraction gap or explicit failure, rather than an `OTHER` success that silently drops property adverts. |

Make JSON and HTML fallback guards mutually exclusive based on usable payloads,
or add an explicit validated fallback mechanism. Do not rely on edge order:
the current engine's richness scoring can select HTML even when JSON is correct.
Distinguish genuine empty search results with an explicit empty-state signal
from missing or broken data. Use `json.JSONDecoder`; do not execute captured
scripts, call `eval`, or “repair” truncated JSON by appending invented values.

For the first pilot, prioritize complete category JSON plus the observed
truncated-list HTML fallback. Unknown detail variants may remain gaps until
fixtures establish their mapping; they must not block useful list observations.

### Mapping contract

| Normalized value | Archive mapping and precedence |
|---|---|
| `external_id` | Numeric `externalID` preserved as a string; source-key isolation supplies the namespace. Same ID for list/detail and language variants. Require URL-ID agreement on detail pages; never hash a homepage as advert identity. |
| `url` | Original advert link or slug plus external ID on the original site's language path; preserve numeric suffix and remove Wayback wrapping. Keep replay URL separately in raw metadata. |
| `title`, `description` | Captured localized fields with tested language fallbacks. Omit contact objects and redact phones/emails from normalized descriptions and fixtures. |
| `listing_type` | Per-item property-category slug first, unambiguous page category second. Conflicting generic `purpose` cannot override those. Skip/quarantine unresolved mixed-category items. |
| `property_type` | Versioned category + `extraFields.type` mappings; validate codes against captured labels. Do not flatten duplex/penthouse/villa/land into apartments. Unknown remains `OTHER`. |
| Price amount | `extraFields.price`, parsed as `Decimal`; retain existing zero/placeholder safeguards. Preserve down payment separately; do not substitute it for the full amount. |
| Currency | Captured currency/label, or EGP only for a validated Egyptian template; unknown otherwise. Zero JSON-LD price does not erase a valid amount. |
| Price basis | Explicit rental-period/payment-option code mapped using validated era-specific vocabulary, then unambiguous captured period text. Missing rental basis stays `UNKNOWN` with the known amount; no assumed month. |
| Area/rooms/bathrooms | `extraFields.ft` / `rooms` / `bathrooms`, with field-specific normalizers and unit evidence. |
| Location | Dotted level fields for hits; select `location[]` by `level` for detail. Preserve the hierarchy; never turn country level 0 into a city. Map `geography.lat/lng` with range checks when present. |
| `listed_at` | Captured `createdAt` fractional Unix seconds, timezone-aware and checked against capture time. No substitution with fetch time. |
| `observed_at`, activity | Served archive capture timestamp; `is_active=false`, regardless of captured `state="active"`. Missing trustworthy capture time must not create an active archive listing. |
| Provenance | Existing original URL, replay URL, CDX digest, served timestamp, raw-document link, and `_extraction` graph/template fields. Keep extraction diagnostics out of business content hashes. |

Extend the generic template DSL only for operations the confirmed mapping
cannot currently express: item filtering by category, selecting array members
by field value, explicit price basis/down payment, and latitude/longitude.
Use small bounded declarative operations, not embedded Python/JavaScript.
Update `KNOWN_FIELDS`, proposal validation, prompt documentation and offline
tests together. Existing templates retain their old default behavior; the new
archive rules opt into conservative rental-period handling. Parse amount and
period separately so description numbers cannot replace the price.

Keep diagnostic outcomes for malformed state, JSON/HTML fallback use,
unknown rental basis, category conflicts, item rejection and missing identity.
The existing parser discards item-problem details; expose them in structured
run logs/reports so a successful run is not mistaken for accurate extraction.

## Implementation sequence and acceptance gates

### 1. Capture fixtures and expected values

Add sanitized fixtures under `core/tests/fixtures/wayback_dubizzle_eg/`, using
the existing compressed-HTML plus `index.json` convention. Include original
URL, requested/served timestamps, capture digest, retrieval date and expected
non-contact fields. Do not commit whole `window.state` blobs containing search
credentials, sessions, contact data or unrelated runtime configuration.

Retain representative script syntax, JSON shapes, card structure and truncation
behavior. Mark synthetic negatives separately. Cover all six inspected cases;
add a complete 2023 sale capture, a list/detail pair for the **same advert**, a
non-property advert, a mixed category page, an empty page, and a challenge page.
Add rental-period and installment examples before claiming those mappings.

**Gate:** hand-labelled expected IDs, amounts, currency, sale/rent, area and
observation timestamps; complete JSON samples decode with the existing reader,
and the truncated sample intentionally does not. Fixtures contain no contacts
or runtime credentials. Large-payload coverage includes the observed >5 MB case.

### 2. Register the source and install initial rules

Add the subclass/factory, disabled example configuration, packaged graph JSON,
and the explicit seed operation described above. Reuse existing DB tables and
archive services. Test archive scope, independent source state, idempotent
seeding, and retention of existing active graphs.

**Gate:** the source appears in registry descriptors; a fake CDX/frontier run
uses `dubizzle.com.eg` and the configured years without altering either existing
source. Do not rely on `crawl` to finish enumeration after partial seeding.

### 3. Complete extraction and fallback semantics

Implement the mapping contract and minimum DSL extensions. Add diagnostics for
invalid embedded JSON without turning unrelated script syntax into fatal errors.
Validate JSON-list, JSON-detail and HTML-list fallback rules on held-out fixtures.
Extend `jsondata.summarise()` for future induction to prioritize advert objects:
currently it ranks arrays by size, and the 2023 sample puts 160 SEO templates
before 45 advert hits. Detail singleton objects also need a useful summary.
Redact contact/runtime fields before any induction prompt.

**Gate:** all labelled IDs/prices/types/timestamps match; no homepage identities;
same advert list/detail records agree; rental-period absence does not become
monthly; malformed JSON with recoverable cards uses the fallback. Repeated
offline replay with a pinned graph produces identical drafts and no network or
LLM calls. Existing OLX and live Dubizzle fixtures remain unchanged in behavior.

### 4. Bounded 2023 pilot

After the new source and seed command exist, configure a 2023-only pilot scope
and run from `core/`:

```bash
./venv/bin/python -m realestate.cli rules seed --source dubizzle_eg_wayback
./venv/bin/python -m realestate.cli archive enumerate --source dubizzle_eg_wayback --from-year 2023 --to-year 2023
./venv/bin/python -m realestate.cli archive route --source dubizzle_eg_wayback
./venv/bin/python -m realestate.cli crawl --source dubizzle_eg_wayback --max-fetches 50 --max-llm-calls 0 --max-link-lookups 0 --require-complete-enumeration
./venv/bin/python -m realestate.cli archive status --source dubizzle_eg_wayback
```

These are implementation-runbook commands, **not commands executed during this
investigation**. Seeding and the crawl controls are now implemented. Enumeration is metadata-only
but may require many pages; resume it to completion before routing the pilot.
Curate the pilot selection across available months, languages, sale/rent, cities
and list/detail types; the current priority/time ordering does not guarantee that
distribution from a 50-fetch budget by itself.

The pilot limit counts selected capture attempts, including failed fetches and
provenance rejections. A selected capture can involve multiple throttled HTTP
redirects/retries. Provenance rejections and permanent failures remain failed for review; transient
failures are requeued with backoff. Unattempted rows stay queued. Handled fetch failures contribute to the run's existing `errors` and
`PARTIAL` status. The completion gate checks configured source years, so keep
the pilot configuration at 2023–2023; enumeration flags alone do not narrow it.
If enumeration is incomplete, the crawl exits with status 2 and reports the
unfinished years. Resume `archive enumerate` explicitly before rerunning.

Report captures and emitted adverts per stratum, JSON/fallback/gap counts,
identity collisions, price/area completeness, unknown rental periods, rejected
non-property items, requested/served timestamp drift and fetch failures.
Audit prices against the actual capture, not a current advert page.

**Gate:** no known identity collisions, no live-data leakage into historic rows,
all observed times trustworthy, and all hand-labelled pilot fields correct.
Record genuine missing fields instead of treating completeness as accuracy.
Test out-of-order list/detail writes and replays against a disposable database.

### 5. Expand coverage deliberately

Enumerate 2024–2026 explicitly. Review the existing temporal capture allocation
and use controlled reopening of prior `#capture-cap` skips before increasing fetch volume. Evaluate
further property categories and template variants separately; use budgeted
induction only for gaps, with held-out regression checks before activation.
After graph changes, explicitly replay affected already-parsed documents as well
as unrecognized ones: `parse --stale` and the crawl's stale-graph replay can
correct successful older parses, subject to their replay limits.

Cross-source reconciliation and archived API responses remain follow-ups.
Observations now store full normalized snapshots with extraction provenance,
and the shared rate gate supports concurrent archive clients. Use per-capture
snapshots for historical area/location analysis; an aggregate listing row can
combine values from partial captures.

## Files and validation for implementation

Primary changes:

- New source package and packaged rule graphs; `sources/defaults.py`.
- `infrastructure/archive/wayback.py` and archive payload metadata for redirect
  validation and requested-versus-served provenance.
- `application` seed-installation orchestration, `cli.py`, `bootstrap.py`, and
  graph loading/validation in infrastructure.
- `extraction/{jsondata,template,normalisers,prompt_view}.py`, with corresponding
  proposal/prompt vocabulary changes where the DSL expands.
- `core/etc/settings.example.toml`; `docs/sources.md`, `docs/modules.md`, and
  archive operations documentation.
- New fixture/source tests and focused additions to extraction, crawl, Wayback
  client and archive-repository tests.

Regression scenarios must include: Arabic/English ID agreement; list/detail ID
agreement; non-property hits; repeated promoted IDs; top-level-zero versus real
price; contradictory `purpose`; absent rental period; daily rental; installment
amount/down payment; missing/invalid state; valid empty results; cross-host and
nearest-capture redirects; lost capture metadata; pagination normalization;
independent source cursors; source-scoped rules; out-of-order observations; and
repeat seeding/replay. A replay redirect must stay on Wayback and preserve the
served original URL as well as capture time; reject any live-site escape.

Run focused tests during implementation, then the repository-required checks:

```bash
cd core
./venv/bin/pytest
./venv/bin/ruff check src tests
./venv/bin/mypy
```

Use `REALESTATE_TEST_DB_URL` only for a disposable PostgreSQL database and report
integration skips. No code test suite was run for this documentation-only change;
the investigation used the offline compatibility experiments described above.

## Remaining evidence gaps

- Full capture counts and useful-property coverage by year/month/category/host;
  bounded CDX samples cannot answer this.
- A complete 2023 sales payload and more 2023 detail layouts; the inspected
  English sales sample was truncated.
- Proven list/detail identity agreement for the same captured advert, and any
  OLX-to-Dubizzle ID continuity across the rename.
- Rental-period/payment-option code stability, detail array-level mapping across
  eras, category code drift and currency exceptions.
- HTML fallback field accuracy on the truncated sample; finding 45 links does
  not prove recovery of all fields from all 45 cards.
- Availability of archived API response bodies. They were not investigated and
  are not needed for the demonstrated HTML-embedded JSON path.
