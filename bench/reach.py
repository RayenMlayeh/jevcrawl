"""Did each crawler fetch the page that holds the answer? No LLM involved.

Answer accuracy needs an answering LLM and a judge for every task. Whether a crawler *reached*
the page that holds the answer needs neither, and on WebWalkerQA it predicts accuracy well: in
our runs a system almost always answered correctly when it fetched a gold page, and never when
it did not. This compares the systems that need no LLM (``bfs``, ``crawl4ai``, ``jevcrawl``) for
the price of the Jev calls alone; confirm with ``bench/run.py``.

    uv run python bench/reach.py --only bench/results/verified.txt \\
        --exclude bench/tasks/seen-ids.txt --limit 20 --systems jevcrawl crawl4ai bfs
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # allow ``python bench/reach.py``
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.baselines import SYSTEMS
from bench.baselines.common import BenchContext, gold_fetched
from bench.llm import load_config as load_bench_config
from bench.tasks.loader import Task, load_tasks
from jevcrawl.config import load_config

NO_LLM_SYSTEMS = ["bfs", "crawl4ai", "jevcrawl"]


async def reach_one(task: Task, system: str, ctx: BenchContext) -> dict[str, Any]:
    """Run one system on one task and record whether it fetched each gold page."""
    started = time.monotonic()
    row: dict[str, Any] = {"id": task.id, "system": system}
    try:
        output = await SYSTEMS[system](task, ctx)
    except Exception as exc:  # a broken site must not stop the comparison
        return {**row, "error": f"{type(exc).__name__}: {exc}"[:200]}
    pages = output.fetched_urls
    return {
        **row,
        "gold": bool(gold_fetched(task.gold_urls, pages)),
        "gold_all": all(gold_fetched([g], pages) for g in task.gold_urls),
        "pages": len(pages),
        "jev_usd": round(output.jev_cost_usd, 5),
        "seconds": round(time.monotonic() - started),
    }


async def reach_all(tasks: list[Task], systems: list[str], parallel: int) -> list[dict[str, Any]]:
    gate = asyncio.Semaphore(parallel)
    rows: list[dict[str, Any]] = []
    with BenchContext(load_bench_config(), load_config()) as ctx:

        async def one(task: Task, system: str) -> None:
            async with gate:
                row = await reach_one(task, system, ctx)
            print(json.dumps(row), flush=True)
            rows.append(row)

        await asyncio.gather(*(one(t, s) for t in tasks for s in systems))
    return rows


def _mean(rows: list[dict[str, Any]], key: str) -> float:
    return sum(r[key] for r in rows) / len(rows) if rows else 0.0


def summary(rows: list[dict[str, Any]], systems: list[str], n_tasks: int) -> str:
    lines = [f"\n{n_tasks} tasks", "system      any gold  all gold  errors  pages  Jev $  seconds"]
    for system in systems:
        mine = [r for r in rows if r["system"] == system]
        ok = [r for r in mine if "error" not in r]
        lines.append(
            f"{system:<10}  {sum(r['gold'] for r in ok):>3}/{n_tasks:<4}  "
            f"{sum(r['gold_all'] for r in ok):>3}/{n_tasks:<4}  {len(mine) - len(ok):>6}  "
            f"{_mean(ok, 'pages'):5.1f}  {sum(r['jev_usd'] for r in ok):5.3f}  "
            f"{_mean(ok, 'seconds'):7.0f}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--tasks", default="webwalkerqa")
    parser.add_argument("--only", type=Path, help="file of task ids to keep")
    parser.add_argument("--exclude", type=Path, help="file of task ids to leave out")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--systems", nargs="+", default=["jevcrawl"], choices=NO_LLM_SYSTEMS)
    parser.add_argument("--parallel", type=int, default=3, help="runs at the same time")
    args = parser.parse_args(argv)

    tasks = load_tasks(args.tasks, args.limit, seed=args.seed, only=args.only, exclude=args.exclude)
    rows = asyncio.run(reach_all(tasks, args.systems, args.parallel))
    print(summary(rows, args.systems, len(tasks)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
