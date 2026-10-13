"""A deterministic stand-in for the Jev API, served through respx (no network, no money).

Scores come from keyword overlap between the goal and the block or link text. The fake can
also be scripted to return 429 / 529 / 401 sequences.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

import httpx
import respx

BASE = "https://api.typesafe.ai"
STOPWORDS = frozenset(
    (
        "a an the of to in on for and or is are was were be do does did i we you my our your "
        "it its this that with at by from as how what when where which who why many much long "
        "get have has can will would should about"
    ).split()
)


def words(text: str) -> set[str]:
    """Lowercase content words with a trailing plural 's' removed."""
    found = re.findall(r"[a-z0-9]+", text.lower())
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in found} - STOPWORDS


def clip(value: float, low: float = 0.02, high: float = 0.98) -> float:
    return max(low, min(high, value))


def question_text(question: dict[str, Any], state: dict[str, Any]) -> str:
    """The page-derived text a question is about."""
    ins = question["instructions"]
    if isinstance(ins, dict):
        if "block" in ins:
            return f"{ins['block'].get('heading', '')} {ins['block'].get('text', '')}"
        if "link" in ins:
            link = ins["link"]
            return f"{link.get('text', '')} {link.get('context', '')}"
    return " ".join(e.get("text", "") for e in state.get("excerpts", []))


def score(goal: str, question: dict[str, Any], state: dict[str, Any]) -> float:
    """Noul for one question, by keyword overlap with the goal."""
    goal_words = words(goal)
    if not goal_words:
        return 0.02
    overlap = len(goal_words & words(question_text(question, state))) / len(goal_words)
    ins = question["instructions"]
    if isinstance(ins, dict) and "link" in ins:
        return clip(0.3 + 1.5 * overlap)  # every link has some chance; topical ones much more
    if isinstance(ins, dict) and "block" in ins:
        return clip(1.2 * overlap)
    return clip(overlap)  # "answered?": coverage of the goal's content words


class FakeJev:
    """Install with ``FakeJev().install(respx_mock)``; inspect ``requests`` afterwards."""

    def __init__(self, base: str = BASE, price_divisor: float = 4.0) -> None:
        self.base = base
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self.script: list[int | tuple[int, dict[str, str]]] = []
        self.always: int | None = None
        self.max_questions: int | None = None  # larger requests get 400 max_tokens_exceeded
        self.models = [{"name": "jev-latest", "description": "flagship", "release_date": "2026"}]
        self._price_divisor = price_divisor

    def install(self, router: respx.MockRouter) -> FakeJev:
        self.route = router.post(f"{self.base}/v1/systemone").mock(side_effect=self._handle)
        self.models_route = router.get(f"{self.base}/v1/models").mock(
            side_effect=self._handle_models
        )
        return self

    def _handle_models(self, request: httpx.Request) -> httpx.Response:
        self.headers.append(request.headers)
        return httpx.Response(200, json={"models": self.models})

    def fail_next(self, *statuses: int | tuple[int, dict[str, str]]) -> None:
        """Answer the next requests with these statuses (then behave normally)."""
        self.script.extend(statuses)

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        self.headers.append(request.headers)
        forced = self.script.pop(0) if self.script else self.always
        if forced is not None:
            status, headers = forced if isinstance(forced, tuple) else (forced, {})
            if status != 200:
                return httpx.Response(status, json={"error": f"scripted {status}"}, headers=headers)
        if self.max_questions is not None and len(body["questions"]) > self.max_questions:
            return httpx.Response(400, json={"detail": {"error_type": "max_tokens_exceeded"}})
        state = body["state"]
        goal = state["goal"] if isinstance(state, dict) else str(state)
        answers = {
            qid: {"type": "noul", "noul": score(goal, question, state)}
            for qid, question in body["questions"].items()
        }
        tokens = math.ceil(len(json.dumps(body, ensure_ascii=False)) / self._price_divisor)
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "answers": answers,
                "usage": {"input_tokens": tokens, "output_tokens": 2 * len(answers)},
            },
        )
