#!/usr/bin/env bash
# Run the background scraping scheduler as its own process.
set -euo pipefail
CORE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$CORE"
exec ./venv/bin/python -m realestate.worker
