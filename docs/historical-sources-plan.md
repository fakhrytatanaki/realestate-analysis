# Historical sources: archived olx.com.eg — findings and plan

[Guide index](README.md) · [Architecture](architecture.md) · [Data sources](sources.md)

Status: **phases 0–3 implemented** (2026-10-02). Investigation done the same day
against the live Wayback Machine (CDX API + raw `id_` replays), with Firefox MCP
for visual inspection. Numbers are from that day's CDX responses.

## Decisions (2026-10-02)

| Question | Decision | Consequence in the implementation |
|---|---|---|
| Hand-seed rules for known eras? | **No** — run the LLM loop for every era, keep it general | Graphs start empty. The only seed rule is generic (follow links from recognised pages). Hand-written templates exist only as test data (`tests/fixtures/wayback_olx_eg/templates.json`), proving the template language covers 2011–2023 |
| Detail pages? | Whatever the archive captured, routed by the state machine | Detail URLs go through the same navigation graph; ambiguous ones are `DEFER`red and fetched once a recognised list page links to them |
| Activation gate | Automatic once validation passes; tested via the CLI | Induced versions become `ACTIVE` immediately; `rules show`, `rules gaps`, `archive status` for inspection |
| Model | `gemma4:31b` on Ollama Cloud, free tier | `[llm] max_concurrency = 1` (configurable); tool-call structure with JSON fallback |
| Infra | Docker Postgres, local filesystem blob store | No new backends; migration `1_…_historical_sources` |

## How to run it

```bash
cd core
# put the key in etc/settings.toml under [llm] api_key = "...", or export OLLAMA_API_KEY
./venv/bin/python -m realestate.cli crawl --source olx_eg_wayback --rounds 3 --max-fetches 60 --max-llm-calls 10
./venv/bin/python -m realestate.cli archive status --source olx_eg_wayback
./venv/bin/python -m realestate.cli rules show --source olx_eg_wayback --domain navigation
./venv/bin/python -m realestate.cli rules gaps --source olx_eg_wayback
./venv/bin/python -m realestate.cli search --limit 5
```

Commands are documented in [operations](operations.md#archive-sources), modules in
the [module map](modules.md). Sections 1–2 below are the original investigation and
design; where the implementation deviated, it is noted inline.

---

## 1. Findings

### 1.1 Archive coverage

Unique archived URLs (`statuscode:200`, `mimetype:text/html`, collapsed by
`urlkey`) across all `*.olx.com.eg` hosts:

| Year | All URLs | Property list pages | Property detail pages |
|---|---:|---:|---:|
| 2011 | 3,585 | 210 | ~1,700 (`-iid-N`, slug says property) |
| 2012 | 2,825 | – | – |
| 2013 | 3,776 | 151 | ~1,430 |
| 2014 | 2,417 | – | – |
| 2015 | 2,680 | 155 | ~330 |
| 2016 | 8,965 | – | – |
| 2017 | 27,538 | 855 | 14,241 `/ad/` (**7,613 with empty slug**) |
| 2018 | 29,832 | – | – |
| 2019 | 104,952 | 5,984 | 38,379 `/ad/` (23,969 empty slug) |
| 2020 | 51,490 | – | – |
| 2021 | 122,822 | 8,529 | 44,879 `/ad/` (27,150 empty slug) |
| 2022 | 41,623 | – | – |
| 2023 | 14,340 | 465 | 7,398 `/ad/` |

(Breakdowns were sampled on odd years only. The domain-wide CDX index is 110 pages.)
From 2020 on the homepage returns a 301. Later captures move to dubizzle.com.eg.

Two numbers drive the design:

- **List pages are the main record and detail pages are only occasional
  enrichment.** On the 2013 sample list page, only 4 of 30 adverts had any capture
  of their own detail page.
- **From 2017 on, an `/ad/` URL tells you nothing about its category.** It looks
  like `/ad/<slug>-ID8igUm.html`, and Arabic titles usually come out as an empty
  slug (`/ad/-ID9v1Io.html`). About 55–60% of 2017–21 detail URLs can't be
  classified without fetching them. The useful evidence for "this is a flat" is
  *being linked from a property list page*.

### 1.2 Five site eras

| Era | Years | URL shapes | Ad id | UI language | Item markup | Structured cues |
|---|---|---|---|---|---|---|
| **A1** classic OLX | 2010–12 | `{city}.olx.com.eg/<slug>-cat-363[-p-2]`, ads `<slug>-iid-191449087`, `/q/` search, `/nf/` facets | `iid-N` | en | `ul#the-list > li > div.row` with `.c-2` (title + snippet), `.c-3` (price) | none |
| **A2** classic OLX, new template | 2013–14 | same as A1 | `iid-N` | en | `div#the-list > div.li.row` with `.second/third/fourth-column-container` | none |
| **B** "أوليكس دوبيزل" | 2015–16 | `/ar/property-for-sale/search/?page=N`, ads `/listing/7-listings-<md5>/show/?back=…` | md5 | **ar, RTL** | `.d-listing__item`, `.d-listing__name`, `.d-listing__amount`, `.d-listing__currency` | none |
| **C** OLX i2 (Naspers) | 2017–~21 | `/en/i2/properties/properties-for-sale/apartments-for-sale/<city>/`. In 2021: `/en/properties/apartments-duplex-for-rent/` | base62 `-IDa2hfM` **plus** numeric `data-ad-id` | en UI, Arabic titles | 2017–19: `li.item[data-ad-id]`. 2021: `.ads__item` | **`data-ninja`** attribute: URL-encoded JSON with category L1–L3, city, price, creationDate, sellerType |
| **D** EMPG (the same stack Dubizzle runs today) | ~2022–23 | `/en/properties/apartments-duplex-for-rent/`, ads `/ad/<slug>-ID196340922.html` | numeric | en/ar | client-rendered | **`window.state.algolia.content.hits[]`**. The hit keys (`externalID`, `extraFields`, `location.lvlN`, `geography`, `purpose`, `createdAt`, `state`) are almost the same as the `_source` that `DubizzleEgDataSource._to_draft` already parses |

Era D needs essentially no LLM: it is mostly a JSON adapter around existing code.
The C/D boundary date still needs pinning via CDX. **Hypothesis to verify:** C's
numeric `data-ad-id` (148306342 in 2019, 179068510 in 2021) and D's `externalID`
(196340922 in 2023) look like one increasing sequence. If so, an advert can be
followed across the platform migration.

Templates drift even inside one "era". A1 and A2 share URLs and the `#the-list`
id but use different elements (`ul > li` vs `div > div.li`). Era detection
therefore has to work on **template fingerprints**, not years.

### 1.3 What the heterogeneity looks like (verbatim samples)

| Concern | Examples seen |
|---|---|
| Currency | `ج.م15,000` (A1), `35,500ج.م` (A2), `500,000 جنيه مصري` (B), `377,000 EGP` (C), **`1b , Red Sea - 86161 GBP` with the price only in the title** and an empty price column (A2) |
| Numbers | `بمقدم 132 ألف امتلك شقة 130 متر` ("132 thousand down payment", which is *not* the price), Arabic-Indic digits (`٣٥`) in 2023 slugs |
| Area / rooms | `106.0 م٢`, `2 نوم` (B); `Plot Area : 650 M, Building Area : 750 M` inside a free-text snippet (A1); `Bedrooms: 1` (A2) |
| Dates | `April 22, 13 hours and 4 minutes ago` (A1, relative to the capture); `02 Mar` (**no year**, A2); `21 سبتمبر 2015` (B); ISO `2019-04-04` in `data-ninja` (C); epoch `createdAt` (D) |
| Location | transliterated `al-Bah̨r-al-Ah̨mar`, `Ţanţā`; subdomain as city (`hurgada.`, `gizeh.`); `Egypt` only |
| Partial | list rows without bedrooms/area; no price; placeholder ads (`1b , Red Sea - …`) |

So **the capture timestamp is a parse input**. Without it, `02 Mar` and
`13 hours ago` cannot be resolved.

### 1.4 Wayback mechanics

- `https://web.archive.org/web/{ts}id_/{original}` returns the original bytes,
  with no toolbar and no link rewriting. Headers carry `Memento-Datetime`,
  `Link: <…>; rel="original"` and `x-archive-src`. Some replays come back with
  the original `Content-Encoding: gzip` (httpx decodes it; bare curl does not).
- The CDX API supports `from`/`to`, `filter=statuscode:200`, `collapse=urlkey|digest`,
  paging and `fl=timestamp,original,digest`. `digest` lets identical captures be
  skipped before fetching.
- Responses took about 1 s. IA throttles aggressive clients (429s, temporary IP
  blocks) and doesn't publish exact limits, so the crawler must be adaptive and
  conservative.

### 1.5 Firefox MCP

- It works against the existing display (`DISPLAY=:0`, Firefox 153, not headless).
- `new_page`/`navigate_page` hit a BiDi timeout on heavy Wayback pages even
  though the page loaded. Use `wait: "none"`/`"interactive"`, then poll with
  `list_pages`.
- `evaluate_script` is the most useful tool: it probes the DOM, counts selector
  matches and dumps item HTML. `screenshot_page` `saveTo` is limited to
  `~/.firefox-devtools-mcp/output`.
- **Role: a development and labelling tool, not a production fetcher.** Archived
  pages are static, `id_` gives exact bytes, and era D's data is inline JSON.
  A browser adds the Wayback chrome and is much slower. `BrowserDataSource` isn't
  needed for this source. Firefox MCP fits three jobs:
  1. exploring a new era/template,
  2. hand-labelling the gold set (§4),
  3. visually checking an LLM-proposed selector by highlighting its matches on
     the rendered capture.

### 1.6 Ollama Cloud (the LLM API)

Checked against `docs.ollama.com` and `ollama.com/pricing` on the same day:

- Base URL `https://ollama.com/api` (`/api/chat`). Auth is
  `Authorization: Bearer $OLLAMA_API_KEY`. An OpenAI-compatible endpoint is at `/v1`.
- **"Ollama's Cloud currently does not support structured outputs"** (verbatim
  from the structured-outputs page). The `format: <json schema>` parameter can't
  be relied on. **Tool calling is supported** for models trained for it.
- The hosted Gemma is **`gemma4:31b`** (there is no small Gemma in the cloud).
  Cheaper options are `gpt-oss:20b` and `nemotron-3-nano:30b`.
- Prices per 1M tokens (input / output): gemma4 $0.14 / $0.40, gpt-oss:20b
  $0.07 / $0.30, nemotron-3-nano $0.06 / $0.24. Off-peak is cheaper. Concurrency
  is plan-bound (Max: 10 concurrent).
- Errors: 429 (rate limit) and 502 ("cloud model cannot be reached") are
  retryable. Responses include `prompt_eval_count` and `eval_count` for cost
  accounting.
- Privacy: "We do not use them to train models." The inputs are public archived
  pages anyway.

What this means for the design:

- **At these prices, cost is not the main reason for the rule-graph cache.** One
  selector-induction call (~6k tokens in, ~800 out on gemma4) costs about
  $0.0012. Even naive per-document LLM extraction over 100k documents would cost
  roughly $150–200.
- The cache is still the right design, for other reasons:
  - **determinism and replay**: `parse()` can't call a remote model;
  - **auditability**: a rule can be inspected and tested;
  - **throughput under concurrency caps**;
  - **model churn**: cloud model ids change and retire. A compiled rule outlives
    the model that wrote it.

### 1.7 Repo constraints this collides with

1. **The `parse()` invariant** (deterministic, no network) rules out calling an
   LLM inside `parse()`.
2. **The `fetch()` invariant** (no parsing) conflicts with a crawler that needs
   links from fetched pages.
3. **Time semantics.**
   - `first_seen_at`/`last_seen_at` are wall-clock *ingestion* times.
   - `upsert_many` overwrites the row with whatever draft is processed last.
     Replaying captures out of order would let a 2013 price overwrite a 2014 one.
   - There is no price history at all. For historical data, the history *is*
     the product.
4. **`mark_stale`** (`repositories/listing.py`) deactivates by wall clock. It
   must never run for an archive source.
5. **`ListingDraft` requires** `listing_type`, `property_type` and
   `price.currency`. Partial adverts need honest "unknown" values, not invented
   ones.
6. **`attributes` is hashed** (`domain/hashing.py`). Provenance stored there
   (graph version, node ids) would mark every listing "changed" on each rule
   revision unless it is excluded from the hash.
7. **Source factories receive only `SourceContext(settings, log)`**. A crawler
   also needs the frontier and rule-graph repositories.
8. **The missing currency normalisation gets worse.** EGP went from ~5.8/USD
   (2011) to ~16–18 (after the 2016 float) and ~30 (2023), with prices in
   GBP/USD mixed in. A cross-era `price_max` is meaningless without dated FX.
9. Local: `core/venv/bin/pip` has a stale shebang (`/home/fht/...`). Use
   `./venv/bin/python -m pip`. The dev box is aarch64. That doesn't matter for
   production (cloud), but wheels and tooling must have arm64 builds for local work.

---

## 2. Design

### 2.1 Core idea: the rule graph is a compiled cache of LLM decisions

```
 input (URL | document | item fragment)
        │
        ▼
 fingerprint ──► rule graph (pinned version) ── conditions match? ──► yes ─► run node action
        │                                                                  (deterministic)
        │                                                no
        ▼                                                 │
 record a GAP (deduplicated by fingerprint) ◄─────────────┘
        │   (offline, async, never inside parse)
        ▼
 induction job ──► LLM (Ollama Cloud) ──► candidate node(s): conditions + action
        │
        ▼
 validate against the gap document + its fingerprint cluster + gold set
        │ pass
        ▼
 new graph version (draft → active) ──► reparse UNRECOGNISED docs ──► now a hit
```

Every LLM answer is turned into rules (selectors, regexes, spaCy patterns,
vocabulary entries) that run without the LLM afterwards. A thousand pages from
one unseen template produce **one** gap and **one** LLM job. The metric to watch
is **LLM calls per 1,000 documents**, per era. It should trend toward zero as the
graph matures.

*(Superseded by the decision above: no hand seeds; templates.json in the tests
plays the "reference to grade against" role.)* **Original recommendation: seed the
graph by hand first.** The five list-page templates
in §1.2 are few and are now known. Hand-written seed nodes give immediate
coverage *and* a reference to grade LLM-induced rules against. The LLM's job is
the long tail: detail-page templates, the mobile site (`m.olx.com.eg/item/`),
`/q/` search pages, facet pages, vocabulary, and drift inside eras.

There are two graphs, with the same engine and different action vocabularies:

| Graph | Input | Actions |
|---|---|---|
| **navigation** | CDX row (URL, timestamp, digest), optionally the referring page's classification | `FETCH(priority)`, `SKIP(reason)`, `EXPAND` (pagination / capture fan-out), `DEFER` (needs evidence, e.g. an `/ad/` URL with an empty slug that no list page has linked to yet) |
| **extraction** | an archived document + its capture metadata | `CLASSIFY` (era/template/page kind) → `SPLIT` (item fragments) → `EXTRACT` (field) → `NORMALISE` → emit draft. Also `LINKS` (discovered URLs) |

### 2.2 State-machine semantics

- **Nodes are states.** Each has a `kind` (`classify | split | extract |
  normalise | route | links | gap`) and an `action` spec (JSONB).
- **Edges are guarded transitions.** They are evaluated in priority order, and
  the first match wins. If no edge from a state matches, that's a **miss** and
  ends in a `gap` node.
- **Every evaluation returns a trace** (path of node ids plus the conditions that
  fired), stored with the drafts. The service aggregates traces into hit/miss
  statistics. `parse()` stays free of side effects.

Condition types (all deterministic, stored as typed JSONB):

| Type | Engine | Example |
|---|---|---|
| `url_regex` | `re` | `-cat-(363\|367)(-p-\d+)?$` |
| `capture_range` | – | `2012-06 ≤ ts < 2015-01` (a hint, never decisive alone) |
| `template_fp` | simhash over DOM tag-path shingles | Hamming distance ≤ 6 from cluster centroid |
| `dom_css` / `dom_xpath` | selectolax / lxml | `div#the-list > div.li` count ≥ 5 |
| `jsonpath` | over embedded JSON (`window.state`, `data-ninja`) | `$.algolia.content.hits[*].externalID` exists |
| `text_regex` | `re` on node text | `(?:ج\.م\|جنيه\|EGP\|GBP\|USD)` |
| `spacy_match` | spaCy `Matcher`/`EntityRuler` on `spacy.blank("ar"/"en")` tokens | `[{"LIKE_NUM":true},{"LOWER":{"IN":["م٢","متر","sqm","m"]}}]` |
| `all` / `any` / `not` | composite | – |

**Stateful parsers are code, not rules.** Price, area, rooms, date and currency
normalisers are small finite-state tokenisers in a library:

- Arabic-Indic digits, `ألف`/`مليون` multipliers, separators.
- `ج.م`/`جنيه مصري`/`L.E`/`EGP`/`GBP`/`£` disambiguation (`£` alone is ambiguous
  between E£ and GBP, so it needs context).
- Down-payment vs price (`مقدم`).
- Date anchoring against the capture: a yearless `02 Mar` takes the capture year,
  or the previous year if that date would be after the capture.
- Relative `13 hours ago` / `منذ 3 أيام` / `Yesterday`.

Rules refer to normalisers by name and parameters. The LLM may propose
**vocabulary** additions (e.g. `نوم` → bedroom unit) but **never normalises
numbers itself**.

An example seed node (era A2 list page):

```json
{
  "key": "a2.list.split",
  "kind": "split",
  "action": {"engine": "css", "item": "div#the-list > div.li",
             "fields": {
               "title":    {"css": "h3 a", "text": true},
               "url":      {"css": "h3 a", "attr": "href"},
               "external_id": {"css": "h3 a", "attr": "href", "regex": "iid-(\\d+)", "prefix": "iid:"},
               "price_raw":{"css": ".third-column-container", "text": true, "normalise": "price_ar_en"},
               "date_raw": {"css": ".fourth-column-container", "text": true, "normalise": "date_anchor_capture"},
               "category": {"css": ".itemlistinginfo a", "text": true, "normalise": "vocab:category"},
               "title_price": {"from": "title", "normalise": "price_in_text", "when_missing": "price_raw"}
             }},
  "edges_in": [{"from": "classify.root",
                "condition": {"all": [{"type": "dom_css", "css": "div#the-list > div.li", "min": 3},
                                      {"type": "url_regex", "pattern": "-cat-\\d+"}]}}]
}
```

### 2.3 Postgres schema

The graph is small (hundreds of nodes). It is loaded into memory per version,
so no graph database and no recursive CTEs are needed.

| Table | Key columns | Purpose |
|---|---|---|
| `rule_graph` | `id`, `source_key`, `domain` (nav/extract), `version`, `status` (draft/active/retired), `parent_id`, `created_at`, `notes` · unique(`source_key`,`domain`,`version`) | Immutable versions. Exactly one active version per (source, domain) |
| `rule_node` | `id`, `graph_id`, `key`, `kind`, `action` JSONB, `origin` (seed/llm/human), `llm_decision_id` | States |
| `rule_edge` | `id`, `graph_id`, `from_node`, `to_node`, `condition` JSONB, `priority` | Guarded transitions |
| `rule_stats` | `graph_id`, `node_id`, `hits`, `misses`, `failures`, `last_hit_at` | Aggregated from parse traces by the service |
| `llm_decision` | `id`, `task`, `input_fp` (sha256), `model`, `prompt_version`, `request` JSONB (trimmed), `response` JSONB, `valid`, `tokens_in`, `tokens_out`, `latency_ms` · unique(`task`,`input_fp`,`model`,`prompt_version`) | Raw LLM cache plus cost ledger. Lets a rule be traced back to the answer that produced it |
| `extraction_gap` | `id`, `source_key`, `stage`, `fingerprint`, `sample_document_ids[]`, `occurrences`, `status` (open/induced/rejected/ignored) | Deduplicated work queue for induction |
| `vocab_entry` | `source_key`, `field`, `raw`, `normalised`, `origin`, `confidence` | Category/property-type/unit/currency synonyms, Arabic and English |
| `crawl_frontier` | `id`, `source_key`, `original_url`, `url_key`, `capture_ts`, `digest`, `status`, `priority`, `route_node_id`, `discovered_from_document_id`, `attempts` · unique(`source_key`,`url_key`,`capture_ts`) | Crawl state, resumable |
| `listing_observation` | `id`, `listing_id`, `observed_at`, `raw_document_id`, `price`, `currency`, `price_type`, `content_hash`, `completeness` | One row per sighting. **Price history** |

Additions to existing tables:

- `listing.first_observed_at` / `listing.last_observed_at`: capture time for
  archive sources, seen time for live ones.
- `raw_document.captured_at` (indexed) and `raw_document.rule_graph_version`.
- A new `RawDocumentStatus.UNRECOGNISED`: no rule matched, so the document is
  waiting for induction. It is distinct from `FAILED` (a rule crashed).

### 2.4 Fit with the pipeline, without breaking the invariants

```
 ArchiveIndex (CDX) ──► crawl_frontier ◄──────────────────────────┐
                          │ nav graph routes each row             │ discovered links
                          ▼                                       │ (from LINKS nodes)
 fetch(): drains frontier, GET {ts}id_/{url} ──► bytes only       │
                          ▼                                       │
 archive: blob + RawDocument(meta: captured_at, original_url,     │
          digest, referrer, nav_node)                             │
                          ▼                                       │
 parse(): extraction graph @ pinned version ──► drafts + trace ───┤
          (pure; capture ts comes from meta)                      │
                          │                                       │
       no match ──► UNRECOGNISED + extraction_gap                 │
                          ▼                                       │
 InductionJob (offline): LLM ─► candidate ─► validate ─► new version ─► reparse
```

- **`fetch()` stays bytes-only.** Link discovery happens on the *archived* bytes
  through a separate optional port method,
  `LinkDiscovering.discover(payload) -> Sequence[DiscoveredLink]`. It is pure and
  driven by `links` nodes, and a `CrawlService` calls it after archiving. The
  `fetch()` generator re-reads the frontier between yields. `DataSource.parse`
  keeps its signature.
- **`parse()` stays deterministic.**
  - The graph version is loaded once at source construction and pinned for the
    whole run, and recorded on each `RawDocument`.
  - The LLM is never called from `parse()`, and `parse()` never reads the
    `llm_decision` cache. Only compiled rules run.
  - Replaying with the same version gives the same drafts. Replaying with a newer
    version is the point of `--reparse`.
- **Per-item LLM extraction** (for example bedrooms buried in free-text
  descriptions) is deliberately left out of phase 1. When needed, it becomes a
  separate **enrichment** stage that writes to `attributes` with provenance. Even
  there, the preferred output is an LLM-induced spaCy pattern rather than
  per-item answers.

### 2.5 LLM integration (Ollama Cloud)

- **Port (domain):** `StructuredLlm.complete(task, messages, schema) -> dict`.
  Domain only knows the task name and a plain JSON-schema dict.
- **Adapter (infrastructure):** `OllamaCloudLlm`, using httpx against
  `https://ollama.com/api/chat` with Bearer auth.
  - **Structured output without `format`.** Primary path: a single
    **tool definition** whose `parameters` is the schema, with `tool_choice`
    forcing that tool. Fallback: a JSON-only instruction plus extraction of the
    first JSON object from the reply.
  - Validate in application code with a pydantic model. That's allowed there,
    since application already uses pydantic DTOs. On failure, send the validation
    errors back once or twice ("repair" turn), then mark the gap
    `rejected:invalid`.
  - Settings: `temperature: 0`, fixed `seed`, `stream: false`. Retry 429/502/5xx
    with backoff.
  - Record `prompt_eval_count`/`eval_count` into `llm_decision` for cost tracking.
  - Concurrency is bounded by config and matched to the Ollama plan.
- **Model choice is configuration, chosen by eval** (§4), per task:

  | Task | Default | Why |
  |---|---|---|
  | page classification, URL-shape routing | `gemma4:31b` | Gemma as intended, good multilingual and Arabic, cheap |
  | selector induction | `gemma4:31b`, escalating to `gpt-oss:120b` on validation failure | escalation only on the rare hard cases |
  | vocabulary mapping | `gpt-oss:20b` or `nemotron-3-nano` | trivial task, cheapest models |

  Because the adapter speaks the native Ollama API, a self-hosted Ollama (local
  `gemma4`/smaller Gemma for dev, or a GPU node) is just a different base URL and
  no key.

LLM tasks:

| Task | Input (trimmed) | Output | Compiled into | Validation |
|---|---|---|---|---|
| `classify_page` | URL, capture year, DOM skeleton | `page_kind`, `is_property`, `language`, distinguishing selectors | `classify` node: `template_fp` + `dom_css` conditions | selectors match the sample and ≥ 90% of its fingerprint cluster, and don't match a negative sample from other clusters |
| `induce_item_rules` | skeleton with repeated siblings collapsed to 2, plus 2 full item fragments | item selector + per-field `{css, attr, regex, normaliser}` | `split` node | ≥ 3 items per doc on ≥ 80% of the cluster; fields pass their normalisers; agreement with the LLM's own direct reading of the 2 sample items; no regression on the gold set |
| `route_url_shapes` | a CDX URL-shape cluster with 20 examples | `kind`, `is_property`, `fetch?`, generalising regex | `route` node | regex matches all positives and none of a sampled negative set |
| `map_vocab` | unseen label (`Apartments & Duplex for Rent`, `شقق للبيع`, `نوم`) | enum or unit | `vocab_entry` | enum membership; human-reviewable list |
| `induce_spacy_pattern` (phase 4) | descriptions with LLM-labelled spans | `Matcher` patterns | `spacy_match` / `extract` node | precision on held-out labelled spans |

**DOM trimming** happens before any prompt. Drop `script`/`style`/`svg`/`noscript`
and the Wayback toolbar. Keep only `id`, `class`, `href` and `data-*`
attributes, and truncate text to 80 characters. Collapse runs of same-shape
siblings to 2 plus a count. Cap the prompt at ~8k tokens.

**Activation gate:** an LLM-induced version starts as `draft`. It's promoted to
`active` automatically only if it passes the gold-set regression and the
cluster-coverage checks. Otherwise it's queued for human review. Firefox MCP can
highlight its matches on the live capture for that review.

### 2.6 Crawl strategy

1. **Enumerate** the CDX per year (`filter=statuscode:200`,
   `filter=mimetype:text/html`, `fl=timestamp,original,digest`). This is roughly
   400k rows: small, stored straight into `crawl_frontier` with status
   `discovered`. It is a metadata request; no page bytes are fetched.
2. **Route** every row through the navigation graph. The seed rules are:
   - **FETCH (high priority):** property list pages in every era:
     - `-cat-(16|363|367|410|…)(-p-N)?` on any city subdomain,
     - `/(ar|en)/property-for-(sale|rent)/…search/`,
     - `/en/i2/properties/…`,
     - `/(en/)?properties/…`,
     - plus their `?page=`/`-p-N` pagination.
   - **FETCH (medium):** detail URLs whose slug matches the property lexicon
     (`شقة|شقق|فيلا|للبيع|للايجار|apartment|villa|flat|duplex|chalet|land|for-(rent|sale)|\d+م`),
     and any URL a fetched property list page linked to.
   - **DEFER:** `/ad/-ID…html` with an empty slug and no linking evidence yet.
   - **SKIP:** vehicles, electronics, jobs, fashion, `help.`, `blog.`, assets,
     facet permutations beyond the first.

   Unknown URL shapes become `route_url_shapes` gaps (one per shape cluster).
3. **Choose captures.**
   - List pages: every *distinct digest* up to N per month. Each one is a new
     snapshot of which ads were live at what price.
   - Detail pages: the earliest and latest distinct digest.
4. **Fetch**, single-flight per IP:
   - `{ts}id_/{original}`. Read the true capture time from `Memento-Datetime`.
     Follow Wayback's nearest-capture redirects and record the actual timestamp.
   - Start at ~1 request every 1–2 s with no parallelism. Back off sharply on
     429 (minutes, honouring `Retry-After`).
   - Use a **fixed, honest UA with a contact address.** Override
     `default_headers`; don't rotate UAs against IA.
   - **Don't spread across IPs to dodge throttling.** That is both abusive and
     against IA's terms.

   Budget: about 35k property list URLs across all years (estimated from the odd
   years). At ~40k fetches/day that's a day or two for the list-page pass. Detail
   enrichment comes after.
5. **Resume:** the frontier is the checkpoint. A run is a `RunTrigger.BACKFILL`
   job (the enum value already exists) with a page/time budget, not an interval
   schedule.

### 2.7 Data model changes for historical listings

- **`ListingDraft.observed_at: datetime | None`.** Archive sources set it to the
  capture time.
- **Upsert by observation order:**
  - always append a `listing_observation` row (deduplicated on
    `(listing_id, observed_at, content_hash)`);
  - overwrite the `listing` row's business fields only if
    `observed_at >= last_observed_at`.

  This makes replay order irrelevant. The existing `.update()` / `auto_now`
  behaviour is kept for live sources.
- **`SourceDescriptor.kind: live | archive`.**
  - `mark_stale` and anything else wall-clock-based skips archive sources.
  - Archive listings are stored `is_active=False`, since "active" isn't knowable.
  - The API gains `observed_from`/`observed_to` filters through
    `ListingQueryParams` → `ListingQuery`.
- **External ids are namespaced per era**: `iid:<n>` (A), `md5:<hash>` (B),
  `ad:<numeric>` (C via `data-ad-id`, and D's `externalID` if §1.2's continuity
  hypothesis holds; otherwise `c:<base62>`).
- **Unknown is explicit, never invented:**
  - price: `amount=None`, `currency="XXX"` (the ISO 4217 code for "no currency"),
    and a new `PriceType.UNKNOWN`. That is distinct from `ON_REQUEST`, which means
    the seller asked to be called;
  - `PropertyType.OTHER`;
  - `listing_type` from the category the item was found under. If that's
    unresolvable, the item is skipped and logged.
- **Provenance:** `attributes["_extraction"] = {graph_version, nodes: {field: node_key}, completeness}`.
  `domain/hashing.py` must **exclude `_`-prefixed attribute keys** so that rule
  revisions don't register as advert changes.
- **Currency follow-up:** a dated `FxRateProvider` (CBE monthly averages) to
  derive `price_usd_at_observation` and an inflation-adjusted figure. It's
  optional for phase 1, but cross-era comparison is meaningless without it.
- **PII:** archived descriptions include phone numbers. Redact them in the
  normalised `description`. Raw bytes stay in the blob store as for every source.

### 2.8 Layering (within the rules in CLAUDE.md)

| Layer | New modules |
|---|---|
| `domain/` | `rules.py` (graph, node, edge and condition dataclasses, plus the trace), `archive.py` (`Capture`, `DiscoveredLink`), ports: `ArchiveIndex`, `CrawlFrontierRepository`, `RuleGraphRepository`, `ExtractionGapRepository`, `LlmDecisionRepository`, `StructuredLlm`, optional `LinkDiscovering` |
| `application/` | `services/crawl_service.py` (frontier → fetch → archive → discover), `services/rule_induction_service.py` (gap → LLM → validate → version), `jobs/induce_rules_job.py`, pydantic schemas for LLM outputs, the gold-set evaluator |
| `infrastructure/` | `archive/wayback.py` (CDX client and memento fetch, built on `HttpDataSource` politeness), `extraction/` (graph executor, condition evaluators on selectolax/lxml/re/spaCy, normaliser library, DOM trimming and fingerprinting), `llm/ollama.py`, `db/` models and repositories for the new tables, `sources/olx_eg_wayback/` (source, seed graph JSON, era-D adapter reusing the Dubizzle hit mapping) |
| `bootstrap.py` | wire the new repositories and LLM. Extend `SourceContext` with an optional `services` bag so the archive source's factory receives frontier and graph handles without importing infrastructure into application |

Generic parts (`archive/`, `extraction/`, `llm/`, the services) carry no OLX
knowledge. A second archived portal (old aqarmap, propertyfinder.eg, past
dubizzle) is a new seed graph plus a source class.

### 2.9 Cloud deployment

| Component | Shape |
|---|---|
| LLM | Ollama Cloud. `OLLAMA_API_KEY` comes from the secret store. A plan with enough concurrency for induction bursts (Pro, or Max for 10 concurrent). Induction runs as a batch, so off-peak scheduling halves the cost |
| Crawler worker | one small CPU replica per archive source, holding a single egress IP *on purpose* (IA politeness) |
| Parse/induction workers | horizontally scalable. CPU only (selectolax, spaCy blank tokenisers, no spaCy models to download) |
| Blob store | `S3BlobProvider` (done: any S3-compatible store, GCS via its interop endpoint, credentials from the instance role). Still to do: gzip historical HTML. Estimate 50–500 KB per page, so 10–50 GB for ~100k documents |
| Postgres | managed. The rule tables are tiny. `listing_observation` is the table that grows |
| Scheduling | backfill jobs are triggered manually or by a budgeted cron. Not the per-source interval used by live sources |

Local development and CI use a `FakeStructuredLlm` that replays recorded
`llm_decision` rows, so tests stay offline, deterministic and free.

---

## 3. Phased plan

Implemented: phases 0–3, plus the detail-page routing of phase 4 (deferred URLs are
fetched on link evidence). Not yet: LLM-induced spaCy patterns (the `spacy_match`
condition exists, but no prompt asks for it yet), description enrichment,
`FxRateProvider`. The S3 `BlobProvider` now exists (`[blob] backend = "s3"`;
GCS through its S3 interop endpoint). Deviations from the plan below: the
navigation validator accepts several partial rules per URL-shape group (groups
mix property and non-property pages), and rule order follows the model's answer
order.

**Phase 0: groundwork (no LLM, no crawl)**
- Add `observed_at` to `ListingDraft`, plus `listing_observation`,
  `first_observed_at`/`last_observed_at`, observation-ordered upsert, and
  `SourceDescriptor.kind`, with `mark_stale` excluded for archive sources.
- Add `PriceType.UNKNOWN`, the `XXX` currency convention, and exclude
  `_`-prefixed attributes from the content hash.
- Add `RawDocumentStatus.UNRECOGNISED` and `raw_document.captured_at` /
  `rule_graph_version`.
- Migration via aerich.
- Tests:
  - out-of-order replay leaves the latest observation on the row;
  - an unchanged hash still doesn't touch `updated_at`.
- *Exit:* existing suite plus new tests green, and both layering greps empty.

**Phase 1: archive access and frontier**
- The `ArchiveIndex` port and `WaybackCdxClient` (paging, filters, digest),
  `crawl_frontier` with its repository, a memento fetcher with an adaptive delay
  and 429 cooldown, and the fixed UA.
- CLI: `cli archive enumerate --source olx_eg_wayback --from 2011 --to 2023` and
  `cli archive status`.
- Recorded CDX and `id_` fixtures under `var/fixtures/olx_eg_wayback/`
  (sanitised).
- *Exit:* the frontier holds all CDX rows, and resume after kill is idempotent.

**Phase 2: rule-graph engine and seeded extraction** (first historical listings)
- Domain graph model, the Postgres tables, and a loader that imports seed JSON
  as graph version 1.
- Executor, condition evaluators, DOM fingerprinting, and the normaliser
  library (price/area/rooms/date/currency, Arabic and English).
- Hand-written seed nodes:
  - navigation rules (§2.6);
  - extraction for list pages in A1, A2, B, C(2017–19), C(2021) and D. D reuses
    the Dubizzle hit mapping, factored out of `DubizzleEgDataSource` into a
    shared function.
- `OlxEgWaybackDataSource` with `fetch` from the frontier plus
  `LinkDiscovering.discover`, and a `CrawlService`.
- *Exit:* a bounded backfill (say 500 list pages spread over all eras) produces
  listings for every era; field accuracy on the gold set is ≥ 95% for
  title/price/currency/listing_type; reparse is byte-for-byte deterministic.

**Phase 3: gaps and the LLM induction loop**
- `extraction_gap` (fingerprint deduplication), the `StructuredLlm` port and
  `OllamaCloudLlm` (tool-call schema, JSON fallback, repair turn, cost ledger),
  and `llm_decision`.
- Prompts for `classify_page`, `induce_item_rules`, `route_url_shapes` and
  `map_vocab`, each with a versioned prompt id.
- The validation harness, draft → active promotion, and automatic reparse of
  `UNRECOGNISED` documents after promotion.
- *Exit:* on a held-out era (hide one seed era, say B, and let the loop rebuild
  it), induced rules reach ≥ 90% of the hand-seeded accuracy. LLM calls per
  1k documents and cost per era are reported.

**Phase 4: detail pages and enrichment**
- Detail-page templates per era (mostly induced). Merge detail fields into
  list-derived listings by external id.
- spaCy pattern induction for description fields (rooms, area, floor, finishing,
  compound names).
- Empty-slug `/ad/` resolution: fetch only those linked from property list pages.
- *Exit:* completeness (fraction of the core fields present) is reported per era,
  with detail-enriched vs list-only listings distinguished.

**Phase 5: generalise**
- A `WaybackDataSource` base, plus documentation in `docs/sources.md` for adding
  an archived portal (seed graph + key).
- Optional `FxRateProvider` with dated rates.

---

## 4. Evaluation

- **Gold set:** ~30 list items per template (A1, A2, B, C-2017, C-2021, D) plus
  ~10 detail pages per era, ~250 records in all. Hand-label them with Firefox MCP
  open on the capture. Store them under `core/tests/gold/` as (capture
  URL + timestamp + expected fields), with the raw bytes in `var/fixtures`.
- **Metrics, per era and per field:** precision, recall, exact-match rate on
  price amount and currency, date error in days, and the share of documents
  `UNRECOGNISED`.
- **Loop health:** LLM calls per 1k documents, induction acceptance rate, cost
  per era, rule hit rate over time (from `rule_stats`).
- **Regression:** every graph promotion re-runs the gold set, and it must not
  get worse.

## 5. Risks and open questions

| Risk | Mitigation |
|---|---|
| IA throttling or blocking | single-flight, adaptive delay, honest UA, resumable frontier, digest deduplication |
| Ollama Cloud: no `format`, models retire, plan concurrency | tool-call schema + validation/repair; model is per-task config; rules outlive models; recorded decisions make tests independent of the API |
| LLM rules overfit one page | cluster-coverage and negative-sample validation, gold-set gate, draft status, human review via Firefox MCP |
| "Price" is really a down payment or instalment (`مقدم`, `قسط`) | the normaliser detects these keywords → `PriceType.INSTALLMENT` / attribute, never `TOTAL` |
| Year inference errors (`02 Mar`) | anchored to `Memento-Datetime`. Documents where the capture date is uncertain are flagged |
| PII in descriptions | redact phones/emails in normalised text |

Decisions needed from you:

1. **Priority years.** Coverage is thin before 2016 (hundreds of property pages
   per year) and dense in 2019–21. Should we start dense (C/D: more data, less
   heterogeneity) or start with the hard eras (A/B: proves the LLM loop)?
2. **Detail pages:** worth a phase of their own, or list-page data only?
3. **Activation gate:** may induced rules go live automatically once they pass
   validation, or should every promotion be human-approved at first?
4. **Ollama plan and model:** confirm `gemma4:31b` as the default, and the plan
   tier (it sets the concurrency).
5. **Target cloud** (blob store and Postgres provider), so phase 0 can include
   the matching `BlobProvider`.
