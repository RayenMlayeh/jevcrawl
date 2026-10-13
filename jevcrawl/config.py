"""Settings. Four environment variables; everything else is a sensible default."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CACHE_DIR = Path("~/.jevcrawl/cache").expanduser()


@dataclass(frozen=True)
class Thresholds:
    """Decision thresholds, calibrated on real Jev scores (see README, "How it works")."""

    keep: float = 0.35  # keep a block at or above this (recall first)
    uncertain_low: float = 0.20  # blocks in [uncertain_low, keep) are returned as "uncertain"
    stop: float = 0.85  # stop when "do the kept blocks answer the goal?" reaches this
    link_floor: float = 0.10  # links below this priority wait in a reserve
    hub_weight: float = 0.6  # weight of "leads to a section about the goal" in link priority
    # How much a page's best block score scales the links found on it. Small on purpose: on
    # listing pages the items are links, not answers, so their block scores are near 0.
    page_weight: float = 0.2
    depth_decay: float = 0.9  # per click from the start page


@dataclass(frozen=True)
class Config:
    """Resolved settings for one run."""

    api_key: str | None = None
    base_url: str = "https://api.typesafe.ai"  # any Jev-compatible endpoint works
    model: str = "jev-1.13.0"  # pinned: thresholds are calibrated per model version
    price_per_mtok: float = 0.042  # USD per million Jev input tokens; output is free
    cache_dir: Path = DEFAULT_CACHE_DIR
    same_domain: bool = True  # stay on the start host and its subdomains
    links_per_page: int = 100  # links per page that get a Jev question
    block_tokens: int = 300  # target size of a text block
    max_kept_per_page: int = 10
    fetch_concurrency: int = 4  # pages fetched at the same time
    use_sitemap: bool = True  # offer sitemap URLs that share words with the goal as links
    sitemap_links: int = 60
    keep_layout_tags: bool = False  # treat nav/aside/footer text as content
    thresholds: Thresholds = field(default_factory=Thresholds)


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Settings from ``TYPESAFE_API_KEY``, ``JEVCRAWL_BASE_URL``, ``JEVCRAWL_MODEL`` and
    ``JEVCRAWL_CACHE_DIR``; everything else keeps its default."""
    env = os.environ if env is None else env

    def get(name: str) -> str | None:
        return env.get(name, "").strip() or None

    defaults = Config()
    cache_dir = get("JEVCRAWL_CACHE_DIR")
    return Config(
        api_key=get("TYPESAFE_API_KEY"),
        base_url=(get("JEVCRAWL_BASE_URL") or defaults.base_url).rstrip("/"),
        model=get("JEVCRAWL_MODEL") or defaults.model,
        cache_dir=Path(cache_dir).expanduser() if cache_dir else defaults.cache_dir,
    )
