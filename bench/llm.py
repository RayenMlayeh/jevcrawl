"""The benchmark's LLM access (Anthropic API). The only place an LLM is used in this repo."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import tomllib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

BENCH_DIR = Path(__file__).parent
DEFAULT_CONFIG = BENCH_DIR / "config.toml"


@dataclass
class Usage:
    """Token usage of one or more LLM calls."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: Usage) -> None:
        """Accumulate another usage into this one."""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost_usd += other.cost_usd


@dataclass
class LLMResult:
    """Text answer plus what the call cost."""

    text: str
    usage: Usage = field(default_factory=Usage)


class LLM(Protocol):
    """Anything that can complete a prompt. ``schema`` requests JSON matching a JSON Schema."""

    model: str

    async def complete(
        self, system: str | None, user: str, schema: dict[str, Any] | None = None
    ) -> LLMResult: ...


class LLMRefusal(RuntimeError):
    """The model declined to answer (stop_reason == "refusal")."""


@dataclass(frozen=True)
class BenchConfig:
    """Parsed ``bench/config.toml``."""

    raw: dict[str, Any]

    @property
    def llm(self) -> dict[str, Any]:
        return self.raw["llm"]

    @property
    def limits(self) -> dict[str, Any]:
        return self.raw["limits"]

    @property
    def seed(self) -> int:
        return int(self.raw.get("tasks", {}).get("seed", 0))

    def price(self, model: str) -> tuple[float, float]:
        """(input, output) USD per million tokens; raises if the model has no listed price."""
        try:
            entry = self.raw["prices"][model]
            return float(entry["input"]), float(entry["output"])
        except KeyError as exc:
            raise SystemExit(
                f"No price for model {model!r} in bench/config.toml [prices]; add it so that "
                "costs are computed from a number you chose, not a guess."
            ) from exc


def load_config(path: Path | None = None) -> BenchConfig:
    """Read the benchmark configuration."""
    return BenchConfig(tomllib.loads((path or DEFAULT_CONFIG).read_text(encoding="utf-8")))


class AnthropicLLM:
    """Messages API wrapper: low effort, no sampling parameters, optional JSON-schema output.

    Per the current API: ``temperature`` is not sent (it is rejected by the newest models),
    thinking is left at the model default, and effort goes in ``output_config``.
    """

    def __init__(
        self,
        model: str,
        config: BenchConfig,
        *,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self._price = config.price(model)
        self._effort = config.llm.get("effort", "low")
        self._max_tokens = int(config.llm.get("max_tokens", 4096))
        if client is None:
            import anthropic

            client = anthropic.AsyncAnthropic()
        self._client = client

    async def complete(
        self, system: str | None, user: str, schema: dict[str, Any] | None = None
    ) -> LLMResult:
        output_config: dict[str, Any] = {"effort": self._effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "output_config": output_config,
            "messages": [{"role": "user", "content": user}],
        }
        if system:
            kwargs["system"] = system
        response = await self._client.messages.create(**kwargs)
        if response.stop_reason == "refusal":
            raise LLMRefusal(f"{self.model} refused: {getattr(response, 'stop_details', None)}")
        text = "".join(b.text for b in response.content if b.type == "text")
        usage = Usage(
            response.usage.input_tokens,
            response.usage.output_tokens,
            (
                response.usage.input_tokens * self._price[0]
                + response.usage.output_tokens * self._price[1]
            )
            / 1_000_000,
        )
        return LLMResult(text, usage)


# --------------------------------------------------------------------------- Claude Code

Runner = Callable[[list[str], str], Awaitable[tuple[int, str, str]]]
CLAUDE_CODE_SYSTEM = "You are a careful assistant. Follow the instructions in the user message."


async def _run_subprocess(argv: list[str], stdin: str) -> tuple[int, str, str]:
    """Run ``argv`` with ``stdin`` in an empty temp directory (so no CLAUDE.md is picked up)."""
    with tempfile.TemporaryDirectory(prefix="jevcrawl-cc-", ignore_cleanup_errors=True) as cwd:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        out, err = await asyncio.wait_for(proc.communicate(stdin.encode("utf-8")), timeout=600)
    return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


class ClaudeCodeLLM:
    """The LLM interface on top of ``claude -p``, using your Claude Code login (no API key).

    Each call is a headless Claude Code session with no tools, no MCP servers, no settings and no
    skills, and with our own system prompt in place of Claude Code's. Token counts are
    **estimates of the prompt we sent** (characters / 3.5) plus the reported output tokens,
    because Claude Code adds its own overhead to every call; costs are those tokens priced with
    ``[prices]``, i.e. notional, not what your plan charges.
    """

    def __init__(
        self,
        model: str,
        config: BenchConfig,
        *,
        runner: Runner = _run_subprocess,
        executable: str | None = None,
    ) -> None:
        self.model = model
        self._price = config.price(_price_key(model, config))
        self._effort = config.llm.get("effort", "low")
        self._run = runner
        self._exe = executable or shutil.which("claude")
        if self._exe is None:
            raise SystemExit("The 'claude' command (Claude Code) was not found on PATH.")

    async def complete(
        self, system: str | None, user: str, schema: dict[str, Any] | None = None
    ) -> LLMResult:
        from jevcrawl.models import estimate_tokens

        assert self._exe is not None
        argv = [
            self._exe, "-p", "--output-format", "json", "--model", self.model,
            "--system-prompt", system or CLAUDE_CODE_SYSTEM,
            "--tools", "", "--disable-slash-commands", "--strict-mcp-config",
            "--no-session-persistence", "--setting-sources", "", "--effort", self._effort,
        ]  # fmt: skip
        if schema is not None:
            argv += ["--json-schema", json.dumps(schema)]
        code, out, err = await self._run(argv, user)
        try:
            data = json.loads(out)
        except ValueError as exc:
            raise RuntimeError(
                f"claude -p exited {code} with unreadable output: {err[:300]}"
            ) from exc
        if data.get("is_error") or code != 0:
            raise RuntimeError(f"claude -p failed: {str(data.get('result'))[:300]}")
        if schema is not None:
            structured = data.get("structured_output")
            text = json.dumps(structured) if structured is not None else str(data.get("result", ""))
        else:
            text = str(data.get("result", ""))
        tokens_in = estimate_tokens((system or CLAUDE_CODE_SYSTEM) + user)
        tokens_out = int((data.get("usage") or {}).get("output_tokens") or 0)
        usage = Usage(
            tokens_in,
            tokens_out,
            (tokens_in * self._price[0] + tokens_out * self._price[1]) / 1_000_000,
        )
        return LLMResult(text, usage)


def _price_key(model: str, config: BenchConfig) -> str:
    """Price table key for ``model``; Claude Code accepts aliases such as ``sonnet``."""
    aliases = config.raw.get("claude_code", {}).get("price_like", {})
    return aliases.get(model, model)


def resolve_provider(requested: str, config: BenchConfig) -> str:
    """``auto`` picks the Anthropic API if ANTHROPIC_API_KEY is set, else Claude Code."""
    choice = requested if requested != "config" else config.llm.get("provider", "auto")
    if choice != "auto":
        return choice
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "anthropic"
    if shutil.which("claude"):
        return "claude-code"
    raise SystemExit(
        "No LLM available for the benchmark: set ANTHROPIC_API_KEY, or install Claude Code "
        "(the 'claude' command) and log in, or pass --llm explicitly."
    )


def make_llm(provider: str, role: str, config: BenchConfig) -> LLM:
    """Build the LLM for ``role`` (``answer``, ``judge`` or ``nav``) on ``provider``."""
    if provider == "claude-code":
        section = config.raw.get("claude_code", {})
        model = section.get(f"{role}_model") or config.llm[f"{role}_model"]
        return ClaudeCodeLLM(model, config)
    return AnthropicLLM(config.llm[f"{role}_model"], config)
