"""BlobProvider contract, run against every backend, plus backend specifics.

The S3 half runs against moto's in-process S3 server and is skipped when the
``s3``/``dev`` extras (aiobotocore, moto) are not installed.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from realestate.config.settings import BlobSettings, S3BlobSettings
from realestate.domain.blob_keys import build_blob_key, validate_blob_key
from realestate.domain.enums import RawDocumentKind
from realestate.domain.exceptions import BlobKeyError, BlobNotFoundError, ConfigurationError
from realestate.domain.ports.blob_provider import BlobProvider
from realestate.infrastructure.blob.factory import BlobProviderFactory
from realestate.infrastructure.blob.local_fs import LocalFsBlobProvider

try:
    import boto3
    from moto.server import ThreadedMotoServer

    from realestate.infrastructure.blob.s3 import S3BlobProvider
except ImportError:  # pragma: no cover - depends on installed extras
    S3_AVAILABLE = False
else:
    S3_AVAILABLE = True

_CREDENTIALS = {"access_key_id": "testing", "secret_access_key": "testing"}


@pytest.fixture(scope="module")
def s3_endpoint() -> Iterator[str]:
    if not S3_AVAILABLE:
        pytest.skip("aiobotocore/moto not installed (pip install -e '.[dev]')")
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


@pytest.fixture
def s3_bucket(s3_endpoint: str) -> str:
    """A fresh bucket per test, so tests cannot see each other's objects."""
    bucket = f"blob-{uuid.uuid4().hex[:12]}"
    boto3.client(
        "s3",
        endpoint_url=s3_endpoint,
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    ).create_bucket(Bucket=bucket)
    return bucket


def _s3_provider(endpoint: str, bucket: str, prefix: str = "raw") -> BlobProvider:
    return S3BlobProvider(
        bucket=bucket,
        prefix=prefix,
        region="us-east-1",
        endpoint_url=endpoint,
        addressing_style="path",
        **_CREDENTIALS,
    )


@pytest.fixture(params=["local_fs", "s3"])
async def provider(request: pytest.FixtureRequest, blob_root: Path) -> AsyncIterator[BlobProvider]:
    if request.param == "local_fs":
        instance: BlobProvider = LocalFsBlobProvider(blob_root)
    else:
        endpoint = request.getfixturevalue("s3_endpoint")
        instance = _s3_provider(endpoint, request.getfixturevalue("s3_bucket"))
    yield instance
    await instance.aclose()


# --- contract: every backend ------------------------------------------------


async def test_put_returns_ref_and_roundtrips(provider: BlobProvider) -> None:
    payload = '{"title": "شقة"}'.encode()

    ref = await provider.put("fixture/a.json", payload, content_type="application/json")

    assert ref.key == "fixture/a.json"
    assert ref.size_bytes == len(payload)
    assert ref.sha256 == hashlib.sha256(payload).hexdigest()
    assert ref.content_type == "application/json"
    assert ref.uri == provider.uri("fixture/a.json")
    assert await provider.get("fixture/a.json") == payload
    assert await provider.exists("fixture/a.json")


async def test_stream_reassembles_the_payload(provider: BlobProvider) -> None:
    payload = b"x" * 5000
    await provider.put("fixture/big.bin", payload, content_type="application/octet-stream")

    chunks = [chunk async for chunk in provider.stream("fixture/big.bin", chunk_size=1024)]

    assert [len(chunk) for chunk in chunks] == [1024, 1024, 1024, 1024, 904]
    assert b"".join(chunks) == payload


async def test_missing_key_raises_not_found(provider: BlobProvider) -> None:
    with pytest.raises(BlobNotFoundError):
        await provider.get("fixture/nope.json")
    with pytest.raises(BlobNotFoundError):
        _ = [chunk async for chunk in provider.stream("fixture/nope.json")]
    assert not await provider.exists("fixture/nope.json")


async def test_delete_is_idempotent(provider: BlobProvider) -> None:
    await provider.put("fixture/a.json", b"{}", content_type="application/json")

    await provider.delete("fixture/a.json")
    await provider.delete("fixture/a.json")

    assert not await provider.exists("fixture/a.json")


async def test_put_overwrites(provider: BlobProvider) -> None:
    await provider.put("fixture/a.json", b"first", content_type="application/json")
    await provider.put("fixture/a.json", b"second", content_type="application/json")

    assert await provider.get("fixture/a.json") == b"second"


@pytest.mark.parametrize(
    "key",
    ["../escape.json", "/etc/passwd", "", "a/../../b.json", "fixture//b.json", "a/./b", "a\\b"],
)
async def test_bad_keys_are_rejected_before_any_io(provider: BlobProvider, key: str) -> None:
    """A source-supplied id must never escape the root or alias another key."""
    with pytest.raises(BlobKeyError):
        await provider.put(key, b"x", content_type="text/plain")
    with pytest.raises(BlobKeyError):
        await provider.get(key)
    with pytest.raises(BlobKeyError):
        await provider.exists(key)


async def test_healthcheck_passes_on_a_reachable_store(provider: BlobProvider) -> None:
    assert await provider.healthcheck()


# --- local filesystem specifics ---------------------------------------------


async def test_local_uri_is_a_file_uri_and_writes_leave_no_temp_files(blob_root: Path) -> None:
    provider = LocalFsBlobProvider(blob_root)
    ref = await provider.put("fixture/a.json", b"first", content_type="application/json")
    await provider.put("fixture/a.json", b"second", content_type="application/json")

    assert ref.uri == (blob_root / "fixture" / "a.json").resolve().as_uri()
    assert not list(blob_root.rglob("*.tmp"))


# --- S3 specifics -------------------------------------------------------------


async def test_s3_objects_live_under_the_prefix(s3_endpoint: str, s3_bucket: str) -> None:
    provider = _s3_provider(s3_endpoint, s3_bucket, prefix="/prod/")
    try:
        ref = await provider.put("olx/2013/a.html", b"<html/>", content_type="text/html")
    finally:
        await provider.aclose()

    assert ref.key == "olx/2013/a.html"  # the database keeps the backend-neutral key
    assert ref.uri == f"s3://{s3_bucket}/prod/olx/2013/a.html"
    listing = boto3.client(
        "s3",
        endpoint_url=s3_endpoint,
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    ).head_object(Bucket=s3_bucket, Key="prod/olx/2013/a.html")
    assert listing["ContentType"] == "text/html"
    assert listing["Metadata"]["sha256"] == hashlib.sha256(b"<html/>").hexdigest()


async def test_s3_healthcheck_fails_for_a_missing_bucket(s3_endpoint: str) -> None:
    provider = _s3_provider(s3_endpoint, "no-such-bucket-here")
    try:
        assert not await provider.healthcheck()
    finally:
        await provider.aclose()


# --- factory and key policy ---------------------------------------------------


def test_factory_builds_the_configured_backend(tmp_path: Path) -> None:
    local = BlobProviderFactory.create(BlobSettings(backend="local_fs", root=str(tmp_path / "b")))
    assert isinstance(local, LocalFsBlobProvider)
    assert (tmp_path / "b").is_dir()

    if S3_AVAILABLE:
        remote = BlobProviderFactory.create(
            BlobSettings(backend="s3", s3=S3BlobSettings(bucket="raw", prefix="p"))
        )
        assert isinstance(remote, S3BlobProvider)
        assert remote.uri("a/b.json") == "s3://raw/p/a/b.json"


def test_factory_requires_a_bucket_for_s3() -> None:
    with pytest.raises(ConfigurationError, match="bucket"):
        BlobProviderFactory.create(BlobSettings(backend="s3"))


def test_key_policy_accepts_generated_keys() -> None:
    key = build_blob_key("olx_eg_wayback", RawDocumentKind.HTML)
    assert validate_blob_key(key) == key


def test_blob_keys_are_date_partitioned_and_unique() -> None:
    first = build_blob_key("dubizzle_eg", RawDocumentKind.HTML)
    second = build_blob_key("dubizzle_eg", RawDocumentKind.HTML)

    assert first.startswith("dubizzle_eg/")
    assert first.endswith(".html")
    assert len(first.split("/")) == 5  # source/yyyy/mm/dd/uuid.ext
    assert first != second
