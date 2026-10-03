"""Feeds links found on recognised pages back into the crawl frontier."""

from __future__ import annotations

from collections.abc import Sequence

from realestate.domain.archive import DiscoveredLink, LinkRequest, surt_key
from realestate.domain.enums import LinkRel
from realestate.domain.ports.archive import (
    CrawlFrontierRepository,
    LinkRequestRepository,
    LinkSink,
)

#: When one page links a URL in several roles, the most specific wins.
_REL_ORDER = {LinkRel.DETAIL: 0, LinkRel.LIST: 1, LinkRel.PAGINATION: 2}


class FrontierLinkSink(LinkSink):
    """Turns discovered links into routing evidence on matching captures.

    Links are matched by canonical URL key against captures already enumerated
    from the archive index. A link no capture matches is recorded as a lookup
    request (when a request repository is given): the crawl later asks the
    index for that exact URL near the linking page's capture time, so adverts
    the domain-wide enumeration missed are not silently lost.
    """

    def __init__(
        self, frontier: CrawlFrontierRepository, requests: LinkRequestRepository | None = None
    ) -> None:
        self._frontier = frontier
        self._requests = requests

    async def offer(
        self,
        source_key: str,
        links: Sequence[DiscoveredLink],
        *,
        parent_timestamp: str | None = None,
    ) -> int:
        best: dict[str, LinkRel] = {}
        urls: dict[str, str] = {}
        for link in links:
            key = surt_key(link.url)
            current = best.get(key)
            if current is None or _REL_ORDER[link.rel] < _REL_ORDER[current]:
                best[key] = link.rel
                urls[key] = link.url
        matched = await self._frontier.add_evidence(source_key, best)
        if self._requests is not None and matched < len(best):
            known = await self._frontier.known_url_keys(source_key, list(best))
            await self._requests.request(
                [
                    LinkRequest(
                        source_key=source_key,
                        url_key=key,
                        url=urls[key],
                        rel=rel,
                        parent_timestamp=parent_timestamp,
                    )
                    for key, rel in best.items()
                    if key not in known
                ]
            )
        return matched
