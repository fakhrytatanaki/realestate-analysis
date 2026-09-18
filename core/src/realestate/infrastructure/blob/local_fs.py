"""Local filesystem blob storage."""

from __future__ import annotations

import contextlib
import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path, PurePosixPath

import aiofiles
import aiofiles.os

from realestate.domain.exceptions import BlobKeyError, BlobNotFoundError
from realestate.domain.models import BlobRef
from realestate.domain.ports.blob_provider import BlobProvider


class LocalFsBlobProvider(BlobProvider):
    """Stores blobs as files under a single root directory.

    Writes go to a temporary sibling and are then ``os.replace``d into place, so
    a crashed scrape never leaves a half-written payload that a later parse pass
    would happily read.
    """

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    @property
    def root(self) -> Path:
        return self._root

    def _path_for(self, key: str) -> Path:
        """Resolve ``key`` inside the root, rejecting traversal attempts."""
        if not key or key.startswith("/"):
            raise BlobKeyError(f"blob key must be a non-empty relative path: {key!r}")
        pure = PurePosixPath(key)
        if any(part in ("..", ".", "") for part in pure.parts):
            raise BlobKeyError(f"blob key must not contain empty, '.' or '..' segments: {key!r}")
        # Insist on the canonical spelling, so "a//b" and "a/b" can never end up
        # as two database rows pointing at one file.
        if "/".join(pure.parts) != key:
            raise BlobKeyError(f"blob key is not in canonical form: {key!r}")
        candidate = (self._root / Path(*pure.parts)).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise BlobKeyError(f"blob key escapes the storage root: {key!r}")
        return candidate

    async def put(self, key: str, data: bytes, *, content_type: str) -> BlobRef:
        path = self._path_for(key)
        await aiofiles.os.makedirs(path.parent, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        digest = hashlib.sha256(data)
        try:
            async with aiofiles.open(temp_path, "wb") as handle:
                await handle.write(data)
                await handle.flush()
            os.replace(temp_path, path)
        except BaseException:
            # Best-effort cleanup; the original error is what matters.
            with contextlib.suppress(OSError):
                os.unlink(temp_path)
            raise
        return BlobRef(
            key=key,
            uri=self.uri(key),
            size_bytes=len(data),
            sha256=digest.hexdigest(),
            content_type=content_type,
        )

    async def get(self, key: str) -> bytes:
        path = self._path_for(key)
        try:
            async with aiofiles.open(path, "rb") as handle:
                return await handle.read()
        except FileNotFoundError as exc:
            raise BlobNotFoundError(key) from exc

    async def stream(self, key: str, *, chunk_size: int = 65536) -> AsyncIterator[bytes]:
        path = self._path_for(key)
        try:
            handle = await aiofiles.open(path, "rb")
        except FileNotFoundError as exc:
            raise BlobNotFoundError(key) from exc
        try:
            while chunk := await handle.read(chunk_size):
                yield chunk
        finally:
            await handle.close()

    async def exists(self, key: str) -> bool:
        return await aiofiles.os.path.isfile(self._path_for(key))

    async def delete(self, key: str) -> None:
        try:
            await aiofiles.os.remove(self._path_for(key))
        except FileNotFoundError:
            pass

    def uri(self, key: str) -> str:
        return self._path_for(key).as_uri()

    async def healthcheck(self) -> bool:
        try:
            await aiofiles.os.makedirs(self._root, exist_ok=True)
        except OSError:
            return False
        return os.access(self._root, os.W_OK)

