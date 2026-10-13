"""Shared pieces of the benchmark systems."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urlsplit

from bench.llm import LLM, BenchConfig, Usage
from bench.tasks.loader import Task
from jevcrawl.config import Config
from jevcrawl.crawl.loop import Session, fetch_failure, parse_page
from jevcrawl.models import Link, Page, estimate_tokens
from jevcrawl.segment.links import Scope, canonical_url, prefilter_links


@dataclass
class SystemOutput:
    """What one system hands to the final LLM, and what getting it cost."""

    context: str
    fetched_urls: list[str] = field(default_factory=list)
    jev_cost_usd: float = 0.0
    nav_usage: Usage = field(default_factory=Usage)  # LLM calls made while navigating
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchContext:
    """Everything a system needs besides the task."""

    config: BenchConfig
    base: Config  # jevcrawl settings shared by every system
    nav_llm: LLM | None = None
    _tmp: tempfile.TemporaryDirectory[str] | None = field(default=None, repr=False)

    def cache_dir(self, task: Task, system: str) -> Path:
        """A fresh, empty cache directory for this (task, system) pair.

        Empty on purpose: a cached Jev answer must never make a later run look free.
        """
        if self._tmp is None:
            self._tmp = tempfile.TemporaryDirectory(
                prefix="jevcrawl-bench-", ignore_cleanup_errors=True
            )
        root = Path(self._tmp.name)
        path = root / f"{system}-{task.id}-{len(list(root.iterdir()))}"
        path.mkdir(parents=True)
        return path

    def __enter__(self) -> BenchContext:
        return self

    def __exit__(self, *exc: object) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None


def render_markdown(page: Page) -> str:
    """The page as simple markdown: title, source URL, then each block under its heading."""
    parts = [f"# {page.title or page.final_url}", f"Source: {page.final_url}"]
    last_heading = ""
    for block in page.blocks:
        if block.heading_path and block.heading_path != last_heading:
            depth = min(block.heading_path.count(" > ") + 2, 6)
            parts.append(f"{'#' * depth} {block.heading_path.rsplit(' > ', 1)[-1]}")
            last_heading = block.heading_path
        parts.append(block.text)
    return "\n\n".join(parts)


def cut_context(chunks: list[str], max_tokens: int) -> tuple[str, bool]:
    """Join page chunks, stopping at ``max_tokens`` (estimated). Returns (text, was_cut)."""
    kept: list[str] = []
    used = 0
    for chunk in chunks:
        tokens = estimate_tokens(chunk)
        if used + tokens > max_tokens:
            room = int((max_tokens - used) * 3.5)
            if room > 200:
                kept.append(chunk[:room])
            return "\n\n---\n\n".join(kept), True
        kept.append(chunk)
        used += tokens
    return "\n\n---\n\n".join(kept), False


def normalize_url(url: str) -> str:
    """Comparable form for the gold-URL check: no fragment, scheme-less, no trailing slash."""
    parts = urlsplit(urldefrag(url.strip())[0])
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    return f"{host}{path}" + (f"?{parts.query}" if parts.query else "")


def gold_fetched(gold_urls: list[str], fetched: list[str]) -> bool | None:
    """Was any gold page fetched? None when the task lists no gold URL."""
    if not gold_urls:
        return None
    fetched_set = {normalize_url(u) for u in fetched}
    return any(normalize_url(g) in fetched_set for g in gold_urls)


async def fetch_page(session: Session, url: str) -> Page | None:
    """Fetch and segment one page; None if it cannot be used."""
    result = await session.fetcher.fetch(url)
    if not result.ok:
        return None
    return parse_page(result, session.config)


async def fetch_root(session: Session, url: str) -> Page:
    """Like :func:`fetch_page` but raises a readable error for the root URL."""
    result = await session.fetcher.fetch(url)
    if not result.ok:
        raise RuntimeError(fetch_failure(url, result).message)
    page = parse_page(result, session.config)
    if page is None:
        raise RuntimeError(f"could not parse {url}")
    return page


def next_links(page: Page, scope: Scope, seen: set[str], limit: int | None = None) -> list[Link]:
    """Same-scope, non-trap links of a page that were not seen yet (no scoring involved)."""
    links = prefilter_links(page.links, scope, seen)
    unique: dict[str, Link] = {}
    for link in links:
        unique.setdefault(canonical_url(link.url), link)
    out = list(unique.values())
    return out[:limit] if limit else out
