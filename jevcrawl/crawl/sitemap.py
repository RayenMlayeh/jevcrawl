"""Free discovery of deep pages from the site's own sitemap.xml.

Benchmark questions (and real ones) often target a page several clicks deep: one event among
hundreds, behind a calendar and "next page" links. Many sites list every page in a sitemap, and
the URL often names the page ("/events/open-session-equity-1-february-10-2023/"). We read the
sitemap once per crawl, keep the URLs whose path shares words with the goal (a free, lexical
filter) and hand those few to Jev as ordinary link candidates. Jev still decides; nothing is
fetched just because it is in the sitemap.
"""

from __future__ import annotations

import gzip
import html
import re
from urllib.parse import unquote, urljoin, urlsplit

from jevcrawl.fetch.http import Fetcher
from jevcrawl.log import get_logger
from jevcrawl.models import Link
from jevcrawl.segment.links import Scope, canonical_url, goal_overlap, goal_words, is_skippable

log = get_logger("crawl.sitemap")

DEFAULT_PATHS = ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml")
SITEMAP_CONTEXT = "Listed in the site's sitemap (no anchor text; words come from the URL)"
MAX_FILES = 12  # sitemap files read per crawl (an index can list hundreds)
MAX_URLS = 50_000
_LOC = re.compile(rb"<loc>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</loc>", re.I | re.S)
_ROBOTS_SITEMAP = re.compile(r"^\s*sitemap\s*:\s*(\S+)", re.I | re.M)
_SEPARATORS = re.compile(r"[-_+.]+")
_EXTENSION = re.compile(r"\.(?:s?html?|php|aspx?|jsp)$", re.I)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _decode(body: bytes) -> bytes:
    if body[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(body)
        except (OSError, EOFError):
            return b""
    return body


async def _sitemap_files(fetcher: Fetcher, root_url: str) -> list[str]:
    """Sitemaps named in robots.txt, or the usual locations if it names none."""
    origin = _origin(root_url)
    body = await fetcher.fetch_raw(origin + "/robots.txt")
    if body:
        named = _ROBOTS_SITEMAP.findall(body.decode("utf-8", "replace"))
        if named:
            return [urljoin(origin, url) for url in named]
    return [origin + path for path in DEFAULT_PATHS]


async def sitemap_urls(fetcher: Fetcher, root_url: str, goal: str) -> list[str]:
    """Page URLs listed in the site's sitemaps (index files are followed, best-matching first)."""
    words = goal_words(goal)
    queue = await _sitemap_files(fetcher, root_url)
    done: set[str] = set()
    urls: list[str] = []
    while queue and len(done) < MAX_FILES and len(urls) < MAX_URLS:
        file_url = queue.pop(0)
        if file_url in done:
            continue
        done.add(file_url)
        raw = await fetcher.fetch_raw(file_url)
        body = _decode(raw) if raw else b""
        if not body:
            continue
        locs = [html.unescape(m.decode("utf-8", "replace")).strip() for m in _LOC.findall(body)]
        if b"<sitemapindex" in body[:4096].lower():
            # Child sitemaps whose names share words with the goal ("event-sitemap.xml") first.
            ranked = sorted(locs, key=lambda u: -goal_overlap(words, Link("", u, "", "", 0)))
            queue = ranked + queue
        else:
            urls.extend(locs)
    log.info("sitemap: %d files read, %d urls", len(done), len(urls))
    return urls[:MAX_URLS]


def slug_text(url: str) -> str:
    """Readable words from a URL path: ``events / open session equity 1 february 10 2023``."""
    segments = [s for s in unquote(urlsplit(url).path).split("/") if s]
    words = [_SEPARATORS.sub(" ", _EXTENSION.sub("", s)).strip() for s in segments[-3:]]
    return " / ".join(w for w in words if w)


def sitemap_links(
    urls: list[str],
    goal: str,
    scope: Scope,
    exclude: set[str],
    limit: int,
) -> list[Link]:
    """The ``limit`` sitemap URLs sharing the most words with the goal, as Jev link candidates.

    A URL must share at least two goal words (one if the goal has fewer than four), so a site
    with numeric URLs ("/info/2124/10671.htm") contributes nothing rather than noise.
    """
    words = goal_words(goal)
    if not words or limit <= 0:
        return []
    need = 1 if len(words) < 4 else 2
    scored: list[tuple[int, int, Link]] = []
    for position, raw in enumerate(dict.fromkeys(urls)):
        url = canonical_url(raw)
        if url in exclude or not scope.contains(url) or is_skippable(url):
            continue
        link = Link("", url, slug_text(url), SITEMAP_CONTEXT, position)
        overlap = goal_overlap(words, link)
        if overlap >= need:
            scored.append((-overlap, position, link))
    scored.sort(key=lambda row: (row[0], row[1]))
    best = [link for _, _, link in scored[:limit]]
    return [
        Link(f"S{i}", link.url, link.text, link.context, link.position)
        for i, link in enumerate(best)
    ]
