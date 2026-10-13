"""Baseline 1: breadth-first crawl to depth 2; every page goes to the LLM as markdown."""

from __future__ import annotations

from dataclasses import replace

from bench.baselines.common import (
    BenchContext,
    SystemOutput,
    cut_context,
    fetch_page,
    fetch_root,
    next_links,
    render_markdown,
)
from bench.tasks.loader import Task
from jevcrawl.crawl.loop import open_session
from jevcrawl.segment.links import Scope, canonical_url

NAME = "bfs"


async def run(task: Task, ctx: BenchContext) -> SystemOutput:
    """Visit pages level by level (up to ``bfs_depth`` and ``max_pages``) and send all of them."""
    limits = ctx.config.limits
    config = replace(ctx.base, cache_dir=ctx.cache_dir(task, NAME))
    chunks: list[str] = []
    fetched: list[str] = []
    async with open_session(config) as session:
        root = await fetch_root(session, task.root_url)
        scope = Scope(root.final_url, config.same_domain)
        seen = {canonical_url(task.root_url), canonical_url(root.final_url)}
        level = [root]
        for depth in range(int(limits["bfs_depth"]) + 1):
            following = []
            for page in level:
                chunks.append(render_markdown(page))
                fetched.append(page.final_url)
                if depth < int(limits["bfs_depth"]):
                    following += next_links(page, scope, seen)
                    seen.update(link.url for link in following)
            level = []
            for link in following:
                if len(fetched) + len(level) >= int(limits["max_pages"]):
                    break
                page = await fetch_page(session, link.url)
                if page is not None:
                    seen.add(canonical_url(page.final_url))
                    level.append(page)
            if not level:
                break
    context, cut = cut_context(chunks, int(limits["max_context_tokens"]))
    return SystemOutput(context, fetched, notes={"context_cut": cut})
