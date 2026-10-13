"""``map``: rank the URLs of a site by how likely they are to answer a goal."""

from __future__ import annotations

from typing import Any

from jevcrawl.config import Config
from jevcrawl.crawl.loop import open_session, run_loop
from jevcrawl.crawl.stop import Budget


async def map_site(
    start_url: str,
    goal: str,
    config: Config,
    *,
    limit: int = 50,
    max_pages: int = 10,
    max_seconds: float = 120,
    max_decision_usd: float = 0.05,
) -> dict[str, Any]:
    """Run the best-first loop with link questions only (no block questions, no "answered?").

    Returns ``{"urls": [{url, priority, p_target, p_hub, anchor, found_on}, ...], "pages",
    "stats"}`` with the top ``limit`` URLs by priority. Fetched pages are not returned as content.
    """
    budget = Budget(max_pages, max_seconds, 0, max_decision_usd)
    async with open_session(config) as session:
        out = await run_loop(
            goal, start_url, budget, session, use_blocks=False, check_answered=False
        )
    ranked = sorted(out.candidates.values(), key=lambda c: -c["priority"])[:limit]
    return {"urls": ranked, "pages": out.pages, "stats": out.stats.to_dict()}
