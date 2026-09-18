"""Tortoise ORM configuration.

Imported both by the application at startup and by ``aerich`` at the command
line (see ``[tool.aerich]`` in ``pyproject.toml``), so the two can never drift.
"""

from __future__ import annotations

import os
from typing import Any

from realestate.config.settings import load_settings

#: Every module Tortoise must scan for models. ``aerich.models`` is required so
#: aerich can track applied migrations in the same database.
MODEL_MODULES = ["realestate.infrastructure.db.models", "aerich.models"]


def build_tortoise_config(db_url: str | None = None) -> dict[str, Any]:
    """Assemble the Tortoise config dict for a database URL."""
    url = db_url or os.environ.get("REALESTATE__DB__URL") or load_settings().db.url
    return {
        "connections": {"default": url},
        "apps": {
            "models": {
                "models": MODEL_MODULES,
                "default_connection": "default",
            }
        },
        "use_tz": True,
        "timezone": "UTC",
    }


#: Module-level config consumed by aerich.
TORTOISE_ORM = build_tortoise_config()
