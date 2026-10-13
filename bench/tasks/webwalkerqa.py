"""WebWalkerQA, downloaded from Hugging Face at runtime.

Do not commit the data: the dataset states no license. ``datasets`` caches it outside the repo
(``~/.cache/huggingface``). Install with ``pip install 'jevcrawl[bench]'``.

Columns (checked against the dataset viewer): ``question``, ``answer``, ``root_url`` and
``info`` = {domain, source_website[], golden_path[], type, difficulty_level, lang}. The pages
that hold the answer are ``info.source_website``, which we use as ``gold_urls``.
Splits: ``main`` (680 rows) and ``silver``.
"""

from __future__ import annotations

from bench.tasks.loader import Task

DATASET = "callanwu/WebWalkerQA"


def load(split: str = "main") -> list[Task]:
    """Download one split and convert it to tasks."""
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise SystemExit(
            "WebWalkerQA needs the 'datasets' package: pip install 'jevcrawl[bench]'"
        ) from exc
    rows = load_dataset(DATASET, split=split)
    tasks = []
    for index, row in enumerate(rows):
        info = row.get("info") or {}
        tasks.append(
            Task(
                id=f"wwqa-{split}-{index}",
                question=row["question"],
                root_url=row["root_url"],
                answer=row["answer"],
                gold_urls=list(info.get("source_website") or []),
                lang=info.get("lang"),
            )
        )
    return tasks
