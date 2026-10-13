"""Run the benchmark: every system answers every task with the same LLM and the same judge.

    uv run python bench/run.py --tasks webwalkerqa --limit 50 --yes

Runs call an LLM (answering, judging, llm_nav) and, for ``jevcrawl``, the Jev API, so they spend
money; pass ``--yes`` to confirm.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import importlib.util
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # allow `python bench/run.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench import judge as judging
from bench.baselines import SYSTEMS
from bench.baselines.common import BenchContext, SystemOutput, gold_fetched
from bench.llm import LLM, BenchConfig, Usage, load_config, make_llm, resolve_provider
from bench.report import FIELDS, render
from bench.tasks.loader import Task, load_tasks
from jevcrawl.config import Config
from jevcrawl.config import load_config as load_jevcrawl_config
from jevcrawl.models import estimate_tokens

BENCH_DIR = Path(__file__).parent
RESULTS_DIR = BENCH_DIR / "results"
PROVIDER_NOTES = {
    "claude-code": [
        "LLM calls went through `claude -p` (Claude Code login, no API key). LLM token counts are "
        "estimates of the prompt sent (chars / 3.5) plus reported output tokens, and LLM costs are "
        "those tokens at the `[prices]` list prices: notional, not what your plan charges.",
    ],
}
DEFAULT_SYSTEMS = ["bfs", "crawl4ai", "llm_nav", "jevcrawl"]


async def evaluate(
    task: Task,
    system_name: str,
    ctx: BenchContext,
    answer_llm: LLM,
    judge_llm: LLM,
    run_id: str,
    **run_kwargs: Any,
) -> dict[str, Any]:
    """Run one system on one task, answer it, judge it; returns one CSV row."""
    row: dict[str, Any] = {name: "" for name in FIELDS}
    row.update(run_id=run_id, system=system_name, task_id=task.id, lang=task.lang or "")
    started = time.monotonic()
    try:
        output: SystemOutput = await SYSTEMS[system_name](task, ctx, **run_kwargs)
        reply = await judging.answer(answer_llm, task.question, output.context)
        row["wall_seconds"] = round(time.monotonic() - started, 3)
        verdict = await judging.judge(judge_llm, task.question, task.answer, reply.text)
    except Exception as exc:  # recorded, never hidden: the report counts errors per system
        row["wall_seconds"] = round(time.monotonic() - started, 3)
        row["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return row

    llm = Usage()
    llm.add(reply.usage)
    llm.add(output.nav_usage)
    gold = gold_fetched(task.gold_urls, output.fetched_urls)
    row.update(
        correct=int(verdict.correct),
        gold_fetched="" if gold is None else int(gold),
        pages_fetched=len(output.fetched_urls),
        llm_input_tokens=llm.input_tokens,
        llm_output_tokens=llm.output_tokens,
        llm_cost_usd=round(llm.cost_usd, 8),
        jev_cost_usd=round(output.jev_cost_usd, 8),
        total_cost_usd=round(llm.cost_usd + output.jev_cost_usd, 8),
        judge_cost_usd=round(verdict.usage.cost_usd, 8),
        context_tokens=estimate_tokens(output.context),
        notes=json.dumps(output.notes, sort_keys=True),
    )
    return row


def build_context(bench: BenchConfig, nav_llm: LLM | None) -> BenchContext:
    """The shared context for all systems: jevcrawl settings and the navigation LLM."""
    base: Config = load_jevcrawl_config()
    return BenchContext(bench, base, nav_llm)


async def run_all(
    tasks: list[Task],
    systems: list[str],
    ctx: BenchContext,
    answer_llm: LLM,
    judge_llm: LLM,
    csv_path: Path,
    run_id: str,
) -> list[dict[str, Any]]:
    """Evaluate every (task, system) pair, appending each row to the CSV as it finishes."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    with csv_path.open("w", newline="", encoding="utf-8") as handle, ctx:
        writer = csv.DictWriter(handle, FIELDS)
        writer.writeheader()
        for index, task in enumerate(tasks, 1):
            for name in systems:
                row = await evaluate(task, name, ctx, answer_llm, judge_llm, run_id)
                writer.writerow(row)
                handle.flush()
                rows.append(row)
                status = row["error"] or f"correct={row['correct']}"
                print(f"[{index}/{len(tasks)}] {name:<15} {task.id}: {status}", flush=True)
    return rows


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--tasks", default="webwalkerqa", help="webwalkerqa | own | path/to.jsonl")
    parser.add_argument("--split", default="main", help="WebWalkerQA split (main or silver)")
    parser.add_argument("--limit", type=int, help="run a reproducible sample of this many tasks")
    parser.add_argument(
        "--only", type=Path, help="file of task ids to keep (see python -m bench.tasks.verify)"
    )
    parser.add_argument(
        "--exclude", type=Path, help="file of task ids to leave out (bench/tasks/seen-ids.txt)"
    )
    parser.add_argument("--systems", nargs="+", default=DEFAULT_SYSTEMS, choices=list(SYSTEMS))
    parser.add_argument("--config", type=Path, help="bench config (default bench/config.toml)")
    parser.add_argument("--out", type=Path, help="report path (default bench/report.md)")
    parser.add_argument("--yes", action="store_true", help="confirm that this run spends money")
    parser.add_argument(
        "--llm",
        choices=["config", "auto", "anthropic", "claude-code"],
        default="config",
        help="LLM provider for answering/judging/navigation (default: [llm].provider)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = parse_args(argv)
    bench = load_config(args.config)
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    if not args.yes:
        print(
            "This run calls an LLM (answering, judging, llm_nav) and the Jev API. "
            "It spends money. Re-run with --yes to confirm.",
            file=sys.stderr,
        )
        return 2
    if "crawl4ai" in args.systems and importlib.util.find_spec("crawl4ai") is None:
        print(
            "The crawl4ai system needs Crawl4AI: uv sync --extra bench --extra crawl4ai && "
            "uv run crawl4ai-setup. Or leave it out: --systems bfs llm_nav jevcrawl",
            file=sys.stderr,
        )
        return 2
    tasks = load_tasks(
        args.tasks,
        args.limit,
        split=args.split,
        seed=bench.seed,
        only=args.only,
        exclude=args.exclude,
    )
    provider = resolve_provider(args.llm, bench)
    answer_llm, judge_llm, nav_llm = (
        make_llm(provider, r, bench) for r in ("answer", "judge", "nav")
    )
    ctx = build_context(bench, nav_llm)
    csv_path = RESULTS_DIR / f"{run_id}.csv"
    rows = asyncio.run(run_all(tasks, args.systems, ctx, answer_llm, judge_llm, csv_path, run_id))
    notes = [
        f"Run `{run_id}`, tasks: `{args.tasks}`"
        f"{' (split ' + args.split + ')' if args.tasks == 'webwalkerqa' else ''}, "
        f"{len(tasks)} tasks, sample seed {bench.seed}"
        f"{f', only ids in `{args.only.name}`' if args.only else ''}",
        f"LLM provider `{provider}`: answering `{answer_llm.model}`, judge `{judge_llm.model}`, "
        f"effort `{bench.llm['effort']}`; same page budget ({bench.limits['max_pages']}) "
        "for all systems",
        *PROVIDER_NOTES.get(provider, []),
        f"Raw rows: `results/{csv_path.name}`",
    ]
    out = args.out or BENCH_DIR / "report.md"
    out.write_text(
        render(_as_strings(rows), title="jevcrawl benchmark", notes=notes),
        encoding="utf-8",
    )
    print(f"wrote {out}")
    return 0


def _as_strings(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{k: "" if v is None else str(v) for k, v in row.items()} for row in rows]


if __name__ == "__main__":
    raise SystemExit(main())
