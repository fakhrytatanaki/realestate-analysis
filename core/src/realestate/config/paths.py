"""Filesystem anchors.

The project follows an FHS-like layout: ``etc/`` holds configuration, ``var/``
holds mutable data (blobs, logs, fixtures), ``src/`` holds code. Relative paths
in configuration are resolved against :data:`CORE_DIR`.
"""

from __future__ import annotations

from pathlib import Path

#: The ``core/`` directory -- src/realestate/config/paths.py -> core/
CORE_DIR = Path(__file__).resolve().parents[3]
ETC_DIR = CORE_DIR / "etc"
VAR_DIR = CORE_DIR / "var"
DEFAULT_SETTINGS_FILE = ETC_DIR / "settings.toml"


def resolve(path: str | Path) -> Path:
    """Turn a configured path into an absolute one, relative to ``core/``."""
    candidate = Path(path).expanduser()
    return candidate if candidate.is_absolute() else (CORE_DIR / candidate).resolve()
