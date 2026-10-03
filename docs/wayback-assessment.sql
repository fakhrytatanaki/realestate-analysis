-- Read-only census for docs/wayback-performance-assessment.md.
-- Run using the application's configured database, without printing credentials.
-- No blob contents, descriptions, contact details or LLM responses are selected.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '60s';

SELECT current_timestamp AS snapshot_utc, current_database() AS database_name;

SELECT scope, done, resume_key IS NOT NULL AS has_resume_key
FROM crawl_cursor WHERE source_key = 'olx_eg_wayback' ORDER BY scope;

SELECT left(timestamp, 4) AS capture_year, status, count(*) AS captures
FROM crawl_frontier WHERE source_key = 'olx_eg_wayback'
GROUP BY 1, 2 ORDER BY 1, 2;

SELECT count(*) FILTER (WHERE route_node LIKE '%#capture-cap') AS capture_cap_skips,
       count(*) FILTER (WHERE attempts > 0) AS captures_with_recorded_failures
FROM crawl_frontier WHERE source_key = 'olx_eg_wayback';

SELECT status, count(*) AS documents, sum(size_bytes) AS bytes,
       count(DISTINCT sha256) AS distinct_payload_hashes
FROM raw_document WHERE source_key = 'olx_eg_wayback' GROUP BY status;

SELECT left(meta->>'timestamp', 6) AS capture_month, count(*) AS documents
FROM raw_document WHERE source_key = 'olx_eg_wayback'
GROUP BY 1 ORDER BY 1;

SELECT to_char(o.observed_at AT TIME ZONE 'UTC', 'YYYY-MM') AS observation_month,
       count(*) AS observations, count(DISTINCT o.listing_id) AS listing_ids
FROM listing_observation o JOIN listing l ON l.id = o.listing_id
WHERE l.source_key = 'olx_eg_wayback' GROUP BY 1 ORDER BY 1;

SELECT count(*) AS listings, count(price) AS price_present,
       count(area_sqm) AS area_present, count(bedrooms) AS bedrooms_present,
       count(bathrooms) AS bathrooms_present,
       count(nullif(description, '')) AS description_present,
       count(nullif(city, '')) AS city_present,
       count(nullif(district, '')) AS district_present,
       count(latitude) AS latitude_present, count(longitude) AS longitude_present,
       count(listed_at) AS listed_date_present,
       count(*) FILTER (WHERE property_type = 'OTHER') AS property_type_other,
       count(*) FILTER (WHERE is_active) AS active_archive_rows,
       count(*) FILTER (WHERE attributes #>> '{_raw,price}' = '.') AS dot_price_text,
       min(first_observed_at) AS earliest_observation,
       max(last_observed_at) AS latest_observation
FROM listing WHERE source_key = 'olx_eg_wayback';

SELECT currency, price_type, count(*) AS listings
FROM listing WHERE source_key = 'olx_eg_wayback'
GROUP BY 1, 2 ORDER BY 1, 2;

SELECT attributes #>> '{_extraction,template}' AS template,
       attributes #>> '{_extraction,graph_version}' AS graph_version,
       count(*) AS listings
FROM listing WHERE source_key = 'olx_eg_wayback'
GROUP BY 1, 2 ORDER BY 1, 2;

-- Compare observation provenance URLs, not the final listing URL, to find
-- multiple real advert IDs collapsed into one normalized listing identity.
SELECT l.external_id, count(*) AS observations,
       count(DISTINCT substring(r.source_url FROM 'iid-([0-9]+)')) AS original_ad_ids
FROM listing l JOIN listing_observation o ON o.listing_id = l.id
JOIN raw_document r ON r.id = o.raw_document_id
WHERE l.source_key = 'olx_eg_wayback'
GROUP BY l.id, l.external_id
HAVING count(DISTINCT substring(r.source_url FROM 'iid-([0-9]+)')) > 1
ORDER BY observations DESC;

SELECT count(*) AS raw_documents_without_observations
FROM raw_document r
WHERE r.source_key = 'olx_eg_wayback'
  AND NOT EXISTS (SELECT 1 FROM listing_observation o WHERE o.raw_document_id = r.id);

SELECT g.domain, g.version, g.status,
       (SELECT count(*) FROM rule_node n WHERE n.graph_id = g.id) AS nodes,
       (SELECT count(*) FROM rule_edge e WHERE e.graph_id = g.id) AS edges
FROM rule_graph g WHERE source_key = 'olx_eg_wayback' AND status = 'ACTIVE';

SELECT domain, status, count(*) AS gaps
FROM rule_gap WHERE source_key = 'olx_eg_wayback'
GROUP BY 1, 2 ORDER BY 1, 2;

SELECT status, count(*) AS runs, sum(documents_fetched) AS documents_fetched,
       sum(listings_created) AS created_in_runs,
       sum(listings_updated) AS updated_in_runs, sum(errors) AS errors,
       sum(extract(epoch FROM finished_at - started_at))
           FILTER (WHERE documents_fetched > 0) AS nonempty_run_seconds
FROM scrape_run WHERE source_key = 'olx_eg_wayback' GROUP BY status;

-- Ledger has no source key: these totals cover the database, not necessarily
-- this source. Accepted decisions can be attributed via rule_node references.
SELECT task, valid, count(*) AS decisions,
       sum(tokens_in) AS tokens_in, sum(tokens_out) AS tokens_out,
       sum(latency_ms) AS latency_ms
FROM llm_decision GROUP BY 1, 2 ORDER BY 1, 2;

SELECT count(DISTINCT n.llm_decision_id) AS source_linked_decisions
FROM rule_node n JOIN rule_graph g ON g.id = n.graph_id
WHERE g.source_key = 'olx_eg_wayback';

-- Evidence locators for the assessment's item-level comparisons.
SELECT id, source_url, meta->>'timestamp' AS capture_timestamp,
       blob_key, size_bytes, sha256
FROM raw_document
WHERE source_key = 'olx_eg_wayback' AND id IN (
    '3aefeb77-f183-4940-8c39-ce0f9dc4e017',
    '9211b08d-3654-4051-8a71-6197bd6121e8',
    'a0ad1086-1422-4e61-92d3-6b564bd87646',
    '54e51d6e-6b40-45d7-855d-91b0c0f2253c'
);

ROLLBACK;
