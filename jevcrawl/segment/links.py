"""Links: resolve and canonicalize, then the free URL prefilter and the per-page cap."""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

from w3lib.url import canonicalize_url

from jevcrawl.models import Link

BINARY_EXTENSIONS = frozenset(
    [
        "pdf",
        "zip",
        "gz",
        "tgz",
        "tar",
        "rar",
        "7z",
        "bz2",
        "xz",
        "exe",
        "msi",
        "dmg",
        "pkg",
        "deb",
        "rpm",
        "apk",
        "iso",
        "bin",
        "png",
        "jpg",
        "jpeg",
        "gif",
        "webp",
        "svg",
        "ico",
        "bmp",
        "tif",
        "tiff",
        "avif",
        "heic",
        "mp3",
        "mp4",
        "m4a",
        "m4v",
        "wav",
        "ogg",
        "oga",
        "ogv",
        "webm",
        "avi",
        "mov",
        "mkv",
        "flac",
        "aac",
        "wmv",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "ppt",
        "pptx",
        "odt",
        "ods",
        "odp",
        "rtf",
        "epub",
        "woff",
        "woff2",
        "ttf",
        "otf",
        "eot",
    ]
)

# Whole-token matches, so "/login" and "/my-account" are skipped but "/accounting" is not.
# A path segment is a trap when it is short (at most two words) and one of its words is a trap
# word: "/login", "/my-account", "/cart", "login.php". A long article slug that merely mentions
# one ("keeping-your-account-and-data-secure") is content, not a trap.
_TRAP_WORDS = frozenset(
    "logout login signin signup register cart checkout account wp-admin replytocom calendar".split()
)
_TRAP_QUERY = re.compile(r"(?:^|&)(?:sort|filter|facet|replytocom)=", re.IGNORECASE)
_SEGMENT_SPLIT = re.compile(r"[-_.]+")
_SKIPPED_SCHEMES = ("javascript:", "mailto:", "tel:", "data:", "sms:", "ftp:", "file:")


def resolve_link(href: str, base_url: str) -> str | None:
    """Absolute, canonical http(s) URL without a fragment, or None if ``href`` is not crawlable."""
    href = href.strip()
    if not href or href.startswith("#") or href.lower().startswith(_SKIPPED_SCHEMES):
        return None
    absolute = urldefrag(urljoin(base_url, href))[0]
    if urlsplit(absolute).scheme not in {"http", "https"}:
        return None
    return canonicalize_url(absolute)


def canonical_url(url: str) -> str:
    """Canonical form used for the ``seen`` set and the frontier."""
    return canonicalize_url(urldefrag(url)[0])


@dataclass(frozen=True)
class Scope:
    """Which hosts a crawl may visit: the start host and its subdomains."""

    start_url: str
    same_domain: bool = True

    @property
    def host(self) -> str:
        """Lowercase hostname of the start URL without ``www.``, so sibling subdomains count."""
        return (urlsplit(self.start_url).hostname or "").lower().removeprefix("www.")

    def contains(self, url: str) -> bool:
        """True if ``url`` is inside the crawl scope."""
        if not self.same_domain:
            return True
        host = (urlsplit(url).hostname or "").lower()
        return host == self.host or host.endswith("." + self.host)


def is_skippable(url: str) -> bool:
    """True for binary files and URL patterns that are traps or private areas."""
    parts = urlsplit(url)
    last = parts.path.rsplit("/", 1)[-1]
    if "." in last and last.rsplit(".", 1)[-1].lower() in BINARY_EXTENSIONS:
        return True
    if _TRAP_QUERY.search(parts.query):
        return True
    return any(_is_trap_segment(segment) for segment in parts.path.split("/"))


def _is_trap_segment(segment: str) -> bool:
    segment = segment.lower()
    if segment.startswith("wp-admin") or segment.startswith("wp-login"):
        return True
    words = [w for w in _SEGMENT_SPLIT.split(segment) if w]
    return 0 < len(words) <= 2 and any(w in _TRAP_WORDS for w in words)


def prefilter_links(
    links: Iterable[Link],
    scope: Scope,
    seen: Collection[str] = (),
    queued: Collection[str] = (),
) -> list[Link]:
    """Free URL prefilter: scope, binary extensions, trap patterns, seen or queued URLs."""
    return [
        link
        for link in links
        if scope.contains(link.url)
        and not is_skippable(link.url)
        and link.url not in seen
        and link.url not in queued
    ]


_WORD = re.compile(r"\w+", re.UNICODE)
_STOPWORDS = frozenset(
    (
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "do",
        "does",
        "did",
        "can",
        "how",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "i",
        "we",
        "you",
        "my",
        "our",
        "your",
        "it",
        "its",
        "this",
        "that",
        "at",
        "by",
        "from",
        "as",
        "get",
        "have",
        "has",
        "will",
        "would",
        "should",
        "about",
    )
)


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


_CJK_RUN = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]+")


def _terms(text: str) -> set[str]:
    """Lowercase content words; Chinese/Japanese runs (no spaces) become character bigrams."""
    terms = {f"{run[i]}{run[i + 1]}" for run in _CJK_RUN.findall(text) for i in range(len(run) - 1)}
    rest = _CJK_RUN.sub(" ", text)
    for match in _WORD.finditer(rest):
        word = match.group().lower()
        if len(word) > 2 and word not in _STOPWORDS:
            terms.add(_stem(word))
    return terms


def goal_words(goal: str | None) -> frozenset[str]:
    """Distinct content terms of a goal: words, or character bigrams for Chinese/Japanese."""
    return frozenset(_terms(goal)) if goal else frozenset()


def goal_overlap(words: frozenset[str], link: Link) -> int:
    """How many goal terms appear in the link's text or URL path (a free, lexical hint)."""
    if not words:
        return 0
    return len(words & _terms(f"{link.text} {unquote(urlsplit(link.url).path)}"))


def cap_links(
    links: Iterable[Link],
    limit: int,
    surviving_block_ids: Collection[str],
    goal: str | None = None,
) -> list[Link]:
    """Keep at most ``limit`` links, de-duplicated by URL.

    Preference order: non-empty anchor text, then links inside blocks that survived the free
    block filters, then more words shared with the goal (in the anchor or the URL path), then
    earlier position. The result is returned in page order. The goal hint is lexical only: it
    decides which links of a large menu are worth a Jev question, not which are followed.
    """
    words = goal_words(goal)

    def rank(link: Link) -> tuple[bool, bool, int, int]:
        return (
            not link.text,
            link.block_id not in surviving_block_ids,
            -goal_overlap(words, link),
            link.position,
        )

    best: dict[str, Link] = {}
    for link in links:
        current = best.get(link.url)
        if current is None or rank(link) < rank(current):
            best[link.url] = link
    chosen = sorted(best.values(), key=rank)[:limit]
    return sorted(chosen, key=lambda link: link.position)


def free_filter_links(
    links: Iterable[Link],
    scope: Scope,
    seen: Collection[str],
    surviving_block_ids: Collection[str],
    *,
    queued: Collection[str] = (),
    links_per_page: int = 40,
    goal: str | None = None,
) -> list[Link]:
    """Prefilter, then cap."""
    return cap_links(
        prefilter_links(links, scope, seen, queued), links_per_page, surviving_block_ids, goal
    )
