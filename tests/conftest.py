import os

import pytest

import jevcrawl.crawl.loop as loop_module
from jevcrawl.config import Config

# The isolation fixture below hides the key from tests; the live test needs the real one.
REAL_TYPESAFE_KEY = os.environ.get("TYPESAFE_API_KEY")

ENV_VARS = ("TYPESAFE_API_KEY", "JEVCRAWL_BASE_URL", "JEVCRAWL_MODEL", "JEVCRAWL_CACHE_DIR")


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    """Tests never read the developer's real key or settings."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(loop_module, "START_RETRY_DELAYS", (0.0, 0.0))  # keep the retries


@pytest.fixture
def config(tmp_path):
    """A config with its cache in a temp dir.

    One fetch at a time and no sitemap, so tests can assert the exact request order; the
    concurrency and sitemap tests switch them back on.
    """
    return Config(
        api_key="test-key",
        cache_dir=tmp_path / "cache",
        fetch_concurrency=1,
        use_sitemap=False,
    )
