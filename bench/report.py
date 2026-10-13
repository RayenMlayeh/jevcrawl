"""Turn a results CSV into a markdown summary table. Numbers are computed, never typed."""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

if __package__ in (None, ""):  # allow `python bench/report.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIELDS = [
    "run_id", "system", "task_id", "lang", "correct", "gold_fetched", "pages_fetched",
    "llm_input_tokens", "llm_output_tokens", "llm_cost_usd", "jev_cost_usd", "total_cost_usd",
    "judge_cost_usd", "wall_seconds", "context_tokens", "notes", "error",
]  # fmt: skip


def _num(value: str) -> float | None:
    return float(value) if value not in ("", None) else None


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def summarize(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    """One summary row per system, in order of first appearance."""
    systems: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        systems.setdefault(row["system"], []).append(row)
    out = []
    for system, group in systems.items():
        ok = [r for r in group if not r["error"]]
        graded = [_num(r["correct"]) or 0.0 for r in group]  # an errored task counts as wrong
        gold = [_num(r["gold_fetched"]) for r in ok if r["gold_fetched"] != ""]

        def avg(column: str, rows_: list[dict[str, str]] = ok) -> float | None:
            return _mean([v for r in rows_ if (v := _num(r[column])) is not None])

        out.append(
            {
                "system": system,
                "tasks": len(group),
                "errors": len(group) - len(ok),
                "accuracy": _mean(graded),
                "gold": _mean([g for g in gold if g is not None]),
                "pages": avg("pages_fetched"),
                "llm_in": avg("llm_input_tokens"),
                "jev_cost": avg("jev_cost_usd"),
                "total_cost": avg("total_cost_usd"),
                "wall": avg("wall_seconds"),
            }
        )
    return out


def _fmt(value: object, kind: str) -> str:
    if value is None:
        return "n/a"
    assert isinstance(value, int | float)
    return {
        "pct": f"{value:.1%}",
        "int": f"{value:,.0f}",
        "one": f"{value:.1f}",
        "usd": f"${value:.5f}",
    }[kind]


def render(rows: list[dict[str, str]], *, title: str, notes: list[str]) -> str:
    """The markdown report."""
    lines = [f"# {title}", ""]
    lines += [f"- {note}" for note in notes] + [""]
    lines += [
        "| System | Tasks | Errors | Accuracy | Gold page fetched | Pages fetched "
        "| LLM input tokens | Jev cost / task | Total cost / task | Wall time (s) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summarize(rows):
        lines.append(
            f"| {s['system']} | {s['tasks']} | {s['errors']} | {_fmt(s['accuracy'], 'pct')} "
            f"| {_fmt(s['gold'], 'pct')} | {_fmt(s['pages'], 'one')} | {_fmt(s['llm_in'], 'int')} "
            f"| {_fmt(s['jev_cost'], 'usd')} | {_fmt(s['total_cost'], 'usd')} "
            f"| {_fmt(s['wall'], 'one')} |"
        )
    lines += [
        "",
        "How to read this table: accuracy counts an errored task as wrong; every other column is "
        "a mean over the tasks that did not error. LLM input tokens are the answering LLM's input "
        "plus, for `llm_nav`, the navigation calls. Total cost = answering/navigation LLM + Jev; "
        "the judge is excluded because it is the same for every system. Generated from the CSV "
        "next to this file; do not edit by hand.",
        "",
    ]
    return "\n".join(lines)


def read_rows(path: Path) -> list[dict[str, str]]:
    """Read a results CSV."""
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def main(argv: list[str] | None = None) -> int:
    """``python bench/report.py results.csv [--out report.md]``."""
    parser = argparse.ArgumentParser(description="Render a benchmark results CSV as markdown.")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--out", type=Path, default=Path("bench/report.md"))
    args = parser.parse_args(argv)
    rows = read_rows(args.csv)
    notes = [f"Source: `{args.csv.name}` ({len(rows)} rows)"]
    args.out.write_text(render(rows, title="jevcrawl benchmark", notes=notes), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
