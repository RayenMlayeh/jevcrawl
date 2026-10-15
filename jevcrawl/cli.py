"""Command line entry point: ``jevcrawl`` (MCP server on stdio) and ``jevcrawl <command>``."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from jevcrawl import __version__
from jevcrawl.config import Config, load_config
from jevcrawl.crawl import CrawlError, crawl, map_site, scrape
from jevcrawl.crawl.errors import AUTH_HELP
from jevcrawl.log import configure_logging


def build_parser() -> argparse.ArgumentParser:
    """The argument parser. With no command the MCP server starts."""
    parser = argparse.ArgumentParser(
        prog="jevcrawl",
        description="Crawl websites for AI agents; Jev makes every crawl decision. "
        "With no command, run the MCP server on stdio.",
    )
    parser.add_argument("--version", action="version", version=f"jevcrawl {__version__}")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="log to stderr")
    sub = parser.add_subparsers(dest="command")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--json", action="store_true", help="print the full result as JSON")

    p = sub.add_parser("crawl", help="crawl from URL toward a goal")
    p.add_argument("url")
    p.add_argument("--goal", required=True, help="a specific question to answer")
    p.add_argument("--max-pages", type=int, default=30)
    p.add_argument("--max-seconds", type=float, default=120)
    p.add_argument("--return-tokens", type=int, default=4000)
    p.add_argument("--max-usd", type=float, default=0.05, help="Jev spend limit (max_decision_usd)")
    common(p)

    p = sub.add_parser("scrape", help="fetch one page, optionally pruned to a goal")
    p.add_argument("url")
    p.add_argument("--goal")
    p.add_argument("--max-usd", type=float, default=0.05)
    common(p)

    p = sub.add_parser("map", help="rank the URLs of a site for a goal")
    p.add_argument("url")
    p.add_argument("--goal", required=True)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--max-pages", type=int, default=10)
    p.add_argument("--max-usd", type=float, default=0.05)
    common(p)

    sub.add_parser("doctor", help="check the API key and the connection to Jev")
    return parser


async def _run(args: argparse.Namespace, config: Config) -> dict[str, Any]:
    if args.command == "crawl":
        result = await crawl(
            args.goal,
            args.url,
            config,
            max_pages=args.max_pages,
            max_seconds=args.max_seconds,
            return_tokens=args.return_tokens,
            max_decision_usd=args.max_usd,
        )
        return result.to_dict()
    if args.command == "scrape":
        return (await scrape(args.url, config, args.goal, max_decision_usd=args.max_usd)).to_dict()
    return await map_site(
        args.url,
        args.goal,
        config,
        limit=args.limit,
        max_pages=args.max_pages,
        max_decision_usd=args.max_usd,
    )


def format_result(command: str, data: dict[str, Any]) -> str:
    """Human-readable rendering of a result."""
    lines: list[str] = []
    if command == "map":
        for item in data["urls"]:
            lines.append(f"{item['priority']:.3f}  {item['url']}  ({item['anchor'] or '-'})")
        if not data["urls"]:
            lines.append("(no promising links found)")
    else:
        for title, key in (("kept", "blocks"), ("uncertain", "uncertain")):
            for block in data[key]:
                score = "  -  " if block["relevance"] is None else f"{block['relevance']:.2f}"
                where = f"{block['url']} [{block['char_start']}:{block['char_end']}]"
                heading = f"  ({block['heading_path']})" if block["heading_path"] else ""
                lines.append(f"[{title} {score}] {where}{heading}")
                lines.append("  " + block["text"].replace("\n", "\n  "))
        if not (data["blocks"] or data["uncertain"]):
            lines.append("(no relevant blocks found)")
    s = data["stats"]
    lines.append("")
    summary = f"pages={s['pages_fetched']} stopped={s['stopped_because']}"
    if "tokens_raw" in s:
        summary += f" tokens={s['tokens_raw']:,}->{s['tokens_returned']:,}"
    summary += f" jev_calls={s['jev_calls']} cost=${s['jev_cost_usd']:.4f}"
    if "elapsed_seconds" in s:
        summary += f" time={s['elapsed_seconds']:.1f}s"
    if s["degraded"]:
        summary += f" degraded ({s['degraded_reason']})"
    lines.append(summary)
    if s["needs_js_urls"]:
        lines.append(f"needs JavaScript (not rendered): {', '.join(s['needs_js_urls'])}")
    if s.get("js_rendered_urls"):
        lines.append(f"rendered with JavaScript: {', '.join(s['js_rendered_urls'])}")
    return "\n".join(lines)


def _utf8_streams() -> None:
    """Page text is arbitrary Unicode; never let a legacy console encoding crash the output."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    """Console script ``jevcrawl``."""
    _utf8_streams()
    args = build_parser().parse_args(argv)
    configure_logging({0: "WARNING", 1: "INFO"}.get(args.verbose, "DEBUG"))
    if args.command is None:
        from jevcrawl.mcp_server import main as serve

        serve()
        return 0
    if args.command == "doctor":
        from jevcrawl.doctor import run_doctor

        return asyncio.run(run_doctor(load_config()))
    try:
        data = asyncio.run(_run(args, load_config()))
        if data["stats"]["stopped_because"] == "auth_error":
            raise CrawlError("auth_error", AUTH_HELP)
    except CrawlError as exc:
        error = exc.to_dict()
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"error: {error['message']}", file=sys.stderr)
        return 1
    print(
        json.dumps(data, indent=2, ensure_ascii=False)
        if args.json
        else format_result(args.command, data)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
