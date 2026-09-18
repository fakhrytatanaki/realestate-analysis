#!/usr/bin/env bash
# Apply database migrations. Bootstraps aerich on first run.
set -euo pipefail
CORE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$CORE"

if [ ! -d migrations/models ]; then
  echo "no migrations yet -> aerich init-db"
  ./venv/bin/aerich init-db
else
  ./venv/bin/aerich upgrade
fi
