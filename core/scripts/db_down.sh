#!/usr/bin/env bash
# Stop Postgres. Pass --volumes to also drop the data volume.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
docker compose down "$@"
