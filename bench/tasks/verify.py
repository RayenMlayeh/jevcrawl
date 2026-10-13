"""Keep only the tasks that can still be answered from here, today.

WebWalkerQA was built in 2024; since then some sites went offline, block foreign traffic, or moved
the page that held the answer. A task where no system can even load the gold page measures the
network, not the crawler. This script checks every task live:

1. the root URL loads (HTTP 200, HTML);
2. every gold URL loads;
3. the reference answer is still on the gold pages: at least ``--min-support`` of its content
   words (character bigrams for Chinese/Japanese) occur in their text.

It writes the passing task ids, one per line, plus a JSON file with the reason for every task.
Run it, then benchmark only those ids::

    python -m bench.tasks.verify --tasks webwalkerqa --out bench/results/verified-main.txt
    python bench/run.py --tasks webwalkerqa --only bench/results/verified-main.txt --limit 10 --yes

The output lives in ``bench/results/`` (git-ignored): it depends on your network and the date.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from selectolax.lexbor import LexborHTMLParser

from bench.tasks.loader import Task, load_tasks
from jevcrawl.fetch.http import Fetcher

_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")
_WORD = re.compile(r"\w+", re.UNICODE)
_STOPWORDS = frozenset(
    "the and for with that this from was were are has have had its their his her they which "
    "who what when where during also been into than then there these those about after".split()
)


@dataclass
class Check:
    """Why a task passed or failed."""

    id: str
    ok: bool
    reason: str
    support: float | None = None


def answer_units(answer: str) -> set[str]:
    """What must appear on the gold pages: CJK character bigrams plus other content words."""
    units: set[str] = set()
    for run in re.findall(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]+", answer):
        units.update(run[i : i + 2] for i in range(len(run) - 1))
    for word in _WORD.findall(_CJK.sub(" ", answer).lower()):
        if (len(word) >= 3 and word not in _STOPWORDS) or word.isdigit():
            units.add(word)
    return units


def support(answer: str, text: str) -> float:
    """Share of the answer's units found in ``text`` (1.0 for an answer with no units)."""
    units = answer_units(answer)
    if not units:
        return 1.0
    haystack = text.lower()
    return sum(unit in haystack for unit in units) / len(units)


def page_text(html: str) -> str:
    """All visible text, menus included: the check is about presence, not relevance."""
    tree = LexborHTMLParser(html)
    for node in tree.css("script, style, noscript, template"):
        node.decompose()
    body = tree.body or tree.root
    return body.text(separator=" ") if body is not None else ""


async def check_task(task: Task, fetcher: Fetcher, min_support: float) -> Check:
    root = await fetcher.fetch(task.root_url)
    if not root.ok:
        return Check(task.id, False, f"root: {root.error}")
    if not task.gold_urls:
        return Check(task.id, False, "no gold urls")
    texts = []
    for url in task.gold_urls:
        page = await fetcher.fetch(url)
        if not page.ok or page.html is None:
            return Check(task.id, False, f"gold {url}: {page.error}")
        texts.append(page_text(page.html))
    score = support(task.answer, "\n".join(texts))
    if score < min_support:
        return Check(task.id, False, "answer no longer on gold pages", round(score, 2))
    return Check(task.id, True, "ok", round(score, 2))


async def verify(tasks: list[Task], min_support: float, concurrency: int) -> list[Check]:
    gate = asyncio.Semaphore(concurrency)
    done = 0
    async with Fetcher() as fetcher:

        async def one(task: Task) -> Check:
            nonlocal done
            async with gate:
                try:
                    result = await check_task(task, fetcher, min_support)
                except Exception as exc:  # one odd site must not stop the sweep
                    result = Check(task.id, False, f"error: {exc!r}")
            done += 1
            if done % 25 == 0:
                print(f"  checked {done}/{len(tasks)}", file=sys.stderr)
            return result

        return list(await asyncio.gather(*(one(task) for task in tasks)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--tasks", default="webwalkerqa", help="webwalkerqa | own | path/to.jsonl")
    parser.add_argument("--split", default="main")
    parser.add_argument("--out", type=Path, required=True, help="where to write passing ids")
    parser.add_argument("--min-support", type=float, default=0.6)
    parser.add_argument("--concurrency", type=int, default=16)
    args = parser.parse_args(argv)

    tasks = load_tasks(args.tasks, None, split=args.split)
    checks = asyncio.run(verify(tasks, args.min_support, args.concurrency))
    passing = [c.id for c in checks if c.ok]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(f"{i}\n" for i in passing), encoding="utf-8")
    details = args.out.with_suffix(".json")
    details.write_text(
        json.dumps([c.__dict__ for c in checks], ensure_ascii=False, indent=1), encoding="utf-8"
    )
    reasons: dict[str, int] = {}
    for c in checks:
        key = c.reason.split(":")[0] if not c.ok else "ok"
        reasons[key] = reasons.get(key, 0) + 1
    print(f"{len(passing)}/{len(checks)} tasks still answerable -> {args.out}")
    print("  " + ", ".join(f"{k}: {v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
