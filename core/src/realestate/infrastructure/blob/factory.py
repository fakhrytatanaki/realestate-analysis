"""Blob provider selection."""

from __future__ import annotations

from realestate.config.settings import BlobSettings
from realestate.domain.exceptions import ConfigurationError
from realestate.domain.ports.blob_provider import BlobProvider
from realestate.infrastructure.blob.local_fs import LocalFsBlobProvider


class BlobProviderFactory:
    """Builds the configured :class:`BlobProvider`.

    Adding an S3 backend means one branch here plus one new class -- no caller
    changes, because everything downstream depends on the port.
    """

    @staticmethod
    def create(settings: BlobSettings) -> BlobProvider:
        match settings.backend:
            case "local_fs":
                root = settings.root_path
                root.mkdir(parents=True, exist_ok=True)
                return LocalFsBlobProvider(root)
            case unknown:  # pragma: no cover - guarded by the Literal type
                raise ConfigurationError(f"unknown blob backend: {unknown!r}")
