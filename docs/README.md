# Engineering guide

Realestatepy collects property adverts, archives their original payloads, normalizes
them into PostgreSQL, and exposes a filterable FastAPI API. The backend lives in
`core/`; there is no frontend or distributed task queue.

## Start here

| Guide | Use it for |
|---|---|
| [Development](development.md) | Local setup, code practices, tests, and an agent checklist |
| [Architecture](architecture.md) | Layers, data flow, persistence rules, and current limits |
| [Module map](modules.md) | Find the owner of a behavior; every Python implementation module |
| [Data sources](sources.md) | Existing adapters and how to add or repair a parser |
| [Operations and API](operations.md) | Configuration, endpoints, scheduling, and replay |
| [Historical sources plan](historical-sources-plan.md) | Proposal: archived olx.com.eg via Wayback, LLM-compiled rule graph |

For a first change, read the architecture, locate the relevant module and tests,
then follow the development checklist. For exact request schemas, run the API and
open `/docs` or `/openapi.json`.

These guides describe the current working-tree implementation, including the
implemented Dubizzle adapter. Older notes in `core/docs/architecture.md`, the root
README, and `CLAUDE.md` contain historical assumptions; check executable code and
tests when details differ. Keep this folder updated with behavior changes.
