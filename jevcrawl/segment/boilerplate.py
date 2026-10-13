"""Free block filters: repeated template boilerplate and link-dense blocks."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from urllib.parse import urldefrag, urlsplit

from jevcrawl.models import Block, Page
from jevcrawl.segment.blocks import MAX_LINK_DENSITY

BOILERPLATE_MIN_PAGES = 3

_DIGITS = re.compile(r"\d+")


def block_hash(text: str) -> str:
    """Hash after lowercasing, collapsing whitespace and replacing each digit run by ``0``."""
    normalized = _DIGITS.sub("0", " ".join(text.lower().split()))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def domain_of(url: str) -> str:
    """Per-domain key: the lowercase hostname."""
    return (urlsplit(url).hostname or "").lower()


class BoilerplateStore:
    """Per-crawl memory of which block hashes appear on how many distinct pages of a domain."""

    def __init__(self) -> None:
        self._pages: dict[tuple[str, str], set[str]] = {}

    def observe(self, domain: str, page_url: str, hashes: Iterable[str]) -> set[str]:
        """Record that ``hashes`` appear on ``page_url``; return those now seen on 3+ pages."""
        page_url = urldefrag(page_url)[0]
        found = set()
        for h in set(hashes):
            pages = self._pages.setdefault((domain, h), set())
            pages.add(page_url)
            if len(pages) >= BOILERPLATE_MIN_PAGES:
                found.add(h)
        return found

    def pages_seen(self, domain: str, block_hash_: str) -> int:
        """Number of distinct pages of ``domain`` on which the hash has been seen."""
        return len(self._pages.get((domain, block_hash_), ()))


def link_density(block: Block) -> float:
    """Share of the block's characters that sit inside ``<a>`` elements."""
    return block.link_chars / len(block.text) if block.text else 0.0


def free_filter_blocks(page: Page, store: BoilerplateStore | None) -> list[Block]:
    """Drop boilerplate (seen on 3+ pages of the domain) and link-dense blocks.

    The page's blocks are recorded in ``store`` first, so a block that appears on its third
    page is dropped from that page on. Pass ``store=None`` to skip the boilerplate memory.
    """
    boilerplate: set[str] = set()
    hashes = {b.id: block_hash(b.text) for b in page.blocks}
    if store is not None:
        boilerplate = store.observe(domain_of(page.final_url), page.final_url, hashes.values())
    return [
        b
        for b in page.blocks
        if hashes[b.id] not in boilerplate and link_density(b) <= MAX_LINK_DENSITY
    ]
