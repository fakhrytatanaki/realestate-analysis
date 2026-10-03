# Dubizzle Egypt archive fixtures

The six `sanitized_capture` entries in `index.json` derive from the responses
inspected on 2026-10-03 in `docs/dubizzle-eg-wayback-plan.md`. They retain all
list advert hits, non-contact fields, and captured card structure. Only
allowlisted advert state is retained; runtime configuration, credentials,
sessions, contact objects, descriptions, images and call/chat controls are
removed. Phone/email patterns are redacted in titles and slugs.

Requested and served times are the exact captures recorded in the investigation.
Missing CDX digests and response headers are explicitly null: fixture SHA-256 is
an integrity checksum, not an Internet Archive digest. `original_bytes` records
the response size before sanitization; `sanitized_bytes` records fixture size.
The 2023 sales script intentionally ends inside an unterminated JSON string.
Its retained cards are expected evidence for the later HTML fallback tests.

Expected IDs, amounts, sale/rent and areas are recorded independently of the
rule engine from the retained JSON fields or labelled HTML cards. The detail
price/area and daily basis are the values described by that captured advert.
This is a starting regression set, not a completed hand-audit of every field.

All other entries are explicitly `synthetic`. Mixed categories, repeated IDs,
missing identity/state, empty results and challenges test rejection boundaries.
The same-ad list/detail pair derives from one sanitized hit and demonstrates
schema shapes only; it is not proof of an actual matching archived pair.
`large_payload` uses inert synthetic padding over 5 MiB to exercise the decoder;
the actual 2026 response was 5,167,456 bytes before sanitization.

Still required for the fixture gate: an independently retrieved complete 2023
sales response, a real same-ad list/detail pair, labelled rental-period and
installment examples, and a broader field audit. Do not infer payment-code or
subtype-code meanings from these samples. Seed v1 intentionally supports only
complete apartment/duplex category JSON lists; detail, truncated, empty and
challenge pages remain extraction gaps.
