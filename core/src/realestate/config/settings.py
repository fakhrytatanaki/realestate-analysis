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


class AuthSettings(BaseModel):
    """User accounts for the app (not the admin key, which stays separate)."""

    session_ttl_days: float = Field(default=30, gt=0)
    #: Switch off to close self-service registration.
    allow_signup: bool = True
    min_password_length: int = Field(default=10, ge=8)


class DbSettings(BaseModel):
    url: str = "postgres://realestate:realestate@127.0.0.1:5432/realestate"
    #: Generate schemas directly instead of running migrations (tests only).
    generate_schemas: bool = False


class S3BlobSettings(BaseModel):
    """Any S3-compatible store. Unset credentials use botocore's default chain
    (``AWS_*`` env vars, shared profile, instance/task role)."""

    bucket: str | None = None
    #: Key prefix inside the bucket, so several deployments can share one.
    prefix: str = ""
    region: str | None = None
    #: Set for MinIO, R2, GCS interop (``https://storage.googleapis.com``)...
    endpoint_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    session_token: str | None = None
    #: ``path`` for most self-hosted stores, ``virtual``/``auto`` for AWS.
    addressing_style: Literal["auto", "path", "virtual"] = "auto"
    max_attempts: int = 5


class BlobSettings(BaseModel):
    backend: Literal["local_fs", "s3"] = "local_fs"
    #: Root directory for ``local_fs``.
    root: str = "var/blob"
    s3: S3BlobSettings = Field(default_factory=S3BlobSettings)

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


class LlmSettings(BaseModel):
    """The model rule induction falls back to (Ollama Cloud by default).

    ``api_key`` may be left unset and supplied as ``OLLAMA_API_KEY`` instead.
    Point ``base_url`` at ``http://localhost:11434`` for a self-hosted Ollama.
    """

    provider: Literal["ollama"] = "ollama"
    base_url: str = "https://ollama.com"
    api_key: str | None = None
    model: str = "gemma4:31b"
    #: The free tier allows one request at a time; raise when the plan allows.
    max_concurrency: int = 1
    timeout_seconds: float = 600.0
    temperature: float = 0.0
    seed: int = 7
    max_retries: int = 4
    use_tools: bool = True
    num_ctx: int | None = None
    #: Size cap of the page rendering sent with each template question.
    prompt_budget_chars: int = 24_000


class ArchiveSettings(BaseModel):
    """Crawl and rule-induction knobs shared by every archive source."""

    max_captures_list: int = 4
    max_captures_detail: int = 2
    max_captures_other: int = 1
    cdx_page_size: int = 5000
    nav_batch_size: int = 12
    max_repairs: int = 2
    max_attempts: int = 3


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
    auth: AuthSettings = Field(default_factory=AuthSettings)
    db: DbSettings = Field(default_factory=DbSettings)
    blob: BlobSettings = Field(default_factory=BlobSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    archive: ArchiveSettings = Field(default_factory=ArchiveSettings)
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
