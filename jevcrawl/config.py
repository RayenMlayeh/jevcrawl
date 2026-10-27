"""Settings. Six environment variables; everything else is a sensible default."""

from __future__ import annotations

import os
import string
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CACHE_DIR = Path("~/.jevcrawl/cache").expanduser()
DEFAULT_API_KEY_NAME = "TYPESAFE_API_KEY"


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
    api_key_name: str = DEFAULT_API_KEY_NAME  # the environment variable the key is read from
    base_url: str = "https://api.typesafe.ai"  # any Jev-compatible endpoint works
    model: str = "jev-1.13.0"  # pinned: thresholds are calibrated per model version
    price_per_mtok: float = 0.042  # USD per million Jev input tokens; output is free
    cache_dir: Path = DEFAULT_CACHE_DIR
    same_domain: bool = True  # stay on the start host and its subdomains
    links_per_page: int = 100  # links per page that get a Jev question
    block_tokens: int = 300  # target size of a text block
    max_kept_per_page: int = 10
    fetch_concurrency: int = 4  # pages fetched at the same time
    respect_robots: bool = True  # skip URLs the site's robots.txt disallows
    render_js: bool = True  # render JavaScript-only pages in a browser (needs the js extra)
    max_js_renders: int = 5  # browser renders per call; each takes a few seconds
    use_sitemap: bool = True  # offer sitemap URLs that share words with the goal as links
    sitemap_links: int = 60
    keep_layout_tags: bool = False  # treat nav/aside/footer text as content
    thresholds: Thresholds = field(default_factory=Thresholds)


def validate_api_key_name(name: str) -> str:
    """``name`` if it is a POSIX environment variable name, else ``ValueError``.

    POSIX names use only uppercase letters, digits and underscores and do not start with a
    digit. The message says which rule failed but never repeats ``name``: a key pasted here by
    mistake must not end up in an error message or a log.
    """
    reasons: list[str] = []
    if not name:
        reasons.append("it is empty")
    else:
        if name[0] in string.digits:
            reasons.append("it starts with a digit")
        if any(c in string.ascii_lowercase for c in name):
            reasons.append("it has lowercase letters")
        allowed = string.ascii_uppercase + string.ascii_lowercase + string.digits + "_"
        if any(c not in allowed for c in name):
            reasons.append("it has characters other than A-Z, 0-9 and _")
    if reasons:
        raise ValueError(
            f"invalid API key name: {'; '.join(reasons)}. A name may use only uppercase letters "
            "(A-Z), digits (0-9) and underscores, and may not start with a digit, for example "
            "MY_JEV_KEY. Pass the name of the environment variable, not the key itself."
        )
    return name


def load_config(env: Mapping[str, str] | None = None, api_key_name: str | None = None) -> Config:
    """Settings from ``TYPESAFE_API_KEY``, ``JEVCRAWL_BASE_URL``, ``JEVCRAWL_MODEL``,
    ``JEVCRAWL_CACHE_DIR``, ``JEVCRAWL_RESPECT_ROBOTS`` and ``JEVCRAWL_RENDER_JS``; everything
    else keeps its default. ``api_key_name`` reads the key from that variable instead of
    ``TYPESAFE_API_KEY``. It must be a POSIX variable name (``ValueError`` otherwise), and the
    default name is used only when ``api_key_name`` is None: a named variable that is not set
    means no key."""
    env = os.environ if env is None else env
    key_name = DEFAULT_API_KEY_NAME if api_key_name is None else validate_api_key_name(api_key_name)

    def get(name: str) -> str | None:
        return env.get(name, "").strip() or None

    defaults = Config()
    cache_dir = get("JEVCRAWL_CACHE_DIR")

    def switch(name: str, default: bool) -> bool:
        value = get(name)
        return default if value is None else value.lower() not in {"0", "false", "no", "off"}

    return Config(
        api_key=get(key_name),
        api_key_name=key_name,
        base_url=(get("JEVCRAWL_BASE_URL") or defaults.base_url).rstrip("/"),
        model=get("JEVCRAWL_MODEL") or defaults.model,
        cache_dir=Path(cache_dir).expanduser() if cache_dir else defaults.cache_dir,
        respect_robots=switch("JEVCRAWL_RESPECT_ROBOTS", defaults.respect_robots),
        render_js=switch("JEVCRAWL_RENDER_JS", defaults.render_js),
    )
