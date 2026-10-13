"""Offline stand-ins for the benchmark tests: fixture site, stub LLMs, fake Jev. No network."""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Iterator
from typing import Any

import respx

from bench.llm import LLM, LLMResult, Usage
from bench.tasks.loader import Task
from jevcrawl.models import estimate_tokens
from tests.fixtures.fake_site import BASE, mount_site
from tests.fixtures.jev_fake import FakeJev, words

TASKS = [
    Task("offline-1", "How many days do I have to get a refund?",
         BASE + "/", "Refunds are issued within 14 days of receiving the returned item.",
         [BASE + "/help/returns"]),
    Task("offline-2", "How long does standard delivery take?",
         BASE + "/", "Standard delivery takes 3 to 5 working days.", [BASE + "/help/shipping"]),
    Task("offline-3", "How long does express delivery take?",
         BASE + "/", "Express delivery takes 1 to 2 working days.", [BASE + "/help/shipping"]),
    Task("offline-4", "Which roles is Example Shop hiring for?",
         BASE + "/", "Customer support agent, warehouse operative and backend engineer.",
         [BASE + "/jobs"]),
    Task("offline-5", "How do I exchange an item for a different size?",
         BASE + "/", "Start a new return and place a new order.", [BASE + "/help/returns"]),
]  # fmt: skip


def _overlap(a: str, b: str) -> float:
    wa = words(a)
    return len(wa & words(b)) / len(wa) if wa else 0.0


class StubAnswerer:
    """Answers with the context sentence that overlaps the question most (synthetic)."""

    model = "stub-answerer"

    async def complete(
        self, system: str | None, user: str, schema: dict[str, Any] | None = None
    ) -> LLMResult:
        context, _, question = user.partition("\n\nQuestion: ")
        sentences = re.split(r"(?<=[.!?])\s+|\n+", context)
        best = max(sentences, key=lambda s: _overlap(question, s), default="")
        found = _overlap(question, best) > 0.3
        text = best.strip() if found else "NOT FOUND"
        return LLMResult(text, Usage(estimate_tokens(user), estimate_tokens(text), 0.0))


class StubJudge:
    """Correct if most of the reference's content words appear in the candidate (synthetic)."""

    model = "stub-judge"

    async def complete(
        self, system: str | None, user: str, schema: dict[str, Any] | None = None
    ) -> LLMResult:
        reference = re.search(r"Reference answer: (.*?)\n\nCandidate answer:", user, re.S)
        candidate = user.rsplit("Candidate answer:", 1)[-1]
        ok = bool(reference) and _overlap(reference.group(1), candidate) >= 0.6
        text = json.dumps({"correct": ok, "reason": "keyword overlap (stub)"})
        return LLMResult(text, Usage(estimate_tokens(user), 20, 0.0))


class StubNavigator:
    """Opens the link whose text overlaps the question most (synthetic)."""

    model = "stub-navigator"

    async def complete(
        self, system: str | None, user: str, schema: dict[str, Any] | None = None
    ) -> LLMResult:
        question = user.split("\n\n", 1)[0].removeprefix("Question: ")
        links = re.findall(r"^(\d+)\. (.*?) -> ", user.split("Links:\n", 1)[-1], re.M)
        scored = sorted(((_overlap(question, text), int(i)) for i, text in links), reverse=True)
        best = scored[0] if scored and scored[0][0] > 0 else None
        visited = int(re.search(r"Pages already visited: (\d+)", user).group(1))  # type: ignore[union-attr]
        done = visited >= 2 or best is None
        text = json.dumps({"answer_on_this_page": done, "next_link": None if done else best[1]})  # type: ignore[index]
        return LLMResult(text, Usage(estimate_tokens(user), 12, 0.0))


@contextlib.contextmanager
def offline_environment() -> Iterator[tuple[list[Task], dict[str, LLM]]]:
    """Serve the fixture site and fake Jev through respx; yield (tasks, llms).

    Also sets a dummy ``TYPESAFE_API_KEY`` for the duration.
    """
    saved = {k: os.environ.get(k) for k in ("TYPESAFE_API_KEY",)}
    os.environ["TYPESAFE_API_KEY"] = "smoke-key"
    try:
        with respx.mock(assert_all_called=False) as router:
            mount_site(router)
            FakeJev().install(router)
            llms: dict[str, LLM] = {
                "answer": StubAnswerer(),
                "judge": StubJudge(),
                "nav": StubNavigator(),
            }
            yield TASKS, llms
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
