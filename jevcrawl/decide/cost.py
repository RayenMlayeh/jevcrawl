"""Cost tracking and the hard per-crawl spend limit."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostTracker:
    """Sums Jev input tokens and dollars; Jev output tokens are free."""

    price_per_mtok: float = 0.042
    max_usd: float = 0.05
    input_tokens: int = 0
    calls: int = 0
    cache_hits: int = 0

    def usd(self, tokens: int) -> float:
        """Dollar cost of ``tokens`` input tokens."""
        return tokens * self.price_per_mtok / 1_000_000

    @property
    def spent_usd(self) -> float:
        """Dollars spent so far."""
        return self.usd(self.input_tokens)

    def would_exceed(self, estimated_tokens: int) -> bool:
        """True if sending a request of this estimated size could pass ``max_usd``."""
        return self.spent_usd + self.usd(estimated_tokens) > self.max_usd

    def record_call(self, input_tokens: int) -> None:
        """Account for one completed request."""
        self.calls += 1
        self.input_tokens += input_tokens

    def record_cache_hits(self, count: int = 1) -> None:
        """Account for answers served from the cache (they cost $0)."""
        self.cache_hits += count
