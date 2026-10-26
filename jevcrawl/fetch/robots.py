"""robots.txt: which URLs the site asks crawlers to stay out of."""

from __future__ import annotations

from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from jevcrawl.fetch.http import Fetcher

AGENT = "jevcrawl"


class Robots:
    """Reads each host's robots.txt once and answers whether a URL may be fetched.

    A missing, unreadable or empty robots.txt allows everything.
    """

    def __init__(self, fetcher: Fetcher) -> None:
        self._fetcher = fetcher
        self._bodies: dict[str, bytes | None] = {}
        self._parsers: dict[str, RobotFileParser | None] = {}

    async def body(self, origin: str) -> bytes | None:
        """The raw robots.txt of ``origin`` (``scheme://host``), fetched once; None if missing."""
        if origin not in self._bodies:
            self._bodies[origin] = await self._fetcher.fetch_raw(origin + "/robots.txt")
        return self._bodies[origin]

    async def allowed(self, url: str) -> bool:
        """True if ``url`` may be fetched."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._parsers:
            body = await self.body(origin)
            parser: RobotFileParser | None = None
            if body:
                parser = RobotFileParser()
                parser.parse(body.decode("utf-8", "replace").splitlines())
            self._parsers[origin] = parser
        parser = self._parsers[origin]
        return parser is None or parser.can_fetch(AGENT, url)
