"""Blob naming policy.

Where an archived payload lives is a domain decision, not a storage-backend
detail: the same layout should hold whether the bytes end up on a local disk or
in object storage.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from realestate.domain.enums import RawDocumentKind

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
