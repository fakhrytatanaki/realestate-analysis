#!/usr/bin/env bash
# Open a psql shell inside the Postgres container.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
[ -f .env ] && set -a && . ./.env && set +a
docker compose exec postgres psql -U "${POSTGRES_USER:-realestate}" -d "${POSTGRES_DB:-realestate}" "$@"
