# Benchmark

Does jevcrawl find answers as well as other ways of crawling, for less? Each system below crawls
from a site's home page, hands what it found to the **same answering LLM**, and the **same judge**
grades the answer, so the crawl step is the only variable. This folder is the only place in the
repository where an LLM is used.

| System | What reaches the answering LLM |
|---|---|
| `bfs` | breadth-first crawl to depth 2, every page as markdown |
| `crawl4ai` | [Crawl4AI](https://github.com/unclecode/crawl4ai)'s `BestFirstCrawlingStrategy` with a `KeywordRelevanceScorer` on the question's words, depth 3, every page as markdown |
| `llm_nav` | an LLM reads each page and picks the next link; the pages it read |
| `jevcrawl` | jevcrawl's kept and uncertain blocks |

All systems share the page budget (30 pages), jevcrawl's fetcher, its URL filters and its
segmenter, so the baselines also benefit from navigation and footer removal.

## Results

WebWalkerQA, 50 tasks verified live and never used during development, 2026-10-12:

| System | Accuracy | Gold page fetched | Pages / task | Cost / task |
|---|---|---|---|---|
| **jevcrawl** | **40%** | **54%** | 23.3 | **$0.025** |
| `llm_nav` | 30% | 33% | 9.0 | $0.194 |
| `bfs` | 8% | 11% | 29.1 | $0.228 |

Answering model `claude-opus-5-5`, judge `claude-sonnet-5-5`, both through Claude Code, so LLM
token counts are estimates and LLM costs are list-price equivalents. Jev cost is real: $0.011 per
task. Fifty tasks is a small sample: the cost gap is large and stable, the accuracy gap less so.

Crawl4AI was added after that run, so it was compared on whether it fetches a gold page
(`bench/reach.py`, no LLM), on 20 other verified unseen tasks, 2026-10-13, Crawl4AI 0.9.4:

| System | Fetched a gold page | Fetched every gold page | Median time | Sites it could not load |
|---|---|---|---|---|
| **jevcrawl** | **10/20** | **5/20** | 29 s | **0** |
| `crawl4ai` | 5/20 | 2/20 | 43 s | 3 |
| `bfs` | 2/20 | 2/20 | **14 s** | 2 |

jevcrawl fetched a gold page on 6 tasks where Crawl4AI did not, Crawl4AI on 1 where jevcrawl did
not. Crawl4AI's three failures were Chinese university sites its browser could not load. Jev
cost for the 20 tasks: $0.20. The README charts are drawn from these tables by
[`assets/build.py`](../assets/build.py).

## Running

```bash
uv sync --extra bench
export TYPESAFE_API_KEY=...
export ANTHROPIC_API_KEY=...       # optional: without it, the LLM calls go through Claude Code

# 1. keep only the tasks that are still answerable from your network today
uv run python -m bench.tasks.verify --tasks webwalkerqa --out bench/results/verified.txt

# 2. run them (leaving out the tasks looked at while developing)
uv run python bench/run.py --tasks webwalkerqa --only bench/results/verified.txt \
    --exclude bench/tasks/seen-ids.txt --limit 50 --yes --out bench/results/report.md

# re-render a report from its CSV
uv run python bench/report.py bench/results/<run>.csv
```

Runs spend money (answering, judging, navigation, Jev), so they need `--yes`. Start with
`--limit 5`. `bench/reach.py` measures only whether the systems that need no LLM (`bfs`,
`crawl4ai`, `jevcrawl`) fetched a gold page: Jev cost only, useful while changing the crawler.
The Crawl4AI baseline needs `uv sync --extra bench --extra crawl4ai && uv run crawl4ai-setup`.

**Without an Anthropic API key**, the LLM calls go through Claude Code (`claude -p`, your
login). `--llm auto` (the default) uses the API when `ANTHROPIC_API_KEY` is set and Claude Code
otherwise. Each call is a headless session with no tools and our own system prompt. Token counts
are then estimates, and the calls count against your plan's usage limits. In a Claude Code cloud
session, give the environment `TYPESAFE_API_KEY` and full network access.

## Tasks

**WebWalkerQA** (680 question/answer pairs; Wu et al., *WebWalker: Benchmarking LLMs in Web
Traversal*, ACL 2025, [arXiv:2501.07572](https://arxiv.org/abs/2501.07572)) is downloaded from Hugging Face at run time
(`callanwu/WebWalkerQA`); its license is not stated, so the data is never committed. It dates
from 2024: some sites are offline or block foreign traffic and some answer pages have moved,
which is why `verify` re-checks every task (root and gold pages load, the reference answer is
still on them) before a run. About half pass.

Your own tasks can be a JSONL file, one object per line, passed as `--tasks path/to.jsonl`:

```json
{"id": "help-1", "question": "...", "root_url": "https://...", "answer": "...", "gold_urls": ["https://..."], "lang": "en"}
```

## Metrics

Per task and system (`results/<run>.csv`): judge verdict, whether a gold URL was fetched, pages
fetched, LLM input and output tokens, LLM cost, Jev cost, total cost, judge cost (not part of
the total), wall time and context size. Prices are in `config.toml`. Every (task, system) pair
starts with empty caches, so a cached Jev answer never makes a run look free. An errored task
stays in the CSV and counts as wrong.
