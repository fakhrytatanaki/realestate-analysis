"""Blob naming policy.

Where an archived payload lives is a domain decision, not a storage-backend
detail: the same layout should hold whether the bytes end up on a local disk or
in object storage.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath

from realestate.domain.enums import RawDocumentKind
from realestate.domain.exceptions import BlobKeyError

#: File extension used when archiving each payload kind.
KIND_EXTENSIONS: dict[RawDocumentKind, str] = {
    RawDocumentKind.JSON: "json",
    RawDocumentKind.HTML: "html",
    RawDocumentKind.XML: "xml",
    RawDocumentKind.SCREENSHOT: "png",
    RawDocumentKind.OTHER: "bin",
}


def build_blob_key(
    source_key: str,
    kind: RawDocumentKind,
    *,
    when: datetime | None = None,
) -> str:
    """Date-partitioned key: ``{source}/{yyyy}/{mm}/{dd}/{uuid}.{ext}``.

    Partitioning by day keeps directories small and makes "replay everything
    since Tuesday" a directory walk rather than a full scan.
    """
    moment = when or datetime.now(UTC)
    extension = KIND_EXTENSIONS.get(kind, "bin")
    return f"{source_key}/{moment:%Y}/{moment:%m}/{moment:%d}/{uuid.uuid4().hex}.{extension}"


def validate_blob_key(key: str) -> str:
    """Return ``key`` unchanged if it is a canonical relative key, else raise.

    Every :class:`BlobProvider` applies this rule, whatever the backend, so a
    key accepted by one store is accepted by all of them, and ``a//b`` and
    ``a/b`` can never end up as two database rows pointing at one object.

    Raises:
        BlobKeyError: for an empty or absolute key, ``.``/``..``/empty
            segments, or a non-canonical spelling.
    """
    if not key or key.startswith("/") or "\\" in key or "\x00" in key:
        raise BlobKeyError(f"blob key must be a non-empty relative POSIX path: {key!r}")
    pure = PurePosixPath(key)
    if any(part in ("..", ".", "") for part in pure.parts):
        raise BlobKeyError(f"blob key must not contain empty, '.' or '..' segments: {key!r}")
    if "/".join(pure.parts) != key:
        raise BlobKeyError(f"blob key is not in canonical form: {key!r}")
    return key
