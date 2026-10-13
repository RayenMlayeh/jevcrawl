"""``scrape``: one known page, optionally pruned to the blocks that answer a goal."""

from __future__ import annotations

import time

from jevcrawl.config import Config
from jevcrawl.crawl.loop import (
    classify,
    fetch_failure,
    fetch_start,
    open_session,
    page_record,
    parse_page,
    validate_url,
)
from jevcrawl.crawl.pack import pack_result
from jevcrawl.decide.client import JevAuthError
from jevcrawl.decide.decider import BudgetExceeded
from jevcrawl.models import CrawlResult, Stats, estimate_tokens
from jevcrawl.segment.boilerplate import free_filter_blocks

STOP_SINGLE_PAGE = "single_page"  # a scrape stops because it only ever visits one page


async def scrape(
    url: str,
    config: Config,
    goal: str | None = None,
    *,
    return_tokens: int = 4000,
    max_decision_usd: float = 0.05,
) -> CrawlResult:
    """Fetch one page.

    With a ``goal``: ask Jev about the blocks only (no link questions) and return the kept and
    uncertain blocks. Without a goal: return every block that passed the free filters, with no
    Jev call at all.
    """
    start = validate_url(url)
    started = time.monotonic()
    async with open_session(config) as session:
        stats = Stats(model=config.model)
        fetched = await fetch_start(session.fetcher, start)
        if not fetched.ok:
            raise fetch_failure(start, fetched)
        page = parse_page(fetched, config)
        if page is None:
            raise fetch_failure(start, fetched)
        stats.pages_fetched = 1
        stats.tokens_raw = estimate_tokens(page.text)
        if page.needs_js:
            stats.needs_js_urls.append(page.final_url)
        blocks = free_filter_blocks(page, session.boilerplate)

        if goal is None:  # all blocks that passed the free filters, in page order, no Jev call
            stats.stopped_because = STOP_SINGLE_PAGE
            stats.tokens_returned = sum(b.tokens_est for b in blocks)
            stats.elapsed_seconds = round(time.monotonic() - started, 3)
            return CrawlResult(blocks, [], [page_record(page)], stats)

        decider = session.decider(stats, max_decision_usd)
        stats.stopped_because = STOP_SINGLE_PAGE
        try:
            decision = await decider.page(goal, page, blocks, [], ask_links=False)
            kept, unsure = classify(
                blocks, decision.blocks, config.thresholds, config.max_kept_per_page
            )
        except BudgetExceeded:
            kept, unsure, stats.stopped_because = [], blocks, "budget"
        except JevAuthError:
            kept, unsure, stats.stopped_because = [], [], "auth_error"
        stats.elapsed_seconds = round(time.monotonic() - started, 3)
        return pack_result(kept, unsure, [page_record(page)], stats, return_tokens)
