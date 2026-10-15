"""MCP server (stdio) exposing ``crawl``, ``scrape`` and ``map`` to AI agents.

Expected problems (missing key, invalid URL, unreachable start page, rejected key) are
returned as ``{"error": "<code>", "message": "<what to do>"}``; they are never uncaught
exceptions.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

try:  # mcp 2.x renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore

from jevcrawl.config import Config, load_config
from jevcrawl.crawl import CrawlError
from jevcrawl.crawl import crawl as run_crawl
from jevcrawl.crawl import map_site as run_map
from jevcrawl.crawl import scrape as run_scrape
from jevcrawl.crawl.errors import AUTH_HELP
from jevcrawl.log import configure_logging, get_logger

log = get_logger("mcp")

INSTRUCTIONS = (
    "jevcrawl crawls websites for you. A small decision model (Jev) picks which pages to read "
    "and which passages matter, so you only receive the relevant text, copied verbatim with "
    "its source URL and character offsets. Use `crawl` when the answer may be several pages "
    "deep, `scrape` for one known page, `map` to find which URLs are worth reading."
)

mcp = _Server("jevcrawl", instructions=INSTRUCTIONS, log_level="WARNING")


def _error(code: str, message: str) -> dict[str, str]:
    return {"error": code, "message": message}


def _bad_argument(**checks: tuple[Any, float]) -> dict[str, str] | None:
    for name, (value, minimum) in checks.items():
        if value < minimum:
            return _error("invalid_argument", f"`{name}` must be at least {minimum:g}.")
    return None


async def _guarded(operation: Callable[[Config], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    """Run a tool body, turning every expected failure into an error payload."""
    try:
        config = load_config()
        data = await operation(config)
    except CrawlError as exc:
        return exc.to_dict()
    except Exception as exc:  # a tool must never raise into the agent's session
        log.exception("tool failed")
        return _error("internal_error", f"{type(exc).__name__}: {exc}")
    if (data.get("stats") or {}).get("stopped_because") == "auth_error":
        return _error("auth_error", AUTH_HELP)
    return data


@mcp.tool(name="crawl")
async def crawl(
    goal: str,
    start_url: str,
    max_pages: int = 30,
    max_seconds: int = 120,
    return_tokens: int = 4000,
    max_decision_usd: float = 0.05,
) -> dict[str, Any]:
    """Crawl a website toward a goal and return only the relevant passages, verbatim.

    Use this instead of fetching pages yourself when the answer may be several pages deep
    (documentation, help centers, policies, product catalogs). A small decision model scores
    every block of text and every link, follows the most promising links first, and stops as
    soon as the collected text answers the goal. No language model runs inside the crawl.

    Args:
        goal: A specific question to answer, e.g. "How many days do I have to get a refund?".
            Vague goals ("learn about the shop") return vague results.
        start_url: Where to start; the crawl stays on this host and its subdomains.
        max_pages: Page limit. Start with 10-30.
        max_seconds: Wall-clock limit.
        return_tokens: Most text (estimated tokens) to hand back.
        max_decision_usd: Hard limit on what the decision model may cost for this call.

    Returns:
        `blocks`: relevant passages, best first. Each has `url`, `heading_path`, `text`
        (copied from the page), `char_start`/`char_end` (offsets into the page text whose
        sha256 is in `pages`) and `relevance` (0-1).
        `uncertain`: borderline passages. Read these before concluding something is missing.
        `pages`: every page fetched. `stats`: what it cost and why it stopped; check
        `stats.stopped_because` ("answered", "frontier_empty", "max_pages", "max_seconds",
        "budget") and `stats.degraded` (true means the decision model was unreachable and
        results are unscored). `stats.needs_js_urls` lists pages that only render with
        JavaScript and could not be rendered; `stats.js_rendered_urls` those it rendered.
        On failure: `{"error": code, "message": ...}`.

    Quote returned text together with its `url`.
    """
    if bad := _bad_argument(max_pages=(max_pages, 1), max_seconds=(max_seconds, 1)):
        return bad
    if bad := _bad_argument(
        return_tokens=(return_tokens, 1), max_decision_usd=(max_decision_usd, 0)
    ):
        return bad

    async def operation(config: Config) -> dict[str, Any]:
        result = await run_crawl(
            goal,
            start_url,
            config,
            max_pages=max_pages,
            max_seconds=max_seconds,
            return_tokens=return_tokens,
            max_decision_usd=max_decision_usd,
        )
        return result.to_dict()

    return await _guarded(operation)


@mcp.tool(name="scrape")
async def scrape(url: str, goal: str | None = None) -> dict[str, Any]:
    """Fetch one known page and return its text as blocks, optionally pruned to a goal.

    Without `goal`: every block of the page that survives the free filters (navigation,
    footers and repeated boilerplate removed), in page order, with no decision-model call.
    With `goal`: only the blocks that help answer it (`blocks`) plus borderline ones
    (`uncertain`); links are not followed.

    Returns the same shape as `crawl` (`blocks`, `uncertain`, `pages`, `stats`), or
    `{"error": code, "message": ...}`. Text is verbatim with `char_start`/`char_end` offsets.
    """

    async def operation(config: Config) -> dict[str, Any]:
        return (await run_scrape(url, config, goal)).to_dict()

    return await _guarded(operation)


@mcp.tool(name="map")
async def map_urls(
    start_url: str, goal: str, limit: int = 50, max_pages: int = 10
) -> dict[str, Any]:
    """Rank the URLs of a site by how likely they are to answer a goal, without returning content.

    Runs the same best-first exploration as `crawl` but only scores links. Use it to decide
    which pages deserve a `scrape`, or to see what a site offers on a topic.

    Returns `urls`: the top `limit` entries, best first, each `{url, priority, p_target,
    p_hub, anchor, found_on}` (`p_target`: the page itself likely answers the goal; `p_hub`:
    it likely leads to pages that do), plus `pages` (fetched) and `stats`. On failure:
    `{"error": code, "message": ...}`.
    """
    if bad := _bad_argument(limit=(limit, 1), max_pages=(max_pages, 1)):
        return bad

    async def operation(config: Config) -> dict[str, Any]:
        return await run_map(
            start_url,
            goal,
            config,
            limit=limit,
            max_pages=max_pages,
        )

    return await _guarded(operation)


def main() -> None:
    """Run the server on stdio (what ``jevcrawl`` with no arguments does)."""
    configure_logging("WARNING")
    mcp.run()


if __name__ == "__main__":
    main()
