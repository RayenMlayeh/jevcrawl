from pathlib import Path

import pytest

from jevcrawl.config import (
    DEFAULT_CACHE_DIR,
    Config,
    Thresholds,
    load_config,
    validate_api_key_name,
)


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


def test_api_key_is_read_from_the_named_variable():
    env = {"TYPESAFE_API_KEY": "default", "MY_JEV_KEY": " mine "}
    named = load_config(env=env, api_key_name="MY_JEV_KEY")
    assert (named.api_key, named.api_key_name) == ("mine", "MY_JEV_KEY")
    unnamed = load_config(env=env)
    assert (unnamed.api_key, unnamed.api_key_name) == ("default", "TYPESAFE_API_KEY")


def test_a_named_variable_that_is_unset_does_not_fall_back_to_the_default():
    c = load_config(env={"TYPESAFE_API_KEY": "default"}, api_key_name="MY_JEV_KEY")
    assert (c.api_key, c.api_key_name) == (None, "MY_JEV_KEY")


def test_the_default_variable_is_used_only_when_no_name_is_given():
    env = {"TYPESAFE_API_KEY": "k"}
    assert load_config(env=env, api_key_name=None).api_key == "k"
    for explicit in ("", "  ", "other_name"):  # naming something else is never a silent default
        with pytest.raises(ValueError, match="invalid API key name"):
            load_config(env=env, api_key_name=explicit)


@pytest.mark.parametrize("name", ["MY_JEV_KEY", "TYPESAFE_API_KEY", "_KEY", "K", "A1_B2", "__"])
def test_posix_names_are_accepted(name):
    assert validate_api_key_name(name) == name
    assert load_config(env={name: "k"}, api_key_name=name).api_key == "k"


@pytest.mark.parametrize(
    ("name", "reasons"),
    [
        ("", ["it is empty"]),
        ("1KEY", ["starts with a digit"]),
        ("9LIVES", ["starts with a digit"]),
        ("my_key", ["lowercase letters"]),
        ("MyKey", ["lowercase letters"]),
        ("MY-KEY", ["other than A-Z, 0-9 and _"]),
        ("MY KEY", ["other than A-Z, 0-9 and _"]),
        ("MY.KEY", ["other than A-Z, 0-9 and _"]),
        ("KÉY", ["other than A-Z, 0-9 and _"]),
        ("1my-key", ["starts with a digit", "lowercase letters", "other than A-Z, 0-9 and _"]),
    ],
)  # fmt: skip
def test_names_that_are_not_posix_are_refused_and_never_repeated(name, reasons):
    with pytest.raises(ValueError) as info:
        validate_api_key_name(name)
    message = str(info.value)
    assert all(reason in message for reason in reasons)
    assert "uppercase letters" in message and "may not start with a digit" in message
    assert not name or name not in message  # a pasted key must not leak into the error
    if "starts with a digit" not in reasons:
        assert "it starts with a digit" not in message
    if "lowercase letters" not in reasons:
        assert "it has lowercase letters" not in message


def test_thresholds_match_the_calibrated_values():
    t = Thresholds()
    assert (t.keep, t.uncertain_low, t.stop, t.link_floor) == (0.35, 0.20, 0.85, 0.10)
    assert (t.hub_weight, t.page_weight, t.depth_decay) == (0.6, 0.2, 0.9)


def test_respect_robots_is_on_unless_switched_off():
    assert load_config(env={}).respect_robots is True
    for off in ("0", "false", "No", "OFF"):
        assert load_config(env={"JEVCRAWL_RESPECT_ROBOTS": off}).respect_robots is False
    assert load_config(env={"JEVCRAWL_RESPECT_ROBOTS": "1"}).respect_robots is True


def test_render_js_is_on_unless_switched_off():
    assert load_config(env={}).render_js is True
    assert load_config(env={"JEVCRAWL_RENDER_JS": "0"}).render_js is False
