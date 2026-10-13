"""The benchmark systems: ``name -> async run(task, ctx) -> SystemOutput``."""

from bench.baselines import bfs, crawl4ai_sys, jevcrawl_sys, llm_nav

SYSTEMS = {
    "bfs": bfs.run,
    "crawl4ai": crawl4ai_sys.run,
    "llm_nav": llm_nav.run,
    "jevcrawl": jevcrawl_sys.run,
}
