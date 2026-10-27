"""Phase 4: the MCP tools (test 9 and the expected-error contract) and `doctor`."""

import asyncio

import httpx
import pytest

from jevcrawl import mcp_server
from jevcrawl.cli import main
from jevcrawl.config import Config
from jevcrawl.doctor import run_doctor
from tests.fixtures.fake_site import BASE, mount_site
from tests.fixtures.jev_fake import FakeJev

GOAL = "How many days do I have to get a refund?"
REFUND = "Refunds are issued within 14 days of receiving the returned item."


@pytest.fixture
def world(respx_mock, monkeypatch, tmp_path):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("JEVCRAWL_CACHE_DIR", str(tmp_path / "mcp-cache"))
    real = asyncio.sleep

    async def quick(seconds, *a, **k):
        await real(0)

    monkeypatch.setattr(asyncio, "sleep", quick)
    return mount_site(respx_mock), FakeJev().install(respx_mock)


async def test_server_lists_exactly_the_three_tools_with_documented_arguments():
    tools = {t.name: t for t in await mcp_server.mcp.list_tools()}
    assert set(tools) == {"crawl", "scrape", "map"}
    schemas = {name: t.input_schema if hasattr(t, "input_schema") else t.inputSchema
               for name, t in tools.items()}  # fmt: skip
    crawl = schemas["crawl"]["properties"]
    assert list(crawl) == [
        "goal", "start_url", "max_pages", "max_seconds", "return_tokens", "max_decision_usd"
    ]  # fmt: skip
    assert (crawl["max_pages"]["default"], crawl["max_seconds"]["default"]) == (30, 120)
    assert (crawl["return_tokens"]["default"], crawl["max_decision_usd"]["default"]) == (4000, 0.05)
    assert list(schemas["scrape"]["properties"]) == ["url", "goal"]
    assert list(schemas["map"]["properties"]) == ["start_url", "goal", "limit", "max_pages"]
    assert schemas["crawl"]["required"] == ["goal", "start_url"]
    for tool in tools.values():  # agents read these to decide when to call
        assert len(tool.description) > 200 and "goal" in tool.description.lower()


async def test_crawl_tool_returns_the_refund_sentence(world):
    data = await mcp_server.crawl(GOAL, BASE + "/")
    assert any(REFUND in b["text"] for b in data["blocks"])
    assert data["stats"]["stopped_because"] == "answered"
    assert {"url", "heading_path", "text", "char_start", "char_end", "relevance"} <= set(
        data["blocks"][0]
    )


async def test_scrape_and_map_tools(world):
    scraped = await mcp_server.scrape(BASE + "/help/returns")
    assert any(REFUND in b["text"] for b in scraped["blocks"])
    mapped = await mcp_server.map_urls(BASE + "/", GOAL, limit=3, max_pages=2)
    assert mapped["urls"][0]["url"] == BASE + "/help/returns"


# --------------------------------------------------------------- test 9 and the error contract


async def test_401_returns_auth_error_payload(world):
    world[1].always = 401
    data = await mcp_server.crawl(GOAL, BASE + "/")
    assert data["error"] == "auth_error" and "TYPESAFE_API_KEY" in data["message"]
    assert set(data) == {"error", "message"}
    scraped = await mcp_server.scrape(BASE + "/help/returns", GOAL)
    assert scraped["error"] == "auth_error"


async def test_missing_key_returns_auth_error_payload(world, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    data = await mcp_server.crawl(GOAL, BASE + "/")
    assert data["error"] == "auth_error" and "TYPESAFE_API_KEY" in data["message"]
    assert world[1].call_count == 0


@pytest.mark.parametrize(
    ("url", "code"),
    [("not-a-url", "invalid_url"), ("ftp://x.example/", "invalid_url"),
     ("https://help.example.com/files/size-guide.pdf", "not_html")],
)  # fmt: skip
async def test_expected_errors_are_payloads_not_exceptions(world, url, code):
    for call in (
        mcp_server.crawl(GOAL, url),
        mcp_server.scrape(url, GOAL),
        mcp_server.map_urls(url, GOAL),
    ):
        data = await call
        assert data["error"] == code and data["message"]


async def test_unreachable_start_page_is_a_payload(world, respx_mock):
    respx_mock.get("https://down.example/robots.txt").mock(side_effect=httpx.ConnectError("boom"))
    respx_mock.get("https://down.example/").mock(side_effect=httpx.ConnectError("boom"))
    data = await mcp_server.crawl(GOAL, "https://down.example/")
    assert data["error"] == "fetch_failed"


async def test_invalid_arguments_are_payloads(world):
    assert (await mcp_server.crawl(GOAL, BASE + "/", max_pages=0))["error"] == "invalid_argument"
    assert (await mcp_server.map_urls(BASE + "/", GOAL, limit=0))["error"] == "invalid_argument"


async def test_unexpected_exceptions_never_escape(world, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(mcp_server, "run_crawl", boom)
    data = await mcp_server.crawl(GOAL, BASE + "/")
    assert data["error"] == "internal_error" and "kaboom" in data["message"]


async def test_missing_key_is_a_payload(world, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    data = await mcp_server.crawl(GOAL, BASE + "/")
    assert data["error"] == "auth_error" and "TYPESAFE_API_KEY" in data["message"]


async def test_server_reads_the_key_from_the_named_variable(world, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    monkeypatch.setenv("MY_JEV_KEY", "custom-key")
    monkeypatch.setattr(mcp_server, "_api_key_name", "MY_JEV_KEY")
    data = await mcp_server.crawl(GOAL, BASE + "/")
    assert "error" not in data
    assert world[1].headers[0]["authorization"] == "Bearer custom-key"


async def test_unset_named_variable_is_a_payload_that_names_it(world, monkeypatch):
    monkeypatch.setattr(mcp_server, "_api_key_name", "MY_JEV_KEY")  # TYPESAFE_API_KEY is set
    data = await mcp_server.crawl(GOAL, BASE + "/")
    assert data["error"] == "auth_error" and "MY_JEV_KEY" in data["message"]
    assert world[1].call_count == 0


def test_server_main_remembers_the_key_name(monkeypatch):
    monkeypatch.setattr(mcp_server.mcp, "run", lambda: None)
    monkeypatch.setattr(mcp_server, "_api_key_name", None)  # restored when the test ends
    mcp_server.main(api_key_name="MY_JEV_KEY")
    assert mcp_server._api_key_name == "MY_JEV_KEY"


# --------------------------------------------------------------- doctor


async def test_doctor_ok(world, capsys):
    code = await run_doctor(Config(api_key="test-key"))
    out = capsys.readouterr().out
    assert code == 0 and "ok:" in out and "jev-latest" in out
    assert "test-key" not in out and "set (8 characters, TYPESAFE_API_KEY)" in out
    assert "https://api.typesafe.ai" in out and "jev-1.13.0" in out


async def test_doctor_reports_missing_and_rejected_keys(world, capsys):
    assert await run_doctor(Config(api_key=None)) == 1
    assert "TYPESAFE_API_KEY" in capsys.readouterr().out
    world[1].models_route.mock(return_value=httpx.Response(401))
    assert await run_doctor(Config(api_key="bad")) == 1
    assert "rejected" in capsys.readouterr().out


def test_doctor_cli_command(world, capsys):
    assert main(["doctor"]) == 0
    assert "ok:" in capsys.readouterr().out


def test_no_arguments_starts_the_server(monkeypatch):
    started = []
    monkeypatch.setattr(mcp_server, "main", lambda api_key_name=None: started.append(True))
    assert main([]) == 0 and started == [True]
