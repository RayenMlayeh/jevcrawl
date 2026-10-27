"""The command line, run in-process against the fixture site and the fake Jev."""

import asyncio
import io
import json

import pytest

from jevcrawl.cli import _utf8_streams, main
from tests.fixtures.fake_site import BASE, mount_site
from tests.fixtures.jev_fake import FakeJev

GOAL = "How many days do I have to get a refund?"
REFUND = "Refunds are issued within 14 days of receiving the returned item."


@pytest.fixture
def world(respx_mock, monkeypatch, tmp_path):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("JEVCRAWL_CACHE_DIR", str(tmp_path / "cli-cache"))
    real = asyncio.sleep

    async def quick(seconds, *a, **k):
        await real(0)

    monkeypatch.setattr(asyncio, "sleep", quick)
    return mount_site(respx_mock), FakeJev().install(respx_mock)


def test_crawl_json(world, capsys):
    assert main(["crawl", BASE + "/", "--goal", GOAL, "--max-pages", "5", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert any(REFUND in b["text"] for b in data["blocks"])
    assert data["stats"]["stopped_because"] == "answered"
    assert set(data) == {"blocks", "uncertain", "pages", "stats"}


def test_crawl_human_output(world, capsys):
    assert main(["crawl", BASE + "/", "--goal", GOAL]) == 0
    out = capsys.readouterr().out
    assert REFUND in out and "/help/returns" in out and "stopped=answered" in out
    assert "cost=$" in out


def test_scrape_without_goal_needs_no_key(world, capsys, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert main(["scrape", BASE + "/help/returns", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert any(REFUND in b["text"] for b in data["blocks"]) and data["stats"]["jev_calls"] == 0


def test_map_prints_ranked_urls(world, capsys):
    assert main(["map", BASE + "/", "--goal", GOAL, "--limit", "3", "--max-pages", "2"]) == 0
    first_line = capsys.readouterr().out.splitlines()[0]
    assert BASE + "/help/returns" in first_line


def test_missing_key_is_a_clear_error(world, capsys, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert main(["crawl", BASE + "/", "--goal", GOAL]) == 1
    assert "TYPESAFE_API_KEY" in capsys.readouterr().err
    assert main(["crawl", BASE + "/", "--goal", GOAL, "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "auth_error"


def test_rejected_key_is_an_auth_error(world, capsys):
    world[1].always = 401
    assert main(["crawl", BASE + "/", "--goal", GOAL, "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "auth_error"


def test_bad_url(world, capsys):
    assert main(["crawl", "nope", "--goal", GOAL]) == 1
    assert "not an absolute http(s) URL" in capsys.readouterr().err


def test_custom_endpoint_via_environment(world, respx_mock, capsys, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "local")
    monkeypatch.setenv("JEVCRAWL_BASE_URL", "http://localhost:11435")
    monkeypatch.setenv("JEVCRAWL_MODEL", "decider:0.8b")
    local = FakeJev(base="http://localhost:11435").install(respx_mock)
    assert main(["scrape", BASE + "/help/returns", "--goal", GOAL, "--json"]) == 0
    assert local.requests[0]["model"] == "decider:0.8b"
    assert local.headers[0]["authorization"] == "Bearer local"
    assert json.loads(capsys.readouterr().out)["stats"]["model"] == "decider:0.8b"


@pytest.mark.parametrize("before_command", [True, False])
def test_api_key_name_selects_the_variable(world, capsys, monkeypatch, before_command):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    monkeypatch.setenv("MY_JEV_KEY", "custom-key")
    flag = ["--api-key-name", "MY_JEV_KEY"]
    crawl = ["crawl", BASE + "/", "--goal", GOAL, "--max-pages", "5", "--json"]
    assert main(flag + crawl if before_command else crawl + flag) == 0
    assert world[1].headers[0]["authorization"] == "Bearer custom-key"
    assert "blocks" in json.loads(capsys.readouterr().out)


def test_unset_named_variable_is_an_error_that_names_it(world, capsys):
    # TYPESAFE_API_KEY is set by the fixture and must not be used instead.
    assert main(["crawl", BASE + "/", "--goal", GOAL, "--api-key-name", "MY_JEV_KEY"]) == 1
    err = capsys.readouterr().err
    assert "MY_JEV_KEY" in err and "TYPESAFE_API_KEY" not in err
    assert world[1].call_count == 0


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("sk-abc123secret", "lowercase letters"),  # a key pasted by mistake
        ("my_jev_key", "lowercase letters"),
        ("1KEY", "starts with a digit"),
        ("MY-KEY", "other than A-Z, 0-9 and _"),
        ("", "it is empty"),
    ],
)
def test_api_key_name_must_be_a_posix_name_and_is_never_echoed(world, capsys, name, reason):
    for argv in (
        ["--api-key-name", name, "crawl", BASE + "/", "--goal", GOAL],
        ["crawl", BASE + "/", "--goal", GOAL, "--api-key-name", name],
        ["doctor", "--api-key-name", name],
        ["--api-key-name", name],  # the MCP server must refuse to start too
    ):
        with pytest.raises(SystemExit) as exit_info:
            main(argv)
        assert exit_info.value.code == 2
        err = capsys.readouterr().err
        assert "--api-key-name" in err and reason in err and "uppercase letters" in err
        assert not name or name not in err
    assert world[1].call_count == 0


def test_doctor_uses_the_named_variable(world, capsys, monkeypatch):
    monkeypatch.setenv("MY_JEV_KEY", "custom-key")
    assert main(["doctor", "--api-key-name", "MY_JEV_KEY"]) == 0
    assert "MY_JEV_KEY" in capsys.readouterr().out
    assert main(["--api-key-name", "OTHER_KEY", "doctor"]) == 1
    assert "Set OTHER_KEY" in capsys.readouterr().out


def test_server_mode_passes_the_key_name_on(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "jevcrawl.mcp_server.main", lambda api_key_name=None: seen.append(api_key_name)
    )
    assert main(["--api-key-name", "MY_JEV_KEY"]) == 0
    assert main([]) == 0
    assert seen == ["MY_JEV_KEY", None]


def test_output_survives_a_legacy_console_encoding(monkeypatch):
    raw = io.BytesIO()
    legacy = io.TextIOWrapper(raw, encoding="cp1252")  # what a Windows console uses
    monkeypatch.setattr("sys.stdout", legacy)
    monkeypatch.setattr("sys.stderr", legacy)
    _utf8_streams()
    print("zero​width, Arabic مرحبا")  # cp1252 cannot encode these
    legacy.flush()
    assert "​" in raw.getvalue().decode("utf-8")
