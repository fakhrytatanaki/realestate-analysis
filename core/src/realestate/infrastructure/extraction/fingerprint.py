"""Structural fingerprints: which page template a document was rendered from.

A 64-bit simhash over ``parent>child`` element signatures (tag plus first
class), weighted logarithmically so a list of 50 identical rows does not drown
out the page chrome. Text is ignored, so two captures of the same template
years apart land a few bits apart, while a redesign lands far away. Gap
clustering and the ``template_fp`` condition both rely on that.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selectolax.lexbor import LexborNode

    from realestate.infrastructure.extraction.document import ParsedDocument

_IGNORED_TAGS = frozenset({"script", "style", "noscript", "svg", "path", "link", "meta", "br"})
_BITS = 64


def _signature(node: LexborNode) -> str:
    classes = (node.attributes.get("class") or "").split()
    return f"{node.tag}.{classes[0]}" if classes else str(node.tag)


def structural_features(document: ParsedDocument) -> Counter[str]:
    features: Counter[str] = Counter()
    root = document.tree.root
    if root is None:
        return features
    for node in root.traverse(include_text=False):
        tag = node.tag or ""
        if tag.startswith("-") or tag in _IGNORED_TAGS:
            continue
        parent = node.parent
        parent_signature = _signature(parent) if parent is not None and parent.tag else "^"
        features[f"{parent_signature}>{_signature(node)}"] += 1
        element_id = node.attributes.get("id")
        if element_id and not any(char.isdigit() for char in element_id):
            features[f"#{element_id}"] += 1
    for name in document.scripts:
        if not name.startswith("window."):
            features[f"script:{name}"] += 1
    return features


def simhash(features: Counter[str]) -> int:
    vector = [0.0] * _BITS
    for feature, count in features.items():
        weight = 1.0 + math.log2(count)
        digest = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
        for bit in range(_BITS):
            vector[bit] += weight if digest >> bit & 1 else -weight
    return sum(1 << bit for bit in range(_BITS) if vector[bit] > 0)


def structural_fingerprint(document: ParsedDocument) -> str:
    """16 hex characters; compare with :func:`fingerprint_distance`."""
    return f"{simhash(structural_features(document)):016x}"


def fingerprint_distance(left: str, right: str) -> int:
    """Hamming distance in bits; 64 when either side is malformed."""
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return _BITS
