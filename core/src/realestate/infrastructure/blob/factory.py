"""Blob provider selection."""

from __future__ import annotations

from realestate.config.settings import BlobSettings
from realestate.domain.exceptions import ConfigurationError
from realestate.domain.ports.blob_provider import BlobProvider
from realestate.infrastructure.blob.local_fs import LocalFsBlobProvider


class BlobProviderFactory:
    """Builds the configured :class:`BlobProvider`.

    A new backend is one branch here plus one class implementing the port --
    no caller changes, because everything downstream depends on the port.
    """

    @staticmethod
    def create(settings: BlobSettings) -> BlobProvider:
        match settings.backend:
            case "local_fs":
                root = settings.root_path
                root.mkdir(parents=True, exist_ok=True)
                return LocalFsBlobProvider(root)
            case "s3":
                s3 = settings.s3
                if not s3.bucket:
                    raise ConfigurationError('[blob] backend = "s3" needs [blob.s3] bucket')
                try:
                    # Imported lazily so local installs don't need the extra.
                    from realestate.infrastructure.blob.s3 import S3BlobProvider
                except ImportError as exc:
                    raise ConfigurationError(
                        "the s3 blob backend needs aiobotocore: pip install 'realestate[s3]'"
                    ) from exc
                return S3BlobProvider(
                    bucket=s3.bucket,
                    prefix=s3.prefix,
                    region=s3.region,
                    endpoint_url=s3.endpoint_url,
                    access_key_id=s3.access_key_id,
                    secret_access_key=s3.secret_access_key,
                    session_token=s3.session_token,
                    addressing_style=s3.addressing_style,
                    max_attempts=s3.max_attempts,
                )
            case unknown:  # pragma: no cover - guarded by the Literal type
                raise ConfigurationError(f"unknown blob backend: {unknown!r}")
