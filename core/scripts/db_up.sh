#!/usr/bin/env bash
# Start the local Postgres container and block until it reports healthy.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
[ -f .env ] || cp .env.example .env
docker compose up -d postgres
printf 'waiting for postgres'
for _ in $(seq 1 60); do
  status="$(docker inspect -f '{{.State.Health.Status}}' realestatepy-postgres 2>/dev/null || echo starting)"
  if [ "$status" = "healthy" ]; then echo " -> healthy"; exit 0; fi
  printf '.'; sleep 1
done
echo " -> timed out"; docker compose logs --tail=40 postgres; exit 1
