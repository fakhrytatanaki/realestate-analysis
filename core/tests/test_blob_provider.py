"""LocalFsBlobProvider behaviour."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from realestate.domain.blob_keys import build_blob_key
from realestate.domain.enums import RawDocumentKind
from realestate.domain.exceptions import BlobKeyError, BlobNotFoundError
from realestate.infrastructure.blob.local_fs import LocalFsBlobProvider


async def test_put_returns_ref_and_roundtrips(blob_root: Path) -> None:
    provider = LocalFsBlobProvider(blob_root)
    payload = b'{"results": []}'

    ref = await provider.put("fixture/a.json", payload, content_type="application/json")

    assert ref.size_bytes == len(payload)
    assert ref.sha256 == hashlib.sha256(payload).hexdigest()
    assert ref.uri.startswith("file://")
    assert await provider.get("fixture/a.json") == payload
    assert await provider.exists("fixture/a.json")


async def test_stream_reassembles_the_payload(blob_root: Path) -> None:
    provider = LocalFsBlobProvider(blob_root)
    payload = b"x" * 5000
    await provider.put("fixture/big.bin", payload, content_type="application/octet-stream")

    chunks = [chunk async for chunk in provider.stream("fixture/big.bin", chunk_size=1024)]

    assert len(chunks) == 5
    assert b"".join(chunks) == payload


async def test_get_missing_key_raises(blob_root: Path) -> None:
    provider = LocalFsBlobProvider(blob_root)
    with pytest.raises(BlobNotFoundError):
        await provider.get("fixture/nope.json")


async def test_delete_is_idempotent(blob_root: Path) -> None:
    provider = LocalFsBlobProvider(blob_root)
    await provider.put("fixture/a.json", b"{}", content_type="application/json")

    await provider.delete("fixture/a.json")
    await provider.delete("fixture/a.json")

    assert not await provider.exists("fixture/a.json")


@pytest.mark.parametrize(
    "key", ["../escape.json", "/etc/passwd", "", "a/../../b.json", "fixture//b.json"]
)
async def test_keys_cannot_escape_the_root(blob_root: Path, key: str) -> None:
    """A source-supplied id must never be able to write outside the blob root."""
    provider = LocalFsBlobProvider(blob_root)
    with pytest.raises(BlobKeyError):
        await provider.put(key, b"x", content_type="text/plain")


async def test_put_overwrites_atomically_without_leaving_temp_files(blob_root: Path) -> None:
    provider = LocalFsBlobProvider(blob_root)
    await provider.put("fixture/a.json", b"first", content_type="application/json")
    await provider.put("fixture/a.json", b"second", content_type="application/json")

    assert await provider.get("fixture/a.json") == b"second"
    assert not list(blob_root.rglob("*.tmp"))


def test_blob_keys_are_date_partitioned_and_unique() -> None:
    first = build_blob_key("dubizzle_eg", RawDocumentKind.HTML)
    second = build_blob_key("dubizzle_eg", RawDocumentKind.HTML)

    assert first.startswith("dubizzle_eg/")
    assert first.endswith(".html")
    assert len(first.split("/")) == 5  # source/yyyy/mm/dd/uuid.ext
    assert first != second
