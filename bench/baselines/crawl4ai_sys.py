"""Crawl4AI's own goal-directed crawl: best-first deep crawl, links scored by goal keywords.

This is Crawl4AI's answer to "crawl toward a question without an LLM", so it is the closest
comparison to jevcrawl. Every page it fetches goes to the answering LLM as markdown.
Needs ``uv sync --extra crawl4ai`` and ``uv run crawl4ai-setup`` (it drives a browser).
"""

from __future__ import annotations

from bench.baselines.common import BenchContext, SystemOutput, cut_context
from bench.tasks.loader import Task
from jevcrawl.segment.links import goal_words

NAME = "crawl4ai"
MAX_DEPTH = 3
LAYOUT_TAGS = ["nav", "footer", "aside"]  # stripped for every system, as jevcrawl does


async def run(task: Task, ctx: BenchContext) -> SystemOutput:
    """Best-first crawl of up to ``max_pages`` pages; all their markdown goes to the LLM."""
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
    from crawl4ai.deep_crawling import BestFirstCrawlingStrategy
    from crawl4ai.deep_crawling.scorers import KeywordRelevanceScorer

    limits = ctx.config.limits
    strategy = BestFirstCrawlingStrategy(
        max_depth=MAX_DEPTH,
        include_external=False,
        url_scorer=KeywordRelevanceScorer(keywords=sorted(goal_words(task.question))),
        max_pages=int(limits["max_pages"]),
    )
    config = CrawlerRunConfig(
        deep_crawl_strategy=strategy,
        excluded_tags=LAYOUT_TAGS,
        cache_mode=CacheMode.BYPASS,
        verbose=False,
    )
    async with AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False)) as crawler:
        results = await crawler.arun(task.root_url, config=config)
    chunks: list[str] = []
    fetched: list[str] = []
    for result in results:
        if not result.success:
            continue
        fetched.append(result.url)
        chunks.append(f"Source: {result.url}\n\n{result.markdown}")
    if not fetched:
        raise RuntimeError(f"Crawl4AI could not fetch {task.root_url}")
    context, cut = cut_context(chunks, int(limits["max_context_tokens"]))
    return SystemOutput(context, fetched, notes={"context_cut": cut})
