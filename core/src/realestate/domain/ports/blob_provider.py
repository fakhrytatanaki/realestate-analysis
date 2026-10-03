"""Port for archiving raw payloads."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from realestate.domain.models import BlobRef


class BlobProvider(ABC):
    """Object storage for raw scrape payloads, whatever the backend.

    Implementations: ``LocalFsBlobProvider`` (a directory) and
    ``S3BlobProvider`` (any S3-compatible bucket, optionally under a prefix).
    Callers only ever see this port, so switching backends is configuration.

    Contract every implementation keeps (``tests/test_blob_provider.py`` runs
    one suite against each):

    - ``key`` is a POSIX-style relative path, checked with
      :func:`~realestate.domain.blob_keys.validate_blob_key` before any I/O;
      a rejected key raises ``BlobKeyError`` and stores nothing.
    - ``put`` is all-or-nothing: readers see the old object or the new one,
      never a partial write. The returned ``BlobRef.sha256`` is computed from
      the bytes given, not trusted from the backend.
    - A missing key raises ``BlobNotFoundError`` from ``get``/``stream``;
      ``delete`` of a missing key is a no-op.
    """

    @abstractmethod
    async def put(self, key: str, data: bytes, *, content_type: str) -> BlobRef:
        """Store ``data`` under ``key``, overwriting any existing object."""

    @abstractmethod
    async def get(self, key: str) -> bytes:
        """Read a whole object.

        Raises:
            BlobNotFoundError: if ``key`` has no object behind it.
        """

    @abstractmethod
    def stream(self, key: str, *, chunk_size: int = 65536) -> AsyncIterator[bytes]:
        """Read an object in chunks, for payloads too large to hold in memory."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Whether an object is stored under ``key``."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove an object. Deleting a missing key is a no-op."""

    @abstractmethod
    def uri(self, key: str) -> str:
        """Absolute, backend-qualified locator for ``key`` (e.g. ``file:///...``)."""

    async def healthcheck(self) -> bool:
        """Whether the backing store is reachable and writable."""
        return True

    async def aclose(self) -> None:  # noqa: B027 - optional hook, not a requirement
        """Release any held resources. Safe to call more than once."""
