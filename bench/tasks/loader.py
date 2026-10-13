"""Benchmark tasks: ``{id, question, root_url, answer, gold_urls}`` (plus optional ``lang``)."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Task:
    """One question to answer starting from a site's root URL."""

    id: str
    question: str
    root_url: str
    answer: str
    gold_urls: list[str] = field(default_factory=list)
    lang: str | None = None


def load_jsonl(path: Path) -> list[Task]:
    """Read tasks from a JSONL file; blank lines are skipped."""
    tasks = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row: dict[str, Any] = json.loads(line)
            tasks.append(
                Task(
                    id=str(row["id"]),
                    question=row["question"],
                    root_url=row["root_url"],
                    answer=row["answer"],
                    gold_urls=list(row.get("gold_urls") or []),
                    lang=row.get("lang"),
                )
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f"{path}:{number}: bad task row ({exc!r})") from exc
    return tasks


def sample(tasks: list[Task], limit: int | None, seed: int = 0) -> list[Task]:
    """A reproducible random sample of ``limit`` tasks (all of them if ``limit`` is None)."""
    if limit is None or limit >= len(tasks):
        return list(tasks)
    return random.Random(seed).sample(tasks, limit)


def load_tasks(
    source: str,
    limit: int | None = None,
    *,
    split: str = "main",
    seed: int = 0,
    only: Path | None = None,
    exclude: Path | None = None,
) -> list[Task]:
    """``source`` is ``webwalkerqa`` or a path to a JSONL file of tasks.

    ``only`` is a file of task ids, one per line (from ``bench.tasks.verify``); other tasks are
    dropped before sampling. ``exclude`` drops the ids it lists (``#`` lines are comments).
    """
    if source == "webwalkerqa":
        from bench.tasks.webwalkerqa import load

        tasks = load(split)
    else:
        tasks = load_jsonl(Path(source))
    if not tasks:
        raise SystemExit(f"No tasks found in {source!r}.")
    if only is not None:
        wanted = {line.strip() for line in only.read_text(encoding="utf-8").splitlines()}
        tasks = [task for task in tasks if task.id in wanted]
        if not tasks:
            raise SystemExit(f"None of the ids in {only} match {source!r} tasks.")
    if exclude is not None:
        lines = exclude.read_text(encoding="utf-8").splitlines()
        skip = {line.strip() for line in lines if not line.startswith("#")}
        tasks = [task for task in tasks if task.id not in skip]
    return sample(tasks, limit, seed)
