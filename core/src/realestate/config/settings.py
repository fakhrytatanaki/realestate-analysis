"""Application settings.

Loaded from ``etc/settings.toml`` and overridable by environment variables using
the ``REALESTATE__`` prefix with ``__`` as the nesting delimiter, e.g.
``REALESTATE__DB__URL=postgres://...``. Environment wins over file, file wins
over the defaults declared here.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from realestate.config.paths import DEFAULT_SETTINGS_FILE, resolve

#: Env var pointing at an alternative settings file (used by tests).
SETTINGS_FILE_ENV = "REALESTATE_SETTINGS_FILE"


class AppSettings(BaseModel):
    name: str = "realestate-core"
    env: Literal["local", "staging", "production"] = "local"
    debug: bool = True
    #: Required in the ``X-Admin-Key`` header to trigger ingestion over HTTP.
    #: ``None`` disables the endpoint entirely rather than leaving it open.
    admin_api_key: str | None = None


class DbSettings(BaseModel):
    url: str = "postgres://realestate:realestate@127.0.0.1:5432/realestate"
    #: Generate schemas directly instead of running migrations (tests only).
    generate_schemas: bool = False


class BlobSettings(BaseModel):
    backend: Literal["local_fs"] = "local_fs"
    root: str = "var/blob"

    @property
    def root_path(self) -> Path:
        return resolve(self.root)


class LoggingSettings(BaseModel):
    level: str = "INFO"
    stdout_enabled: bool = True
    stdout_json: bool = False
    file_enabled: bool = True
    file_path: str = "var/log/app.log"

    @property
    def file_path_resolved(self) -> Path:
        return resolve(self.file_path)


class SchedulerSettings(BaseModel):
    enabled: bool = True
    #: Whether the API process also runs the scheduler, or only ``worker.py`` does.
    run_in_api: bool = False
    timezone: str = "UTC"
    misfire_grace_seconds: int = 60


class SourceSettings(BaseModel):
    """Per-source runtime configuration.

    Which sources *exist* is decided by the code registry; this only says which
    are switched on and how often they run.
    """

    enabled: bool = False
    interval_minutes: float | None = None
    crontab: str | None = None
    max_items: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class Settings(BaseSettings):
    """Root settings object, injected into every factory."""

    model_config = SettingsConfigDict(
        env_prefix="REALESTATE__",
        env_nested_delimiter="__",
        extra="ignore",
        toml_file=os.environ.get(SETTINGS_FILE_ENV) or DEFAULT_SETTINGS_FILE,
    )

    app: AppSettings = Field(default_factory=AppSettings)
    db: DbSettings = Field(default_factory=DbSettings)
    blob: BlobSettings = Field(default_factory=BlobSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    sources: dict[str, SourceSettings] = Field(default_factory=dict)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Highest precedence first: explicit init args, then env, then the TOML file.
        return (init_settings, env_settings, TomlConfigSettingsSource(settings_cls))

    def source(self, key: str) -> SourceSettings:
        """Configuration for one source, defaulting to disabled when absent."""
        return self.sources.get(key, SourceSettings())


def load_settings() -> Settings:
    """Build a fresh :class:`Settings` from file + environment."""
    return Settings()
