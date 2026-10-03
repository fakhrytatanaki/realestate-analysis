# Wayback OLX performance and data-quality assessment

[Guide index](README.md) · [State machine](wayback-crawler-state-machine.md) · [Historical plan](historical-sources-plan.md)

Assessment date: **2026-10-03**. Code revision: `036bd40` (clean working tree
before this documentation change). Source: `olx_eg_wayback`.

## Finding

The collection and replay foundation works, but the current database is **not yet
a reliable sample across historical periods**. It contains only 2013 captures,
and successful parsing hides substantial extraction loss and incorrect identity
merging. Fix identity and extraction validation before increasing crawl volume.
The saved HTML makes most of the observed damage recoverable without another
Wayback request.

The most consequential findings are:

- **149 distinct detail-advert IDs collapse into nine homepage-based identities.**
  These rows combine observations and, in some cases, fields from unrelated ads.
- **382 current listings have a recoverable numeric price in their own linked raw
  document but a null database price.** An extraction regex captures the dot in
  `ج.م` instead of the amount.
- **238 current listings have an explicit `Square Meters:` value in their linked
  raw item, but all 1,182 database listings have null area.**
- Enumeration is complete for **2013 only**. The default 2010–2023 range does not
  mean those other years have been collected.
- All 340 documents are `PARSED`, yet current-rule replay classifies 70 as
  `OTHER`, skips 35 candidate items, and exposes results absent from the database.

These are measurements of this deployment and its stored rules, not an estimate
of all OLX captures available from the Internet Archive.

## Scope and method

Read the configured PostgreSQL database `realestate` on `127.0.0.1:5432`, with
server-enforced read-only connections and a repeatable-read transaction. The
assessment snapshot was **2026-10-03 06:32:25.975252 UTC**. This is the live
database configured in this workspace; no separate remote production database
was assumed. The source is disabled for scheduling, which still permits manual
backfill commands.

Inspected all source rows in the frontier, cursors, raw documents, listings,
observations, graphs, nodes, edges, gaps and runs, plus the decision ledger and
local application log. Read all 340 referenced local blobs and verified their
recorded byte lengths and SHA-256 hashes: **340/340 matched**, with 340 distinct
hashes. No archive fetch, induction, ingestion, reparse write, or database repair
was performed for this assessment.

Reconstructed the active extraction graph, version 8, from stored nodes/edges in
position order. Called `HtmlRuleEngine.extract()` directly on each archived
document with its saved capture metadata and `country_code="EG"`. This bypasses
database writes and preserves the exact active rules. Compared the resulting
drafts with the snapshot and independently inspected item HTML. For recoverable
prices and areas, required that the item ID match the listing **in that listing's
current `raw_document_id`**, not merely on some other capture.

The audit is exhaustive for counts, blob integrity and active-graph replay, but
it is **not a hand-labelled accuracy evaluation of every field**. Presence is
not correctness; the `OTHER` decisions were not all independently adjudicated.
The CPU timing below is one local pass, not a production benchmark. Snapshot
exports and raw HTML were kept out of version control; examples below contain
only advert IDs, document IDs and relevant non-contact fields.

## Coverage and throughput

| Measure | Observed value | Interpretation |
|---|---:|---|
| Frontier captures | 5,311 | All timestamps in 2013; includes non-property URLs |
| Enumeration cursors | `cdx:2013`, `done=true` | No evidence that other years were enumerated |
| Fetched documents | 340; 18,585,881 bytes (17.7 MiB) | 6.4% of this frontier, **not** property-capture recall |
| Final listing rows | 1,182 | Includes nine incorrect merged detail identities |
| Observation rows | 1,938 | 871 listings have only one observation; collisions inflate some histories |
| Observation range | 2013-01-01 through 2013-12-20 | Twelve months, one year |
| Runs | 12 `SUCCESS`, zero recorded errors | Two fetched nothing; success does not imply useful or accurate output |
| Requested/served capture timestamp mismatches | 0/340 | Correct served timestamps in this sample; redirects remain a general concern |

Capture-month distribution is uneven:

| 2013 month | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Raw documents | 34 | 50 | 12 | 25 | 17 | 15 | 5 | 7 | 16 | 145 | 13 | 1 |
| Observation rows | 226 | 79 | 101 | 113 | 107 | 16 | 62 | 111 | 60 | 853 | 181 | 29 |

October contributes 42.6% of documents and 44.0% of observations. Observation
counts are neither independent properties nor a representative market sample:
list pages repeat adverts, detail identity collisions create false histories,
and captures reflect archival availability and crawler selection.

The 10 nonempty ingestion runs consumed **798.6 seconds**, or approximately
**1,533 archived documents/hour** over their combined execution time. Seven
logged crawl intervals total 969.5 seconds, approximately 1,263 documents/hour;
that includes more orchestration/induction work but excludes gaps between manual
commands and initial enumeration. Neither rate is a sustained service guarantee.

All 340 replay HTTP log entries report status 200 on attempt one. Median request
latency was 730.5 ms, p95 1,550 ms, maximum 6,155 ms; there were no replay retry or
rate-limit events in the inspected log window. The configured default delay is
1.5 seconds **after the previous request finishes**, which explains much of the
observed pace. There is no evidence here that throttling is the current bottleneck.

Offline extraction of all blobs took **2.23 seconds** including reads, integrity
checks and result serialization. Per-document extraction median/p95/max was
3.1/19.9/36.6 ms. Improving CPU parallelism has much lower immediate value than
repairing semantics and choosing more useful captures.

## Raw documents versus final database

### Completeness

| Final field | Present / 1,182 | Share | Qualification |
|---|---:|---:|---|
| Numeric price | 293 | 24.8% | At least 382 additional nulls have parseable raw prices |
| Area | 0 | 0% | Explicit raw area labels found for 238 current rows |
| Bedrooms | 232 | 19.6% | Some merged detail rows inherit unrelated values |
| Bathrooms | 210 | 17.8% | Same identity/provenance problem |
| Description | 9 | 0.8% | Exactly the nine collapsed detail identities |
| City | 967 | 81.8% | Includes country names and truncated region names |
| District / latitude / longitude | 0 each | 0% | No demonstrated spatial detail coverage |
| Listed date | 1,173 | 99.2% | Often yearless text inferred relative to capture time |
| Specific property type | 722 | 61.1% | Remaining 460 are `OTHER`; current-rule replay changes many types |

Currency counts are EGP 779, XXX 264, GBP 68, USD 50 and EUR 21. Numeric amounts
must be analysed with currency and price basis; do not pool these into a single
historical price distribution. All 1,182 archive rows correctly have
`is_active=false`.

### Concrete discrepancies

| Raw evidence | Stored result | Cause and significance |
|---|---|---|
| Document `3aefeb77-f183-4940-8c39-ce0f9dc4e017`, capture `20130821130341`, item `537756309`: price text `ج.م125,000` | `price=NULL`, `currency=EGP`, `_raw.price="."` | List template `tpl.v4.olx_eg_real_estate_list_2013` uses `([\d,.]+)`, which matches the currency punctuation first. Passing the complete text to the existing normalizer returns 125000.00 EGP. |
| Document `9211b08d-3654-4051-8a71-6197bd6121e8`, capture `20130320012104`, item `439216534`: `Square Meters: 645` | `area_sqm=NULL` | The v3 template expects digits followed by `m2`; the dominant v4 template omits area altogether. |
| Document `a0ad1086-1422-4e61-92d3-6b564bd87646`: original URL contains `iid-535715253`; `.share-url` contains that advert URL | External ID `u:94ce1501851436f07ed1`, URL `http://www.olx.com.eg` | v6 reads `#headerlogolink`, fails its ID recipes, then hashes the homepage URL. Its 123 stored observations belong to 107 different original advert IDs. |
| Document `54e51d6e-6b40-45d7-855d-91b0c0f2253c`: land advert `iid-309286367`, no bedroom/bathroom definition rows | Merged Cairo-homepage listing has 2 bedrooms, 3 bathrooms | Gap-filling/carry-forward preserves fields from other adverts that collided under the same identity. |
| Raw location `Houses - Apartments for Sale - al-Bah̨r-al-Ah̨mar` | City such as `Ah̨mar`, category retained in `location_name` | Splitting at the last hyphen truncates a transliterated governorate; the location also contains taxonomy. |

There are **616 rows with `_raw.price="."`**; six still have numeric prices, so
this count is not identical to lost prices (title fallback can supply a value).
Across active-rule replay, full price-cell text recovers 567 missing-price draft
occurrences across 393 IDs. The stricter **382** figure counts current database
nulls recoverable from their current provenance document. This establishes
extraction loss; it does not certify that every seller's amount is a realistic
total asking price. Placeholder values such as `ج.م2.00` should remain unknown.

The 173 detail documents recognized as adverts contain **149 distinct numeric
`iid` values but emit nine distinct external IDs**, all based on homepage URLs.
Of those 149 real IDs, 134 already occur among list-derived database rows, while
15 do not. Correct identity would therefore chiefly enrich existing adverts and
separate false histories; it would not simply add 149 new listings.

The competing v8 detail template does not repair this: its ID regex is also
overescaped in the stored recipe, and v6 wins every detail selection in this
replay. Selecting the “richest” template rewards populated fields without proving
that its URL, ID or field values are correct. Template validation does not test
cross-document ID uniqueness, list/detail identity agreement, or homepage URLs.

Location needs a separate validity measure. There are 142 rows with city
`Egypt`, 75 with city `Ah̨mar`, and 29 with `Qāhirah`. Ten titles contain explicit
foreign-location signals such as Cyprus/Larnaca while `country_code` is EG.
These are candidates for geographic review, not a validated count of foreign
properties: the source's country is currently assigned to every draft.

### Replay and historical representation

Active v8 replay produces 97 `LIST`, 173 `DETAIL` and 70 `OTHER` outcomes. Of the
153 list-hinted captures, 56 produce `OTHER`; 14 of 187 detail-hinted captures do
likewise. The winning templates examine 2,013 items and emit 1,978 drafts, with
35 reported item problems. These are engine counts, not gold-set recall.

Replay emits 1,197 unique IDs versus 1,182 stored IDs, including 15 IDs missing
from the database. On matching ID/current-document pairs, **230 property types
differ** between the active graph and stored values. Four documents with no
stored observations now produce listings. Rule upgrades automatically replay
`UNRECOGNISED` documents, but not already `PARSED` documents, so older successful
results retain earlier interpretations. A graph upgrade is not a completed data
migration. The 1,978 draft occurrences and 1,938 persisted observations also have
different deduplication/version semantics and should not be subtracted as a
definitive lost-ad count.

The repository correctly appends historical price observations and prevents
older captures from replacing the latest main values. However:

- `listing_observation` stores price, currency, price type, content hash and raw
  provenance, **not a full historical title/location/area/rooms/description**.
  Joining old prices to today's listing fields can create a false historical row.
- Its unique key is `(listing_id, observed_at)`. Re-extraction replaces the
  interpretation at that moment rather than preserving all parser revisions.
- Missing-field carry-forward and older-document gap-filling create a composite
  listing. A single `raw_document_id` cannot describe each field's origin.
  The stored content hash is computed from the incoming draft before these merges;
  it need not describe every persisted merged field.
- A corrected-ID replay adds proper rows but does not remove the old homepage
  rows or reassign their polluted observations. Repair needs explicit rebuilding
  or reconciliation, not just another ordinary `reparse` command.

## State-machine assessment

The implementation has two versioned decision graphs plus persistent workflow
statuses. This is useful separation: parsing remains deterministic and offline,
and link evidence can unlock deferred details. The failure is that **workflow
completion is being treated as a proxy for data quality**.

| Frontier status | Count | Assessment |
|---|---:|---|
| `FETCHED` | 340 | Every one has a raw document in this snapshot |
| `QUEUED` | 186 | Remaining selected work |
| `DISCOVERED` | 8 | Awaiting routing |
| `UNROUTED` | 1,151 | No accepted route; repeatedly retried by routing |
| `DEFERRED` | 2,306 | Awaiting evidence; not proof these are property pages |
| `SKIPPED` | 1,320 | 49 marked `#capture-cap`; the remainder have other skip reasons |
| `FAILED` | 0 | All frontier attempt counters are zero; successful attempts are not counted |

The active navigation graph is v17 with 175 route nodes; extraction is v8 with
eight templates. Navigation has 187 open, 89 resolved and three failed gaps.
Extraction has seven open and six resolved gaps despite no currently
`UNRECOGNISED` documents. Gap status is therefore also not a current backlog count.

| Weakness | Evidence / mechanism | Consequence |
|---|---|---|
| Nonempty frontier prevents automatic enumeration | `crawl()` enumerates only when total frontier count is zero | Completing/partially seeding 2013 leaves other years untouched unless explicitly enumerated |
| Capture cap applies across a URL's whole enumerated life | Four list, two detail, one other; already queued/fetched digests consume the cap | Earlier partial enumeration can exhaust slots before later years arrive; increasing the cap does not itself reopen skipped rows |
| Selection spreads by capture index, not calendar bins | `select_captures()` spaces indices in timestamp-sorted distinct digests | Dense months can dominate even a “spread” sample; no year/city/category quota |
| Fetch order is priority, timestamp, ID | Evidence receives priority 75; other learned rules can outrank it | Priority and old timestamps can exhaust a bounded budget before under-covered periods |
| Links only annotate existing frontier rows | Logged link offers: 2,263 links, 903 matched URLs over 196 events | Many offers cannot unlock work; these are repeated events, not 1,360 proven missing unique captures |
| Learning is driven by complete misses | Extraction gap collection scans `UNRECOGNISED` only | Wrong-but-populated outputs and omitted area never trigger induction |
| Automatic activation checks plausibility, not truth | Small-sample validation and no held-out labelled promotion gate | Homepage identity, malformed field recipes and low-quality overlap can pass |
| First navigation hit persists | Graphs append rules; newer rules do not automatically revise old skip/defer decisions | Growth can preserve mistaken earlier routes; no reason-specific re-evaluation policy |
| Status success loses quality information | `OTHER` is successful; unknown templates do not increment run errors; item problems are discarded by source `parse()` | Zero-error runs can produce no listings or incorrect ones |
| Failure recovery lacks a durable claim/ack boundary | No atomic worker lease; `mark_fetched()` follows yield even after a handled archive-write error | Possible duplicates or fetched-without-raw state after failures; not observed in this snapshot |
| Terminal retry states and budgets are incomplete | Frontier failures and failed gaps are not automatically retried; fetch budget counts saved documents | A transient failure can strand work; failed HTTP attempts/time can exceed the apparent budget |

Cursor scope is only `cdx:{year}` within a source key. Changing that source's
domain or enumeration filters should invalidate/version cursors, otherwise an
old completion marker can incorrectly stand for a different enumeration scope.

The decision ledger contains 57 calls in this audit dataset: 16 navigation
answers marked valid and eight valid extraction answers out of 41 (19.5%). All
24 accepted decisions are referenced by this source's graph nodes. The ledger
has no source key and stores message roles/lengths rather than full prompts;
rejected-call attribution and exact prompt reconstruction are limited. Aggregate
tokens are 249,718 input / 29,266 output, and recorded model latency totals
128.3 seconds. No monetary cost is recorded. The 57/340 ratio (~168 ledger calls
per 1,000 documents) describes this small learning period, not steady-state cost.

Rejections include invalid condition shapes, selectors matching one list item,
missing sale/rent vocabulary, and price fields yielding no number. These checks
are useful but insufficient: a title-derived price can let a broken price-cell
recipe pass, and missing optional fields can evade checks by being omitted.

## Improvement options

### 1. Repair extraction and identity first

**Recommended: a hybrid of curated era templates and validated induction.**
Use existing fixture recipes as a starting point, then expand validation using
the actual captured corpus. Give deterministic identity and monetary parsing
explicit invariants, while letting induction propose selectors for unfamiliar
layouts. This preserves adaptation without relearning basic OLX identity.

| Option | Benefit | Cost / limitation |
|---|---|---|
| Curated templates for each known era | Fastest way to fix observed errors; straightforward regression tests | Maintained selectors and vocabulary needed per layout |
| Continue fully induced templates with stronger gates | Adapts to previously unseen pages | More validation infrastructure and labelled examples; slower initial acceptance |
| Hybrid: curated IDs/normalizers/seeds plus induced layout extensions | Strong invariants with layout adaptability | Requires a clear boundary between allowed proposals and fixed semantics |

Concrete changes:

1. Extract the advert URL from `.share-url`, a verified canonical advert URL, or
   the archived original URL. For the 2013 era, derive and cross-check `iid` from
   that URL. Reject homepage/category URLs as detail identities. Use the same
   identity contract on list and detail pages; any switch to namespaced IDs such
   as `iid:535715253` requires migration of existing numeric IDs too.
2. Feed complete price text to `parse_price()`, preserving its currency/unit
   markers. Verify field extraction independently of title fallback. Keep a
   distinct unknown state for missing/placeholder values and ambiguous periods;
   do not assume every unqualified rental price is monthly.
3. Recognize labelled area (`Square Meters:`, Arabic equivalents, unit variants)
   and parse bedroom/bathroom values by their labels, not positional `dd` indices.
   Validate accuracy on fields actually present in raw pages.
4. Separate category, locality, administrative region and country. Preserve the
   full original location and apply a curated geography map with uncertainty.
   Do not split transliterated names on arbitrary hyphens.
5. Reject unknown/misplaced recipe fields and overescaped regexes with concrete
   expected matches. Test uniqueness across different detail pages, list/detail
   agreement, and negative examples. Rank only templates that meet correctness
   constraints; retire or explicitly supersede a faulty overlapping template.
6. Add quality gaps for low completeness, conflicting identity, suspicious
   numbers and field extraction failures on `PARSED` documents. Persist parse
   results with graph version, template, item counts, skips and field evidence.

For the current corpus, build corrected listings and observations in staging
from verified blobs, compare against the snapshot, then reconcile by true advert
ID. Rebuild the nine polluted histories and their merged fields from source
evidence. Preserve an audit mapping from old identities to the contributing
documents. A routine replay alone is insufficient for identity cleanup.

### 2. Make temporal coverage an explicit scheduling objective

**Recommended: inventory first, then stratified collection.** Enumerate each
requested year, report cursor completion and availability, and select captures
by `(year or quarter, region, category, language, template era)`. Unknown strata
must remain visible rather than being silently assigned to Cairo or Egypt.

| Sampling option | Best use | Trade-off |
|---|---|---|
| Fixed year/quarter quotas, round-robin across strata | Comparable breadth across time | Sparse cells cannot meet quotas; report shortfalls |
| Availability-proportional sampling with a minimum per stratum | More volume without entirely losing rare periods | Dense years still dominate; retain selection probabilities |
| Adaptive selection by expected new IDs/fields and coverage deficit | Higher useful yield per Wayback request | Can bias the sample; reserve random exploration and log decisions |

Start with a breadth pilot: up to **20 distinct list captures per available
year**, spread over quarters and major categories/regions, plus **five detail
captures per era** chosen for measured missing fields. These are proposed pilot
budgets, not claims of availability or statistical representativeness. Expand
only after identity/price/area gates pass. Report “not enumerated”, “no indexed
capture”, “not selected”, “unavailable replay” and “unextractable” separately.

Replace the lifetime four-capture list cap with per-period quotas and a global
budget. Keep first/last detail captures for change analysis when available.
Deduplicate **byte storage** by digest while preserving each capture's temporal
evidence; unchanged content seen at different times is still a sighting.
Discover missing linked URLs with bounded exact-URL CDX lookup near the parent's
capture time. Persist parent/child evidence and requested/served capture times.
CDX supports date bounds, exact/domain matching and resume keys; its digest
collapse only removes adjacent matching records, so it is not a complete
application deduplication policy. See the
[Internet Archive CDX documentation](https://raw.githubusercontent.com/internetarchive/wayback/master/wayback-cdx-server/README.md).

Reserve separate navigation/extraction budgets so one does not starve the other.
Use list pages for broad enumeration and select detail pages for enrichment
after identity is trustworthy. Randomly audit skipped/deferred URLs per era to
measure routing false negatives. Archive coverage and historical website usage
remain selection biases even with perfect extraction; store sampling decisions
and denominators for later analysis.

### 3. Strengthen historical storage and workflow accounting

The inexpensive storage option is to extend observations with the complete
normalized state and per-field provenance. A more auditable option is immutable
`capture -> extraction revision -> observation` records, with a separately built
latest-listing projection. The latter uses more storage and migration effort but
supports correction, parser comparisons and repeatable historical datasets.
Either should distinguish advertised date, capture time and ingestion time,
retain date precision/inference, and attach price basis/currency to each value.

Introduce a claim with lease expiry and an acknowledgement after raw metadata
is durable. Use idempotent capture/raw linkage, retryable failure classes,
`next_retry_at`, capped attempts and an explicit review state. PostgreSQL
`FOR UPDATE SKIP LOCKED` can support queue claims, with lease recovery designed
separately; see the
[PostgreSQL locking documentation](https://www.postgresql.org/docs/current/sql-select.html#SQL-FOR-UPDATE-SHARE).
Maintain one shared archive request limiter/cooldown across workers; the current
lock/delay is per client. Increase parsing concurrency independently if measured
CPU work later warrants it.

Expose counters for attempted/fetched/archived/recognized/useful documents,
valid/invalid/duplicate items, unique identities, observations, field accuracy,
and progress per time stratum. Separate empty/non-property recognition from
ad-producing recognition. Track total HTTP attempts and wall time as well as
saved-document budgets. Record replay contributions: scrape runs account for
only 245 creations while 1,182 rows exist, because later replays are not new runs.

## Priorities and acceptance criteria

| Priority | Deliverable | Proposed acceptance gate |
|---|---|---|
| P0 | Correct IDs, prices, area; stage a rebuild of current captures | All 149 distinct detail IDs remain distinct; 134 known list IDs join correctly; homepage IDs rejected; all 382 identified price-loss cases and 238 area cases reconciled or explicitly explained |
| P0 | Gold-set promotion and supersession | No identity collision; at least 95% exact match for ID/title/amount/currency/sale-rent on held-out labelled items; no regression on earlier eras |
| P1 | Version-aware replay and full observation provenance | Same bytes/version give identical outputs; changed rules schedule affected parsed documents; replay order does not change historical state or latest projection |
| P1 | Cross-year inventory and stratified pilot | Every requested year has a completion/availability report; every available target quarter has selected samples or a recorded shortfall |
| P1 | Quality reporting and reliable work acknowledgement | Counts reconcile from capture to raw document to parse result; interruption/retry creates no duplicate capture work or unexplained loss |
| P2 | Enrichment and adaptive sampling | Report added correct fields/new independent IDs per 100 fetches, by era and region; preserve exploration sample and selection probabilities |

Build a labelled set from real saved captures: approximately 30 list items and
10 detail adverts per supported era/layout, including Arabic/English pages,
missing/placeholder prices, foreign currency, ambiguous categories, empty pages
and deliberately similar negative examples. Split by document and advert ID,
not individual repeated rows, to avoid train/validation leakage. Measure field
precision and recall against **raw-present values**, plus end-to-end completeness
and temporal/geographic coverage. A target like “95% of all listings have area”
would reward guessing when source pages never provided it.

## Reproduction and validation

[Read-only assessment SQL](wayback-assessment.sql) provides the database census,
monthly distribution, completeness, identity-collision evidence and selected raw
document metadata. Run against the configured database using a read-only client;
the file itself begins a repeatable-read, read-only transaction and ends with
`ROLLBACK`. Counts will change as collection continues.

For the blob-side checks:

1. Read the active extraction graph, raw metadata, listing rows and observation
   rows in one snapshot. Resolve blobs through the configured blob backend;
   compare byte length and SHA-256 with metadata.
2. Construct `ArchivedDocument.from_payload()` with stored content type, URL and
   metadata. Invoke `HtmlRuleEngine.extract()` directly using the saved graph;
   do not call ingestion or a replay command against the live database.
3. Match list items using `#the-list .li, #itemListContent .the-list .li` and
   `h3 a[href]` containing the item's `iid`. Compare full
   `.third-column-container` text through `parse_price()` with draft/stored price.
   Inspect `.c-4 span` for labelled area. Count one recovery per current listing
   whose provenance document and item ID both match.
4. For detail outcomes, compare IDs derived from the original advert URL with
   emitted IDs/URLs. Check collisions across documents and links to list IDs.
   Compare current-document drafts against stored fields, recording graph versions.

Existing offline verification passed: **78 tests** across `test_archive_domain.py`,
`test_extraction_engine.py`, `test_rule_induction_and_crawl.py` and
`test_wayback_and_llm_clients.py` (1.87 seconds). Database integration tests were
not run against the live database. These passing tests validate mechanisms and
fixture recipes; they do not certify the automatically learned database graph.
This change documents the assessment and proposals; implementation and database
repair remain future work.

## Code references

- [Crawl/enumeration/selection](../core/src/realestate/application/services/archive_crawl_service.py)
  and [frontier persistence](../core/src/realestate/infrastructure/db/repositories/crawl.py).
- [Template selection](../core/src/realestate/infrastructure/extraction/engine.py),
  [field recipes/identity fallback](../core/src/realestate/infrastructure/extraction/template.py),
  [normalizers](../core/src/realestate/infrastructure/extraction/normalisers.py),
  [proposal validation](../core/src/realestate/application/services/rule_induction_service.py).
- [Source fetch/parse](../core/src/realestate/infrastructure/sources/wayback/source.py),
  [Wayback HTTP client](../core/src/realestate/infrastructure/archive/wayback.py),
  [ingestion accounting](../core/src/realestate/application/services/ingestion_service.py).
- [Observation and listing schema](../core/src/realestate/infrastructure/db/models.py),
  [historical upserts/merging](../core/src/realestate/infrastructure/db/repositories/listing.py).
