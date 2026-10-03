"""Feeds links found on recognised pages back into the crawl frontier."""

from __future__ import annotations

from collections.abc import Sequence

from realestate.domain.archive import DiscoveredLink, surt_key
from realestate.domain.enums import LinkRel
from realestate.domain.ports.archive import CrawlFrontierRepository, LinkSink

#: When one page links a URL in several roles, the most specific wins.
_REL_ORDER = {LinkRel.DETAIL: 0, LinkRel.LIST: 1, LinkRel.PAGINATION: 2}


class FrontierLinkSink(LinkSink):
    """Turns discovered links into routing evidence on matching captures.

    Links are matched by canonical URL key against captures already enumerated
    from the archive index, so a link to a page the archive never captured is
    simply dropped -- no extra archive requests.
    """

    def __init__(self, frontier: CrawlFrontierRepository) -> None:
        self._frontier = frontier

    async def offer(self, source_key: str, links: Sequence[DiscoveredLink]) -> int:
        best: dict[str, LinkRel] = {}
        for link in links:
            key = surt_key(link.url)
            current = best.get(key)
            if current is None or _REL_ORDER[link.rel] < _REL_ORDER[current]:
                best[key] = link.rel
        return await self._frontier.add_evidence(source_key, best)
