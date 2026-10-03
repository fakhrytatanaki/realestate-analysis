"""Gold documents as JSON label files, one per archived page.

``<root>/<source_key>/<name>.json``::

    {
      "verified": true,
      "document": {"blob_key": "olx_eg_wayback/html/...", "content_type": "text/html",
                   "source_url": "http://...", "meta": {"timestamp": "2013..."}},
      "items": [{"external_id": "487590261", "title": "...", "amount": "35500.00",
                 "currency": "EGP", "listing_type": "SALE"}]
    }

A document is either a blob in the configured store (``blob_key``: real
captures, whose HTML stays out of version control) or a gzipped file next to
the label (``file``: committed test fixtures). Only ``verified`` labels count;
``archive gold-export`` writes unverified candidates for a person to check.
"""

from __future__ import annotations

import gzip
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from realestate.domain.archive import ArchivedDocument
from realestate.domain.gold import GoldDocument, GoldItem, GoldSet
from realestate.domain.ports.blob_provider import BlobProvider


class FileGoldSet(GoldSet):
    def __init__(self, root: Path, *, blob: BlobProvider | None = None) -> None:
        self._root = root
        self._blob = blob

    async def documents(self, source_key: str) -> list[GoldDocument]:
        directory = self._root / source_key
        if not directory.is_dir():
            return []
        out: list[GoldDocument] = []
        for path in sorted(directory.glob("*.json")):
            label = json.loads(path.read_text(encoding="utf-8"))
            if not label.get("verified"):
                continue
            content = await self._content(path, label["document"])
            if content is None:
                continue
            spec = label["document"]
            out.append(
                GoldDocument(
                    ref=path.stem,
                    document=ArchivedDocument.from_payload(
                        content,
                        content_type=spec.get("content_type", "text/html"),
                        source_url=spec.get("source_url"),
                        meta=spec.get("meta") or {},
                    ),
                    items=tuple(_item(entry) for entry in label.get("items", [])),
                )
            )
        return out

    async def _content(self, label_path: Path, spec: dict[str, Any]) -> bytes | None:
        if spec.get("file"):
            data = (label_path.parent / spec["file"]).read_bytes()
            return gzip.decompress(data) if spec["file"].endswith(".gz") else data
        if spec.get("blob_key") and self._blob is not None:
            try:
                return await self._blob.get(spec["blob_key"])
            except Exception:
                return None
        return None


def _item(entry: dict[str, Any]) -> GoldItem:
    amount = entry.get("amount")
    return GoldItem(
        external_id=str(entry["external_id"]),
        title=str(entry["title"]),
        amount=Decimal(str(amount)) if amount is not None else None,
        currency=entry.get("currency"),
        listing_type=str(entry["listing_type"]).upper(),
    )


def label_for(
    *,
    document: dict[str, Any],
    items: list[dict[str, Any]],
    verified: bool = False,
    note: str | None = None,
) -> dict[str, Any]:
    """A label file's content (used by ``archive gold-export``)."""
    label: dict[str, Any] = {"verified": verified, "document": document, "items": items}
    if note:
        label["note"] = note
    return label
