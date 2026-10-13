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


def test_output_survives_a_legacy_console_encoding(monkeypatch):
    raw = io.BytesIO()
    legacy = io.TextIOWrapper(raw, encoding="cp1252")  # what a Windows console uses
    monkeypatch.setattr("sys.stdout", legacy)
    monkeypatch.setattr("sys.stderr", legacy)
    _utf8_streams()
    print("zero​width, Arabic مرحبا")  # cp1252 cannot encode these
    legacy.flush()
    assert "​" in raw.getvalue().decode("utf-8")
