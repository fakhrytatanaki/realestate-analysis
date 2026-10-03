#!/usr/bin/env bash
# Run one ingestion batch. Safe to invoke by absolute path from cron.
set -euo pipefail
CORE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$CORE"
exec ./venv/bin/python -u -m realestate.ingest "$@"
