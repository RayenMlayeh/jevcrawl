from pathlib import Path

from jevcrawl.config import DEFAULT_CACHE_DIR, Config, Thresholds, load_config


def test_defaults():
    c = load_config(env={})
    assert (c.base_url, c.model, c.api_key) == ("https://api.typesafe.ai", "jev-1.13.0", None)
    assert c.cache_dir == DEFAULT_CACHE_DIR
    assert (c.links_per_page, c.fetch_concurrency, c.use_sitemap) == (100, 4, True)
    assert c.thresholds == Thresholds()


def test_the_four_environment_variables():
    c = load_config(
        env={
            "TYPESAFE_API_KEY": " k ",
            "JEVCRAWL_BASE_URL": "http://localhost:11435/",
            "JEVCRAWL_MODEL": "decider:0.8b",
            "JEVCRAWL_CACHE_DIR": "/tmp/jc",
        }
    )
    assert (c.api_key, c.base_url, c.model) == ("k", "http://localhost:11435", "decider:0.8b")
    assert c.cache_dir == Path("/tmp/jc")


def test_blank_values_mean_unset():
    assert load_config(env={"TYPESAFE_API_KEY": "  ", "JEVCRAWL_MODEL": ""}) == Config()


def test_thresholds_match_the_calibrated_values():
    t = Thresholds()
    assert (t.keep, t.uncertain_low, t.stop, t.link_floor) == (0.35, 0.20, 0.85, 0.10)
    assert (t.hub_weight, t.page_weight, t.depth_decay) == (0.6, 0.2, 0.9)
