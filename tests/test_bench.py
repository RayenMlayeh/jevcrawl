"""The benchmark harness, offline: stub LLMs, the fixture site and a fake Jev."""

import csv
import json
import sys
import types
from types import SimpleNamespace

import pytest

import bench.run as bench_run
from bench.baselines.common import BenchContext, gold_fetched, normalize_url
from bench.judge import judge
from bench.llm import (
    AnthropicLLM,
    ClaudeCodeLLM,
    LLMRefusal,
    load_config,
    make_llm,
    resolve_provider,
)
from bench.report import FIELDS, render, summarize
from bench.tasks.loader import Task, load_jsonl, load_tasks, sample
from jevcrawl.config import Config
from jevcrawl.models import estimate_tokens


@pytest.fixture
def bench_config():
    return load_config()


@pytest.fixture
def results_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(bench_run, "RESULTS_DIR", tmp_path / "results")
    return tmp_path / "results"


# --------------------------------------------------------------------- end to end (offline)


# Crawl4AI drives a real browser, which cannot reach the mocked site; it is checked by hand.
OFFLINE_SYSTEMS = ["bfs", "llm_nav", "jevcrawl"]


async def run_offline(results_dir, limit):
    from tests.fixtures.bench_stubs import offline_environment

    with offline_environment() as (tasks, llms):
        ctx = bench_run.build_context(load_config(), llms["nav"])
        csv_path = results_dir / "run.csv"
        await bench_run.run_all(
            tasks[:limit], OFFLINE_SYSTEMS, ctx, llms["answer"], llms["judge"], csv_path, "test"
        )
    return list(csv.DictReader(csv_path.open(encoding="utf-8")))


async def test_every_system_runs_end_to_end_and_the_report_renders(results_dir):
    rows = await run_offline(results_dir, 5)
    assert len(rows) == 5 * 3 and set(rows[0]) == set(FIELDS)
    assert all(r["error"] == "" for r in rows)
    report = render(rows, title="t", notes=["n"])
    for system in ("bfs", "llm_nav", "jevcrawl"):
        assert f"| {system} |" in report


async def test_jevcrawl_reports_jev_cost_and_pages(results_dir):
    rows = await run_offline(results_dir, 2)
    jev = [r for r in rows if r["system"] == "jevcrawl"]
    bfs = [r for r in rows if r["system"] == "bfs"]
    assert jev and all(float(r["jev_cost_usd"]) > 0 for r in jev)
    assert all(float(r["jev_cost_usd"]) == 0 for r in bfs)
    assert all(json.loads(r["notes"])["jev_calls"] >= 1 for r in jev)
    assert all(r["gold_fetched"] in {"0", "1"} for r in rows)


def test_crawl4ai_is_a_default_system_and_its_absence_is_explained(monkeypatch, capsys):
    assert bench_run.DEFAULT_SYSTEMS == ["bfs", "crawl4ai", "llm_nav", "jevcrawl"]
    monkeypatch.setattr(bench_run.importlib.util, "find_spec", lambda name: None)
    assert bench_run.main(["--limit", "1", "--yes"]) == 2
    assert "uv sync --extra bench --extra crawl4ai" in capsys.readouterr().err


def test_a_real_run_refuses_to_spend_money_without_yes(capsys):
    assert bench_run.main(["--limit", "5"]) == 2
    assert "--yes" in capsys.readouterr().err


async def test_a_failing_system_becomes_an_error_row_not_a_crash(bench_config, monkeypatch):
    async def boom(task, ctx, **kw):
        raise RuntimeError("site exploded")

    monkeypatch.setitem(bench_run.SYSTEMS, "bfs", boom)
    ctx = BenchContext(bench_config, Config())
    task = Task("t", "q?", "https://x.example/", "a")
    row = await bench_run.evaluate(task, "bfs", ctx, None, None, "run")
    assert row["error"].startswith("RuntimeError: site exploded") and row["correct"] == ""


# --------------------------------------------------------------------- metrics and report


def row(system, correct, gold="1", error="", pages="3", tokens="100", jev="0.0001", total="0.001"):
    values = dict.fromkeys(FIELDS, "")
    values.update(
        system=system, correct=correct, gold_fetched=gold, error=error, pages_fetched=pages,
        llm_input_tokens=tokens, jev_cost_usd=jev, total_cost_usd=total, wall_seconds="2",
    )  # fmt: skip
    return values


def test_summary_math_and_errors_count_as_wrong():
    rows = [
        row("a", "1", gold="1"), row("a", "0", gold="0", tokens="300"),
        row("a", "", error="boom", gold=""), row("b", "1", gold=""),
    ]  # fmt: skip
    a, b = summarize(rows)
    assert (a["tasks"], a["errors"]) == (3, 1)
    assert a["accuracy"] == pytest.approx(1 / 3)  # the errored task is wrong
    assert a["gold"] == pytest.approx(0.5)  # gold rate over the tasks that list gold pages
    assert a["llm_in"] == pytest.approx(200)  # means exclude errored tasks
    assert b["gold"] is None  # no gold pages listed: n/a, never 0


def test_normalize_url_and_gold_check():
    assert normalize_url("HTTP://www.Example.com/a/b/#frag") == normalize_url(
        "https://example.com/a/b"
    )
    assert normalize_url("https://x.com/a?q=1") != normalize_url("https://x.com/a")
    assert gold_fetched(["https://x.com/a/"], ["http://x.com/a#top"]) is True
    assert gold_fetched(["https://x.com/a"], ["https://x.com/b"]) is False
    assert gold_fetched([], ["https://x.com/b"]) is None


# --------------------------------------------------------------------- tasks


def test_task_file_roundtrip_and_reproducible_sample(tmp_path):
    path = tmp_path / "t.jsonl"
    lines = [
        json.dumps({"id": f"t{i}", "question": "q", "root_url": "https://x.example/",
                    "answer": "a", "gold_urls": ["https://x.example/p"], "lang": "fr"})
        for i in range(10)
    ]  # fmt: skip
    path.write_text("\n".join(lines) + "\n\n", encoding="utf-8")
    tasks = load_jsonl(path)
    assert (
        len(tasks) == 10 and tasks[0].lang == "fr" and tasks[0].gold_urls == ["https://x.example/p"]
    )
    assert [t.id for t in sample(tasks, 4, 7)] == [t.id for t in sample(tasks, 4, 7)]
    assert len(load_tasks(str(path), 3)) == 3
    path.write_text('{"id": "x"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="bad task row"):
        load_jsonl(path)


def test_empty_task_file_explains_itself(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(SystemExit, match="No tasks found"):
        load_tasks(str(empty))


def test_webwalkerqa_rows_become_tasks(monkeypatch):
    rows = [
        {"question": "Q1", "answer": "A1", "root_url": "https://a.example/",
         "info": {"source_website": ["https://a.example/p"], "lang": "en", "domain": "x"}},
        {"question": "Q2", "answer": "A2", "root_url": "https://b.example/", "info": None},
    ]  # fmt: skip
    fake = types.ModuleType("datasets")
    fake.load_dataset = lambda name, split: rows  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "datasets", fake)
    from bench.tasks.webwalkerqa import load

    tasks = load("main")
    assert tasks[0].id == "wwqa-main-0" and tasks[0].gold_urls == ["https://a.example/p"]
    assert tasks[0].lang == "en" and tasks[1].gold_urls == []


# --------------------------------------------------------------------- LLM wrapper and judge


class FakeAnthropic:
    def __init__(self, text='{"correct": true, "reason": "same"}', stop_reason="end_turn"):
        self.calls = []
        self._text, self._stop = text, stop_reason
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason=self._stop,
            stop_details=None,
            content=[SimpleNamespace(type="thinking", thinking=""),
                     SimpleNamespace(type="text", text=self._text)],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )  # fmt: skip


async def test_anthropic_wrapper_request_shape_and_cost(bench_config):
    client = FakeAnthropic()
    llm = AnthropicLLM("claude-opus-5-5", bench_config, client=client)
    result = await llm.complete("sys", "hello", {"type": "object"})
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["system"] == "sys"
    assert call["output_config"] == {
        "effort": "low", "format": {"type": "json_schema", "schema": {"type": "object"}}
    }  # fmt: skip
    assert not {"temperature", "top_p", "top_k", "thinking", "budget_tokens"} & set(call)
    assert call["max_tokens"] == 4096 and call["messages"] == [{"role": "user", "content": "hello"}]
    assert result.usage.input_tokens == 1000
    assert result.usage.cost_usd == pytest.approx((1000 * 4.0 + 200 * 20.0) / 1e6)  # config prices


async def test_wrapper_without_schema_and_refusals(bench_config):
    client = FakeAnthropic(text="plain")
    llm = AnthropicLLM("claude-sonnet-5-5", bench_config, client=client)
    assert (await llm.complete(None, "x")).text == "plain"
    assert "format" not in client.calls[0]["output_config"] and "system" not in client.calls[0]
    refusing = AnthropicLLM(
        "claude-sonnet-5-5", bench_config, client=FakeAnthropic(stop_reason="refusal")
    )
    with pytest.raises(LLMRefusal):
        await refusing.complete(None, "x")


def test_models_without_a_price_are_rejected(bench_config):
    with pytest.raises(SystemExit, match="No price"):
        AnthropicLLM("claude-made-up", bench_config, client=FakeAnthropic())


async def test_judge_parses_the_verdict(bench_config):
    llm = AnthropicLLM("claude-sonnet-5-5", bench_config, client=FakeAnthropic())
    verdict = await judge(llm, "q?", "14 days", "Fourteen days")
    assert verdict.correct is True and verdict.usage.input_tokens == 1000


# --------------------------------------------------------------------- caches and tuning


def test_every_task_gets_a_fresh_empty_cache(bench_config):
    task = Task("t", "q", "https://x.example/", "a")
    with BenchContext(bench_config, Config()) as ctx:
        first, second = ctx.cache_dir(task, "s"), ctx.cache_dir(task, "s")
        assert first != second and not list(first.iterdir())  # costs stay real


def test_bench_config_has_prices_for_every_configured_model(bench_config):
    for key in ("answer_model", "judge_model", "nav_model"):
        assert bench_config.price(bench_config.llm[key])[0] > 0


# --------------------------------------------------------------------- Claude Code provider


def cc_runner(payload, code=0, calls=None):
    async def run(argv, stdin):
        if calls is not None:
            calls.append((argv, stdin))
        return code, json.dumps(payload), ""

    return run


async def test_claude_code_llm_builds_a_toolless_headless_call(bench_config):
    calls = []
    payload = {"is_error": False, "result": "14 days", "usage": {"output_tokens": 7}}
    llm = ClaudeCodeLLM(
        "sonnet", bench_config, runner=cc_runner(payload, calls=calls), executable="claude"
    )
    result = await llm.complete("be brief", "x" * 700)
    argv, stdin = calls[0]
    assert argv[:2] == ["claude", "-p"] and stdin == "x" * 700  # the prompt goes via stdin

    def value(flag):
        return argv[argv.index(flag) + 1]

    assert value("--model") == "sonnet" and value("--system-prompt") == "be brief"
    assert value("--tools") == "" and value("--effort") == "low"
    for flag in ("--disable-slash-commands", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in argv
    assert "--json-schema" not in argv
    assert result.text == "14 days"
    assert result.usage.output_tokens == 7
    sent = "be brief" + "x" * 700
    assert result.usage.input_tokens == estimate_tokens(sent)  # the prompt we sent, not reported
    assert result.usage.cost_usd > 0  # notional: priced from [prices], not from the plan


async def test_claude_code_llm_structured_output_and_errors(bench_config):
    schema = {"type": "object"}
    ok = {"is_error": False, "result": "ignored", "structured_output": {"correct": True}}
    calls = []
    llm = ClaudeCodeLLM(
        "claude-sonnet-5-5", bench_config, runner=cc_runner(ok, calls=calls), executable="claude"
    )
    result = await llm.complete(None, "q", schema)
    assert json.loads(result.text) == {"correct": True}
    argv = calls[0][0]
    assert json.loads(argv[argv.index("--json-schema") + 1]) == schema
    assert "--system-prompt" in argv  # Claude Code's own system prompt is always replaced
    bad = ClaudeCodeLLM(
        "claude-sonnet-5-5",
        bench_config,
        runner=cc_runner({"is_error": True, "result": "Not logged in"}),
        executable="claude",
    )
    with pytest.raises(RuntimeError, match="Not logged in"):
        await bad.complete(None, "q")


def test_provider_selection(bench_config, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("bench.llm.shutil.which", lambda name: "/bin/claude")
    assert resolve_provider("config", bench_config) == "claude-code"  # no key: use the login
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert resolve_provider("auto", bench_config) == "anthropic"
    assert resolve_provider("claude-code", bench_config) == "claude-code"  # explicit wins
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setattr("bench.llm.shutil.which", lambda name: None)
    with pytest.raises(SystemExit, match="No LLM available"):
        resolve_provider("auto", bench_config)
    with pytest.raises(SystemExit, match="not found"):
        make_llm("claude-code", "answer", bench_config)


def test_claude_code_models_come_from_their_own_config_section(bench_config, monkeypatch):
    monkeypatch.setattr("bench.llm.shutil.which", lambda name: "/bin/claude")
    llm = make_llm("claude-code", "judge", bench_config)
    assert llm.model == bench_config.raw["claude_code"]["judge_model"]


def test_verifier_keeps_only_tasks_whose_answer_is_still_on_the_gold_page(respx_mock, tmp_path):
    import asyncio

    import httpx

    from bench.tasks import verify

    assert verify.support("Cali, Colombia", "COP 16 will be held in Cali, Colombia.") == 1.0
    assert verify.support("彩旗、旋转木马", "新增彩旗和旋转木马") == 1.0
    assert verify.support("2002-10-22", "Events since 2019") == 0.0

    def html(text):
        return httpx.Response(200, text=f"<html><body><p>{text}</p></body></html>",
                              headers={"content-type": "text/html"})  # fmt: skip

    respx_mock.get("https://a.example/").mock(return_value=html("home"))
    respx_mock.get("https://a.example/cop").mock(return_value=html("COP 16: Cali, Colombia"))
    respx_mock.get("https://a.example/old").mock(return_value=html("Nothing about it now"))
    respx_mock.get("https://down.example/").mock(side_effect=httpx.ConnectError("x"))
    tasks = [
        Task("good", "Where is COP 16?", "https://a.example/", "Cali, Colombia",
             ["https://a.example/cop"]),
        Task("moved", "Where is COP 16?", "https://a.example/", "Cali, Colombia",
             ["https://a.example/old"]),
        Task("offline", "q", "https://down.example/", "a", ["https://down.example/x"]),
    ]  # fmt: skip
    checks = {c.id: c for c in asyncio.run(verify.verify(tasks, 0.6, 2))}
    assert checks["good"].ok and not checks["moved"].ok and not checks["offline"].ok
    assert checks["moved"].reason == "answer no longer on gold pages"
    assert checks["offline"].reason == "root: network"

    ids = tmp_path / "ids.txt"
    ids.write_text("t2\n", encoding="utf-8")
    rows = [
        {"id": f"t{i}", "question": "q", "root_url": "https://x/", "answer": "a"} for i in (1, 2)
    ]
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    assert [t.id for t in load_tasks(str(path), only=ids)] == ["t2"]


def test_exclude_drops_seen_tasks_and_ignores_comments(tmp_path):
    rows = [
        {"id": f"t{i}", "question": "q", "root_url": "https://x/", "answer": "a"} for i in (1, 2, 3)
    ]
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    seen = tmp_path / "seen.txt"
    seen.write_text("# looked at during development\nt1\nt3\n", encoding="utf-8")
    assert [t.id for t in load_tasks(str(path), exclude=seen)] == ["t2"]


def test_the_committed_seen_ids_file_lists_fifty_ids():
    from pathlib import Path

    lines = (Path(__file__).parent.parent / "bench/tasks/seen-ids.txt").read_text().splitlines()
    ids = [line for line in lines if line and not line.startswith("#")]
    assert len(ids) == len(set(ids)) == 50 and all(i.startswith("wwqa-main-") for i in ids)
