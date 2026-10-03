"""S3-compatible object storage (AWS S3, MinIO, Cloudflare R2, GCS interop).

Needs the optional ``s3`` extra (``aiobotocore``); the factory imports this
module only when ``[blob] backend = "s3"``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from collections.abc import AsyncIterator
from typing import Any

from aiobotocore.session import get_session
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from realestate.domain.blob_keys import validate_blob_key
from realestate.domain.exceptions import BlobNotFoundError
from realestate.domain.models import BlobRef
from realestate.domain.ports.blob_provider import BlobProvider

_MISSING = frozenset({"404", "NoSuchKey", "NotFound"})


def _is_missing(exc: ClientError) -> bool:
    return str(exc.response.get("Error", {}).get("Code")) in _MISSING


class S3BlobProvider(BlobProvider):
    """Stores blobs as objects in one bucket, under an optional key prefix.

    A single ``PutObject`` is atomic on S3 (readers see the old object or the
    new one), which gives the same guarantee as the filesystem provider's
    rename. Credentials left as ``None`` fall through to botocore's default
    chain: ``AWS_*`` environment variables, shared profiles, then instance or
    task roles -- which is what a cloud deployment should use.

    The client is created on first use and released by :meth:`aclose`.
    """

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "",
        region: str | None = None,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        session_token: str | None = None,
        addressing_style: str = "auto",
        max_attempts: int = 5,
    ) -> None:
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        if self._prefix:
            validate_blob_key(self._prefix)
        self._client_kwargs: dict[str, Any] = {
            "region_name": region,
            "endpoint_url": endpoint_url,
            "aws_access_key_id": access_key_id,
            "aws_secret_access_key": secret_access_key,
            "aws_session_token": session_token,
            "config": Config(
                s3={"addressing_style": addressing_style},
                retries={"max_attempts": max_attempts, "mode": "standard"},
            ),
        }
        self._stack: contextlib.AsyncExitStack | None = None
        self._client: Any = None
        self._lock = asyncio.Lock()

    @property
    def bucket(self) -> str:
        return self._bucket

    def _object_key(self, key: str) -> str:
        validate_blob_key(key)
        return f"{self._prefix}/{key}" if self._prefix else key

    async def _s3(self) -> Any:
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    stack = contextlib.AsyncExitStack()
                    client = await stack.enter_async_context(
                        get_session().create_client("s3", **self._client_kwargs)
                    )
                    self._stack, self._client = stack, client
        return self._client

    async def put(self, key: str, data: bytes, *, content_type: str) -> BlobRef:
        object_key = self._object_key(key)
        digest = hashlib.sha256(data).hexdigest()
        client = await self._s3()
        await client.put_object(
            Bucket=self._bucket,
            Key=object_key,
            Body=data,
            ContentType=content_type,
            Metadata={"sha256": digest},
        )
        return BlobRef(
            key=key,
            uri=self.uri(key),
            size_bytes=len(data),
            sha256=digest,
            content_type=content_type,
        )

    async def _open(self, key: str) -> Any:
        object_key = self._object_key(key)
        client = await self._s3()
        try:
            response = await client.get_object(Bucket=self._bucket, Key=object_key)
        except ClientError as exc:
            if _is_missing(exc):
                raise BlobNotFoundError(key) from exc
            raise
        return response["Body"]

    async def get(self, key: str) -> bytes:
        body = await self._open(key)
        async with body:
            data: bytes = await body.read()
        return data

    async def stream(self, key: str, *, chunk_size: int = 65536) -> AsyncIterator[bytes]:
        body = await self._open(key)
        async with body:
            # Reads can return short chunks; re-block so callers get chunk_size.
            buffer = bytearray()
            while chunk := await body.read(chunk_size):
                buffer += chunk
                while len(buffer) >= chunk_size:
                    yield bytes(buffer[:chunk_size])
                    del buffer[:chunk_size]
            if buffer:
                yield bytes(buffer)

    async def exists(self, key: str) -> bool:
        object_key = self._object_key(key)
        client = await self._s3()
        try:
            await client.head_object(Bucket=self._bucket, Key=object_key)
        except ClientError as exc:
            if _is_missing(exc):
                return False
            raise
        return True

    async def delete(self, key: str) -> None:
        # S3 DeleteObject already succeeds for a missing key.
        object_key = self._object_key(key)
        client = await self._s3()
        await client.delete_object(Bucket=self._bucket, Key=object_key)

    def uri(self, key: str) -> str:
        return f"s3://{self._bucket}/{self._object_key(key)}"

    async def healthcheck(self) -> bool:
        try:
            client = await self._s3()
            await client.head_bucket(Bucket=self._bucket)
        except (ClientError, BotoCoreError, OSError):
            return False
        return True

    async def aclose(self) -> None:
        stack, self._stack, self._client = self._stack, None, None
        if stack is not None:
            await stack.aclose()
