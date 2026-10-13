"""Crawl package: frontier, loop, stop conditions, result packing, scrape and map."""

from jevcrawl.crawl.errors import CrawlError
from jevcrawl.crawl.loop import crawl
from jevcrawl.crawl.map import map_site
from jevcrawl.crawl.scrape import scrape

__all__ = ["CrawlError", "crawl", "map_site", "scrape"]
