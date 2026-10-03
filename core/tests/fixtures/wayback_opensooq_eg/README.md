# OpenSooq Egypt archive fixtures

These six sanitized captures were retrieved on 2026-10-03. `index.json`
records requested and served timestamps, original URLs, original byte sizes,
charsets, and SHA-256 checksums of the sanitized bytes. These checksums are
fixture integrity hashes, not CDX digests.

- `2008_portal`: `/view/`, captured 2008-03-26; property sale/rent links use
  `/search/?sc2id=1` and `/search/?sc2id=14`.
- `2008_sale`: captured 2008-03-27 at 15:37:23 UTC, with twelve property rows.
  IDs in displayed order: 131, 123, 122, 121, 120, 119, 117, 116, 111, 73, 72, 46.
- `2008_rent`: captured 2008-03-27 at 15:37:44 UTC, with IDs 135 and 118.
- `2016_portal`: `/ar`, captured 2016-01-15; twelve distinct property links.
- `2025_sale`: captured 2025-07-14 at 18:23:31 UTC; thirty cards, twenty-nine
  unique IDs. First advert 259322363 asks 4,450,000 EGP for 184 m² in Cairo,
  Moqattam, with three bedrooms and three bathrooms.
- `2025_rent`: captured 2025-07-16 at 07:49:15 UTC; thirty unique cards.
  First advert 265015571 asks 3,500 EGP per day for 220 m² in Giza, Dokki,
  with three bedrooms and three bathrooms, published 13-07-2025.

The old lists are Windows-1256, retained in that encoding with their charset
declaration. Scripts, styles, embedded application state, images, contact
buttons, forms and input controls are removed; enclosing form tags are
unwrapped so that their list rows remain intact. The newer fixtures retain
the observed HTML card structure and all advert cards, including the duplicate.
They exercise HTML extraction; they do not establish a JSON extraction contract.

The 2008 lists have no price column. Their title mentioning 4000 EGP per metre
must not become a total sale price. Missing amounts remain unknown. Explicit
rental periods come from the card's metadata; missing periods remain unknown.
Existing placeholder thresholds still apply to amounts, independently of the
period field. Expected IDs and sample values above were read from the archived
HTML independently of the extraction engine.

The 2025 sales capture was served after requesting a 2016 replay; it must not
be labelled as a 2016 observation. The rental request also drifted two days.
Actual replay redirects are covered separately using mocked HTTP and these
archived bytes. Homepage response headers were not retained; the list pages'
served times were checked against `Memento-Datetime` and their final replay URLs.

Detail pages, other generations of list markup, and English layouts still need
capture-backed templates. These fixtures establish an initial adapter, not
complete coverage of every year between 2008 and 2026.
