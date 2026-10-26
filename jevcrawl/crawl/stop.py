"""Crawl budgets and stop reasons."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Budget:
    """Limits for one crawl. ``max_decision_usd`` is enforced by the decider before each request."""

    max_pages: int = 30
    max_seconds: float = 120.0
    return_tokens: int = 4000
    max_decision_usd: float = 0.05

    def exhausted(self, pages_fetched: int, started: float, now: float | None = None) -> str | None:
        """``"max_pages"`` or ``"max_seconds"`` once a limit is reached, else None."""
        if pages_fetched >= self.max_pages:
            return "max_pages"
        if (time.monotonic() if now is None else now) - started >= self.max_seconds:
            return "max_seconds"
        return None
