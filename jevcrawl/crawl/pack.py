"""Choosing what goes back to the agent, within ``return_tokens``."""

from __future__ import annotations

from typing import Any

from jevcrawl.models import Block, CrawlResult, Stats

EXCERPT_TOKENS = 2000
UNCERTAIN_SHARE = 0.25


def by_relevance(blocks: list[Block]) -> list[Block]:
    """Highest relevance first; blocks without a score keep their order, after scored ones."""
    return sorted(blocks, key=lambda b: (b.relevance is None, -(b.relevance or 0.0)))


def cut_to_tokens(blocks: list[Block], max_tokens: int) -> list[Block]:
    """The leading blocks whose estimated tokens fit in ``max_tokens``."""
    out: list[Block] = []
    used = 0
    for block in blocks:
        if used + block.tokens_est > max_tokens:
            break
        out.append(block)
        used += block.tokens_est
    return out


def top_k_excerpts(kept: list[Block], max_tokens: int = EXCERPT_TOKENS) -> list[dict[str, Any]]:
    """The best kept blocks as ``{url, heading, text}``, for the "answered?" question."""
    chosen = cut_to_tokens(by_relevance(kept), max_tokens) or by_relevance(kept)[:1]
    return [{"url": b.url, "heading": b.heading_path, "text": b.text} for b in chosen]


def pack_result(
    kept: list[Block],
    uncertain: list[Block],
    pages: list[dict[str, Any]],
    stats: Stats,
    return_tokens: int,
) -> CrawlResult:
    """Sort, cut to ``return_tokens`` (uncertain: 25% of it) and fill ``tokens_returned``.

    When the decision model was unreachable nothing is scored and ``uncertain`` is all there is,
    so it may use the whole budget.
    """
    blocks = cut_to_tokens(by_relevance(kept), return_tokens)
    share = 1.0 if stats.degraded and not kept else UNCERTAIN_SHARE
    unsure = cut_to_tokens(by_relevance(uncertain), int(return_tokens * share))
    stats.tokens_returned = sum(b.tokens_est for b in blocks) + sum(b.tokens_est for b in unsure)
    return CrawlResult(blocks, unsure, pages, stats)
