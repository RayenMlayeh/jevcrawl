"""Turns a page's blocks and links into Jev scores: cache, pack, budget, send, degrade.

No LLM is called here: only the Jev decision endpoint.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from jevcrawl.config import Config
from jevcrawl.decide import questions as q
from jevcrawl.decide.cache import DecisionCache, decision_key
from jevcrawl.decide.client import (
    DecisionClient,
    Evaluation,
    JevAuthError,
    JevError,
    JevRequestError,
)
from jevcrawl.decide.cost import CostTracker
from jevcrawl.decide.packing import estimate_request_tokens, pack_questions
from jevcrawl.log import get_logger
from jevcrawl.models import Block, Link, Page, Stats

log = get_logger("decide")

DEGRADE_AFTER = 3  # consecutive failed requests


class BudgetExceeded(Exception):
    """Sending the next request could pass ``max_decision_usd``; the crawl stops."""


@dataclass
class PageDecision:
    """Scores for one page. ``None`` means "no answer" (failed or degraded)."""

    blocks: list[float | None] = field(default_factory=list)
    targets: list[float | None] = field(default_factory=list)
    hubs: list[float | None] = field(default_factory=list)
    failed: bool = False


class Decider:
    """Per-crawl decision service. Owns the cost tracker and the degraded-mode state."""

    def __init__(
        self,
        config: Config,
        client: DecisionClient,
        cache: DecisionCache | None,
        stats: Stats,
        *,
        max_decision_usd: float = 0.05,
    ) -> None:
        self._config = config
        self._client = client
        self._cache = cache
        self.stats = stats
        self.cost = CostTracker(config.price_per_mtok, max_decision_usd)
        self.model = config.model
        self._failures = 0
        self._warned_model = False
        stats.model = self.model

    @property
    def degraded(self) -> bool:
        """True after 3 consecutive failed requests."""
        return self.stats.degraded

    async def page(
        self,
        goal: str,
        page: Page,
        blocks: list[Block],
        links: list[Link],
        *,
        ask_blocks: bool = True,
        ask_links: bool = True,
    ) -> PageDecision:
        """Score the blocks and links of one page with a single fan-out request (or chunks)."""
        questions = q.page_questions(blocks, links, ask_blocks=ask_blocks, ask_links=ask_links)
        decision = PageDecision(
            blocks=[None] * len(blocks) if ask_blocks else [],
            targets=[None] * len(links) if ask_links else [],
            hubs=[None] * len(links) if ask_links else [],
        )
        if not questions:
            return decision
        state = q.page_state(goal, page.final_url, page.title)
        answers = await self._ask(state, questions)
        decision.failed = len(answers) < len(questions)
        for i in range(len(decision.blocks)):
            decision.blocks[i] = answers.get(q.block_key(i))
        for i in range(len(decision.targets)):
            decision.targets[i] = answers.get(q.target_key(i))
            decision.hubs[i] = answers.get(q.hub_key(i))
        return decision

    async def answered(self, goal: str, excerpts: list[dict[str, str]]) -> float | None:
        """Do the excerpts fully answer the goal? ``None`` if undecidable (degraded or failed)."""
        if not excerpts:
            return None
        state = q.answered_state(goal, excerpts)
        answers = await self._ask(state, {"answered": q.answered_question()})
        return answers.get("answered")

    async def _ask(
        self, state: dict[str, Any], questions: dict[str, dict[str, Any]]
    ) -> dict[str, float]:
        if self.degraded:
            return {}
        answers, pending, keys = self._from_cache(state, questions)
        if not pending:
            return answers
        bodies = pack_questions(self.model, state, pending)
        estimates = [estimate_request_tokens(b) for b in bodies]
        if self.cost.would_exceed(sum(estimates)):
            raise BudgetExceeded(
                f"{sum(estimates)} estimated tokens would pass the "
                f"${self.cost.max_usd} decision budget (spent ${self.cost.spent_usd:.6f})"
            )
        results = await asyncio.gather(
            *(self._evaluate(b) for b in bodies),
            return_exceptions=True,
        )
        for body, result in zip(bodies, results, strict=True):
            if isinstance(result, JevAuthError):
                raise result
            if isinstance(result, JevError):
                self._fail(result)
            elif isinstance(result, BaseException):
                raise result
            else:
                self._succeed(result)
                self._store(body["questions"], result, keys, answers)
        return answers

    async def _evaluate(self, body: dict[str, Any]) -> Evaluation:
        """Send one request; if Jev says it is too large, split it in two and merge the answers.

        Our token estimate can still be too low for unusual text. Half of a request that fits is
        a request that fits, so a ``max_tokens_exceeded`` rejection is retried as two halves
        (recursively). If one half still fails the other half's answers are kept.
        """
        try:
            return await self._client.evaluate(body)
        except JevRequestError as exc:
            questions = body["questions"]
            if "max_tokens_exceeded" not in exc.body or len(questions) < 2:
                raise
            ids = list(questions)
            middle = len(ids) // 2
            halves = [
                {**body, "questions": {qid: questions[qid] for qid in part}}
                for part in (ids[:middle], ids[middle:])
            ]
            log.info("request too large (%d questions); retrying as two halves", len(ids))
            first, second = await asyncio.gather(
                *(self._evaluate(h) for h in halves), return_exceptions=True
            )
            parts = [r for r in (first, second) if isinstance(r, Evaluation)]
            if not parts:
                raise next(r for r in (first, second) if isinstance(r, BaseException)) from None
            return Evaluation(
                parts[0].model,
                {k: v for part in parts for k, v in part.nouls.items()},
                sum(part.input_tokens for part in parts),
                sum(part.output_tokens for part in parts),
            )

    def _from_cache(
        self, state: dict[str, Any], questions: dict[str, dict[str, Any]]
    ) -> tuple[dict[str, float], dict[str, dict[str, Any]], dict[str, str]]:
        answers: dict[str, float] = {}
        pending: dict[str, dict[str, Any]] = {}
        keys: dict[str, str] = {}
        for qid, question in questions.items():
            key = decision_key(self.model, state, question)
            keys[qid] = key
            hit = self._cache.get(key) if self._cache else None
            if hit is None:
                pending[qid] = question
            else:
                answers[qid] = hit
        self.cost.record_cache_hits(len(answers))
        self.stats.cache_hits = self.cost.cache_hits
        return answers, pending, keys

    def _store(
        self,
        asked: dict[str, Any],
        result: Evaluation,
        keys: dict[str, str],
        answers: dict[str, float],
    ) -> None:
        for qid in asked:
            if qid in result.nouls:
                answers[qid] = result.nouls[qid]
                if self._cache:
                    self._cache.put(keys[qid], result.model, result.nouls[qid])

    def _succeed(self, result: Evaluation) -> None:
        self._failures = 0
        self.cost.record_call(result.input_tokens)
        self.stats.jev_calls = self.cost.calls
        self.stats.jev_input_tokens = self.cost.input_tokens
        self.stats.jev_cost_usd = self.cost.spent_usd
        log.debug("model=%s input_tokens=%d", result.model, result.input_tokens)
        self.stats.model = result.model
        if result.model != self.model and not self._warned_model:
            self._warned_model = True
            log.warning(
                "Jev answered with model %r but thresholds are calibrated for %r",
                result.model,
                self.model,
            )

    def _fail(self, error: JevError) -> None:
        self._failures += 1
        log.warning("decision request failed (%d in a row): %s", self._failures, error)
        if self._failures >= DEGRADE_AFTER and not self.stats.degraded:
            self.stats.degraded = True
            self.stats.degraded_reason = (
                f"{self._failures} consecutive failed decision requests; last error: {error}"
            )
