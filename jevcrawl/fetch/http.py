"""HTTP fetching: HTML pages only, redirects followed, bodies capped at 5 MB."""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from jevcrawl import REPO_URL, __version__
from jevcrawl.log import get_logger

log = get_logger("fetch")

USER_AGENT = f"jevcrawl/{__version__} (+{REPO_URL})"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
MAX_BODY_BYTES = 5 * 1024 * 1024
TIMEOUT = 15.0

_HEADER_CHARSET = re.compile(r"charset\s*=\s*[\"']?([\w.:-]+)", re.IGNORECASE)
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([\w.:-]+)""", re.IGNORECASE)


@dataclass(frozen=True)
class FetchResult:
    """Outcome of one page fetch. ``error`` is None on success."""

    url: str
    final_url: str
    status: int = 0
    html: str | None = None
    content_type: str | None = None
    error: str | None = None  # non_html, http_<status>, network, too_many_redirects

    @property
    def ok(self) -> bool:
        """True when ``html`` holds a usable page."""
        return self.error is None and self.html is not None


def decode_body(body: bytes, content_type: str | None) -> str:
    """Decode with the header charset, then the meta charset, then UTF-8 with replacement."""
    header = _HEADER_CHARSET.search(content_type or "")
    meta = _META_CHARSET.search(body[:4096])
    for name in (
        header.group(1) if header else None,
        meta.group(1).decode("ascii") if meta else None,
    ):
        if name:
            try:
                return body.decode(name, errors="replace")
            except LookupError:
                continue
    return body.decode("utf-8", errors="replace")


def _media_type(content_type: str | None) -> str:
    return (content_type or "").split(";")[0].strip().lower()


async def _read_capped(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= MAX_BODY_BYTES:
            break
    return b"".join(chunks)[:MAX_BODY_BYTES]


class Fetcher:
    """Fetches pages with one shared HTTP client."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            http2=True, follow_redirects=True, max_redirects=5, timeout=TIMEOUT, headers=HEADERS
        )

    async def __aenter__(self) -> Fetcher:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Release the HTTP client (if this object created it)."""
        if self._owns_client:
            await self._client.aclose()

    async def fetch(self, url: str) -> FetchResult:
        """Fetch one HTML page. Never raises for network or HTTP problems."""
        try:
            async with self._client.stream("GET", url) as response:
                final_url = str(response.url)
                content_type = response.headers.get("content-type")
                if response.status_code != 200:
                    return FetchResult(url, final_url, response.status_code,
                                       error=f"http_{response.status_code}")  # fmt: skip
                if _media_type(content_type) not in HTML_TYPES:
                    return FetchResult(url, final_url, 200, content_type=content_type,
                                       error="non_html")  # fmt: skip
                body = await _read_capped(response)
        except httpx.TooManyRedirects:
            return FetchResult(url, url, error="too_many_redirects")
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            log.info("request to %s failed: %s", url, type(exc).__name__)
            return FetchResult(url, url, error="network")
        html = decode_body(body, content_type)
        return FetchResult(url, final_url, 200, html, content_type)

    async def fetch_raw(self, url: str) -> bytes | None:
        """GET any file (robots.txt, sitemap.xml); its body on HTTP 200, else None."""
        try:
            async with self._client.stream("GET", url) as response:
                return await _read_capped(response) if response.status_code == 200 else None
        except (httpx.HTTPError, httpx.InvalidURL):
            return None
