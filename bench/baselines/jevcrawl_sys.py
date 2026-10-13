"""jevcrawl itself, with the real Jev API."""

from __future__ import annotations

from dataclasses import replace

from bench.baselines.common import BenchContext, SystemOutput
from bench.tasks.loader import Task
from jevcrawl.crawl import CrawlError, crawl
from jevcrawl.models import Block

NAME = "jevcrawl"


def format_blocks(blocks: list[Block]) -> str:
    """The text an agent receives from jevcrawl: each block with its source URL."""
    parts = []
    for block in blocks:
        heading = f" ({block.heading_path})" if block.heading_path else ""
        parts.append(f"Source: {block.url}{heading}\n{block.text}")
    return "\n\n---\n\n".join(parts)


async def run(task: Task, ctx: BenchContext) -> SystemOutput:
    """Crawl with jevcrawl's defaults and the shared page budget."""
    config = replace(ctx.base, cache_dir=ctx.cache_dir(task, NAME))
    if not config.api_key:
        raise RuntimeError("TYPESAFE_API_KEY is not set")
    try:
        result = await crawl(
            task.question, task.root_url, config, max_pages=int(ctx.config.limits["max_pages"])
        )
    except CrawlError as exc:
        raise RuntimeError(f"{exc.code}: {exc.message}") from exc
    stats = result.stats
    if stats.stopped_because == "auth_error":
        raise RuntimeError("auth_error: the Jev API key was rejected")
    # An agent is told to read `uncertain` before giving up, so the final LLM gets both.
    return SystemOutput(
        format_blocks(result.blocks + result.uncertain),
        [p["url"] for p in result.pages],
        jev_cost_usd=stats.jev_cost_usd,
        notes={
            "stopped_because": stats.stopped_because,
            "degraded": stats.degraded,
            "tokens_raw": stats.tokens_raw,
            "tokens_returned": stats.tokens_returned,
            "jev_calls": stats.jev_calls,
            "needs_js": len(stats.needs_js_urls),
        },
    )
