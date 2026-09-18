#!/usr/bin/env bash
# Run the API with autoreload.
set -euo pipefail
CORE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$CORE"
exec ./venv/bin/uvicorn realestate.main:create_app --factory --reload \
  --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
