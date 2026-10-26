"""The best-first crawl loop. Jev decides; no LLM runs here."""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from jevcrawl.config import Config, Thresholds
from jevcrawl.crawl.errors import AUTH_HELP, CrawlError
from jevcrawl.crawl.frontier import Frontier, FrontierItem, link_priority
from jevcrawl.crawl.pack import pack_result, top_k_excerpts
from jevcrawl.crawl.sitemap import sitemap_links, sitemap_urls
from jevcrawl.crawl.stop import Budget
from jevcrawl.decide.cache import DecisionCache
from jevcrawl.decide.client import DecisionClient, JevAuthError
from jevcrawl.decide.decider import BudgetExceeded, Decider, PageDecision
from jevcrawl.fetch.browser import Browser, Renderer, playwright_installed
from jevcrawl.fetch.http import Fetcher, FetchResult
from jevcrawl.fetch.jsdetect import MIN_VISIBLE_CHARS
from jevcrawl.fetch.robots import Robots
from jevcrawl.log import get_logger
from jevcrawl.models import Block, CrawlResult, Link, Page, Stats, estimate_tokens
from jevcrawl.segment.blocks import segment_html
from jevcrawl.segment.boilerplate import BoilerplateStore, free_filter_blocks
from jevcrawl.segment.links import Scope, canonical_url, free_filter_links

log = get_logger("crawl")


class Session:
    """Resources shared by the steps of one tool call: fetcher, caches and the Jev client."""

    def __init__(self, config: Config) -> None:
        config.cache_dir.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.fetcher = Fetcher()
        self.robots = Robots(self.fetcher)  # also read for sitemaps when robots are not respected
        self.renderer = make_renderer(config)
        self.renders_left = config.max_js_renders
        self.boilerplate = BoilerplateStore()
        self._client: DecisionClient | None = None
        self._decisions: DecisionCache | None = None

    def decider(self, stats: Stats, max_decision_usd: float) -> Decider:
        """A decider for one crawl. Raises CrawlError if there is no API key."""
        if not self.config.api_key:
            raise CrawlError("auth_error", AUTH_HELP)
        if self._client is None:
            self._client = DecisionClient(self.config)
        if self._decisions is None:
            self._decisions = DecisionCache(self.config.cache_dir / "decisions.sqlite")
        return Decider(
            self.config, self._client, self._decisions, stats, max_decision_usd=max_decision_usd
        )

    async def aclose(self) -> None:
        """Close every resource."""
        await self.fetcher.aclose()
        if self.renderer:
            await self.renderer.aclose()
        if self._client:
            await self._client.aclose()
        if self._decisions:
            self._decisions.close()


def make_renderer(config: Config) -> Renderer | None:
    """A browser for JavaScript-only pages, if enabled and the ``js`` extra is installed."""
    if not config.render_js or not playwright_installed():
        return None
    return Browser()


@asynccontextmanager
async def open_session(config: Config) -> AsyncIterator[Session]:
    """``async with open_session(config) as session:``"""
    session = Session(config)
    try:
        yield session
    finally:
        await session.aclose()


@dataclass
class LoopResult:
    """Raw output of :func:`run_loop`."""

    kept: list[Block] = field(default_factory=list)
    uncertain: list[Block] = field(default_factory=list)
    pages: list[dict[str, Any]] = field(default_factory=list)
    stats: Stats = field(default_factory=Stats)
    candidates: dict[str, dict[str, Any]] = field(default_factory=dict)


def validate_url(url: str) -> str:
    """Canonical http(s) URL, or CrawlError ``invalid_url``."""
    url = url.strip()
    if "://" not in url and " " not in url and "." in url.split("/")[0]:
        url = "https://" + url  # agents often pass "docs.example.com/path"
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise CrawlError("invalid_url", f"{url!r} is not an absolute http(s) URL.")
    return canonical_url(url)


START_RETRY_DELAYS = (1.0, 3.0)  # seconds; the start page is retried, later pages are not


def _transient(result: FetchResult) -> bool:
    error = result.error or ""
    return error in {"network", "timeout", "http_429"} or error.startswith("http_5")


async def fetch_start(fetcher: Fetcher, url: str) -> FetchResult:
    """Fetch the start URL, retrying transient failures: without it there is no crawl at all."""
    result = await fetcher.fetch(url)
    for delay in START_RETRY_DELAYS:
        if result.ok or not _transient(result):
            break
        log.info("retrying start page %s after %s", url, result.error)
        await asyncio.sleep(delay)
        result = await fetcher.fetch(url)
    return result


def fetch_failure(url: str, result: FetchResult) -> CrawlError:
    """Map a failed fetch of the start URL to an agent-facing error."""
    if result.error == "robots":
        return CrawlError(
            "blocked_by_robots",
            f"{url} is disallowed by the site's robots.txt. "
            "Set JEVCRAWL_RESPECT_ROBOTS=0 only if you are allowed to crawl it anyway.",
        )
    if result.error == "non_html":
        return CrawlError("not_html", f"{url} is not an HTML page ({result.content_type}).")
    return CrawlError("fetch_failed", f"Could not fetch {url}: {result.error}.")


def page_record(page: Page) -> dict[str, Any]:
    """``{url, title, lang, text_sha256}`` for the ``pages`` list."""
    return {
        "url": page.final_url,
        "title": page.title,
        "lang": page.lang,
        "text_sha256": page.text_sha256,
    }


def classify(
    blocks: list[Block], scores: list[float | None], profile: Thresholds, cap: int
) -> tuple[list[Block], list[Block]]:
    """Split scored blocks into (kept, uncertain).

    Blocks without a score go to uncertain. Kept blocks beyond ``cap`` per page (best first)
    move to uncertain rather than being dropped silently.
    """
    kept: list[Block] = []
    unsure: list[Block] = []
    for block, score in zip(blocks, scores, strict=True):
        if score is None:
            unsure.append(block.with_relevance(None))
        elif score >= profile.keep:
            kept.append(block.with_relevance(score))
        elif score >= profile.uncertain_low:
            unsure.append(block.with_relevance(score))
    kept.sort(key=lambda b: -(b.relevance or 0.0))
    return kept[:cap], unsure + kept[cap:]


def parse_page(result: FetchResult, config: Config) -> Page | None:
    """Segment a fetched page; None if the document is too pathological to parse."""
    assert result.html is not None
    try:
        return segment_html(
            result.html,
            result.url,
            final_url=result.final_url,
            status=result.status,
            keep_layout_tags=config.keep_layout_tags,
            block_tokens=config.block_tokens,
        )
    except RecursionError:
        log.warning("skipping %s: DOM nested too deeply", result.url)
        return None


async def render_js(session: Session, page: Page, stats: Stats) -> Page:
    """Render a page that needs JavaScript in the browser; report it if that is not possible."""
    if page.needs_js and session.renderer and session.renders_left > 0:
        session.renders_left -= 1
        rendered = await session.renderer.render(page.final_url)
        again = parse_page(rendered, session.config) if rendered.ok else None
        # Judged by text alone: a rendered app keeps its "enable JavaScript" <noscript> notice.
        if again is not None and len(again.text) >= max(MIN_VISIBLE_CHARS, len(page.text) + 1):
            again = dataclasses.replace(again, needs_js=False)
            stats.js_rendered_urls.append(page.final_url)
            return again
    if page.needs_js:
        stats.needs_js_urls.append(page.final_url)
    return page


class _Stop(Exception):
    """Ends the crawl with ``reason``: answered, frontier_empty, max_pages, max_seconds, budget
    or auth_error."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@contextmanager
def _stop_on_limits() -> Iterator[None]:
    """Turn the decider's spend limit and a rejected key into stop reasons."""
    try:
        yield
    except BudgetExceeded as exc:
        log.info("stopping: %s", exc)
        raise _Stop("budget") from exc
    except JevAuthError as exc:
        raise _Stop("auth_error") from exc


class _Crawl:
    """State of one best-first crawl; :func:`run_loop` drives it."""

    def __init__(
        self,
        goal: str,
        start: str,
        budget: Budget,
        session: Session,
        *,
        use_blocks: bool,
        use_links: bool,
        check_answered: bool,
    ) -> None:
        self.goal = goal
        self.budget = budget
        self.session = session
        self.config = session.config
        self.use_blocks = use_blocks
        self.use_links = use_links
        self.check_answered = check_answered
        self.started = time.monotonic()
        self.out = LoopResult()
        self.stats = self.out.stats
        self.decider = session.decider(self.stats, budget.max_decision_usd)
        self.frontier = Frontier()
        self.frontier.push(start, 1.0, 0)
        self.scope = Scope(start, self.config.same_domain)
        self.seen: set[str] = set()
        # Links scored below the floor wait here instead of being thrown away. Real Jev scores
        # for navigation links are low (median hub score ~0.07 on a real site), so the floor
        # alone could empty the frontier after a handful of pages with most of the budget
        # unused. The reserve is explored, best first, only when nothing above the floor is left.
        self.reserve: dict[str, FrontierItem] = {}
        self.sitemap_done = not (use_links and self.config.use_sitemap)

    async def run(self) -> LoopResult:
        try:
            while True:
                now = time.monotonic()
                if reason := self.budget.exhausted(self.stats.pages_fetched, self.started, now):
                    raise _Stop(reason)
                if not len(self.frontier):
                    if not self.reserve:
                        raise _Stop("frontier_empty")
                    log.info("frontier empty; exploring %d below-floor links", len(self.reserve))
                    promote_reserve(self.frontier, self.reserve, self.seen)
                    continue
                try:  # one slow server or Jev retry loop must not overrun max_seconds
                    async with asyncio.timeout(self.started + self.budget.max_seconds - now):
                        await self.step()
                except TimeoutError as exc:
                    raise _Stop("max_seconds") from exc
        except _Stop as stop:
            self.stats.stopped_because = stop.reason
        self.stats.elapsed_seconds = round(time.monotonic() - self.started, 3)
        return self.out

    async def step(self) -> None:
        """Fetch the best few frontier URLs at once, then decide on them in priority order."""
        room = self.budget.max_pages - self.stats.pages_fetched
        width = 1 if not self.seen else max(1, min(self.config.fetch_concurrency, room))
        batch: list[FrontierItem] = []
        while len(self.frontier) and len(batch) < width:
            item = self.frontier.pop()
            if item.url not in self.seen:
                self.seen.add(item.url)
                batch.append(item)
        fetched = await asyncio.gather(*(self.fetch(item) for item in batch))
        for item, result in zip(batch, fetched, strict=True):
            await self.process(item, result)
            if not self.sitemap_done:
                self.sitemap_done = True
                await self.seed_from_sitemap()

    async def fetch(self, item: FrontierItem) -> FetchResult:
        if self.config.respect_robots and not await self.session.robots.allowed(item.url):
            return FetchResult(item.url, item.url, error="robots")
        if item.depth == 0:
            return await fetch_start(self.session.fetcher, item.url)
        return await self.session.fetcher.fetch(item.url)

    async def ask(self, page: Page, blocks: list[Block], links: list[Link]) -> PageDecision:
        with _stop_on_limits():
            return await self.decider.page(
                self.goal, page, blocks, links, ask_blocks=self.use_blocks, ask_links=bool(links)
            )

    async def off_limits(self, item: FrontierItem, final_url: str) -> bool:
        """True when a redirect led off the crawl's hosts or into a robots-disallowed URL."""
        if item.depth == 0:
            return False  # the start page may redirect anywhere; the scope follows it
        if not self.scope.contains(final_url):
            return True
        robots = self.session.robots
        moved = final_url != item.url
        return moved and self.config.respect_robots and not await robots.allowed(final_url)

    async def process(self, item: FrontierItem, fetched: FetchResult) -> None:
        if not fetched.ok:
            if item.depth == 0:
                raise fetch_failure(item.url, fetched)
            log.info("skipping %s: %s", item.url, fetched.error)
            return
        final = canonical_url(fetched.final_url)
        if final != item.url and final in self.seen:
            return  # a redirect to a page already read
        self.seen.add(final)
        if await self.off_limits(item, final):
            log.info("skipping %s: redirected to %s", item.url, final)
            return
        page = parse_page(fetched, self.config)
        if page is None:
            return
        page = await render_js(self.session, page, self.stats)
        self.seen.add(canonical_url(page.final_url))
        if item.depth == 0:
            self.scope = Scope(page.final_url, self.config.same_domain)  # redirects move hosts
        stats = self.stats
        stats.pages_fetched += 1
        stats.tokens_raw += estimate_tokens(page.text)
        self.out.pages.append(page_record(page))

        blocks = free_filter_blocks(page, self.session.boilerplate) if self.use_blocks else []
        surviving = {b.id for b in blocks}
        links: list[Link] = []
        if self.use_links:
            links = free_filter_links(
                page.links, self.scope, self.seen, surviving, queued=self.frontier.queued,
                links_per_page=self.config.links_per_page, goal=self.goal,
            )  # fmt: skip
        decision = await self.ask(page, blocks, links)
        if self.decider.degraded and not self.frontier.is_breadth_first:
            self.frontier.breadth_first()

        profile = self.config.thresholds
        new, unsure = classify(blocks, decision.blocks, profile, self.config.max_kept_per_page)
        self.out.kept += new
        self.out.uncertain += unsure
        # With no block questions (map) there is no evidence about the page: stay neutral.
        page_rel = (
            max((s for s in decision.blocks if s is not None), default=0.0)
            if self.use_blocks
            else 1.0
        )
        self.offer(links, decision, page_rel, item.depth, page.final_url, profile)

        if self.check_answered and new and not self.decider.degraded:
            with _stop_on_limits():
                answered = await self.decider.answered(self.goal, top_k_excerpts(self.out.kept))
            if answered is not None and answered >= profile.stop:
                raise _Stop("answered")

    def offer(
        self,
        links: list[Link],
        decision: PageDecision,
        page_rel: float,
        depth: int,
        found_on: str,
        profile: Thresholds,
    ) -> None:
        """Queue scored links: above the floor into the frontier, below it into the reserve."""
        for i, link in enumerate(links):
            p_target, p_hub = decision.targets[i], decision.hubs[i]
            if p_target is None or p_hub is None:  # no scores: plain breadth-first
                priority = profile.depth_decay ** (depth + 1)
            else:
                priority = link_priority(p_target, p_hub, page_rel, depth, profile)
                if priority < profile.link_floor:
                    known = self.reserve.get(link.url)
                    if link.url not in self.frontier.queued and (
                        not known or known.priority < priority
                    ):
                        self.reserve[link.url] = FrontierItem(
                            link.url, priority, depth + 1, found_on, link.text, p_target, p_hub
                        )
                    continue
            if self.frontier.push(
                link.url, priority, depth + 1, found_on,
                anchor=link.text, p_target=p_target, p_hub=p_hub,
            ):  # fmt: skip
                self.reserve.pop(link.url, None)
                self.out.candidates[link.url] = {
                    "url": link.url,
                    "priority": round(priority, 4),
                    "p_target": None if p_target is None else round(p_target, 4),
                    "p_hub": None if p_hub is None else round(p_hub, 4),
                    "anchor": link.text,
                    "found_on": found_on,
                }

    async def seed_from_sitemap(self) -> None:
        """Offer the sitemap URLs that share words with the goal as depth-1 link candidates."""
        root = self.scope.start_url
        try:
            urls = await sitemap_urls(self.session.fetcher, self.session.robots, root, self.goal)
        except Exception as exc:  # a broken sitemap must never break the crawl
            log.info("sitemap skipped: %r", exc)
            return
        exclude = self.seen | self.frontier.queued
        links = sitemap_links(urls, self.goal, self.scope, exclude, self.config.sitemap_links)
        if not links:
            return
        listing = Page(
            url=root, final_url=root, status=200, title="Sitemap", lang="", text="",
            text_sha256="",
        )  # fmt: skip
        with _stop_on_limits():
            decision = await self.decider.page(
                self.goal, listing, [], links, ask_blocks=False, ask_links=True
            )
        # Neutral evidence (page_rel 0.5) at depth 0: as if the start page linked to them.
        self.offer(links, decision, 0.5, 0, root, self.config.thresholds)
        log.info("sitemap offered %d links", len(links))


async def run_loop(
    goal: str,
    start_url: str,
    budget: Budget,
    session: Session,
    *,
    use_blocks: bool = True,
    use_links: bool = True,
    check_answered: bool = True,
) -> LoopResult:
    """Best-first crawl. ``use_blocks``/``use_links`` choose which Jev questions are asked."""
    crawl = _Crawl(
        goal,
        validate_url(start_url),
        budget,
        session,
        use_blocks=use_blocks,
        use_links=use_links,
        check_answered=check_answered,
    )
    return await crawl.run()


def promote_reserve(frontier: Frontier, reserve: dict[str, FrontierItem], seen: set[str]) -> None:
    """Move every reserved (below-floor) link that is still unseen into the frontier."""
    for url, item in reserve.items():
        if url not in seen:
            frontier.push(
                url, item.priority, item.depth, item.parent_url,
                anchor=item.anchor, p_target=item.p_target, p_hub=item.p_hub,
            )  # fmt: skip
    reserve.clear()


async def crawl(
    goal: str,
    start_url: str,
    config: Config,
    *,
    max_pages: int = 30,
    max_seconds: float = 120,
    return_tokens: int = 4000,
    max_decision_usd: float = 0.05,
) -> CrawlResult:
    """Crawl from ``start_url`` toward ``goal`` and return the relevant blocks verbatim.

    Raises :class:`CrawlError` for expected problems (bad URL, unreachable start page, no key).
    """
    budget = Budget(max_pages, max_seconds, return_tokens, max_decision_usd)
    async with open_session(config) as session:
        out = await run_loop(goal, start_url, budget, session)
    return pack_result(out.kept, out.uncertain, out.pages, out.stats, return_tokens)
