"""Best-first frontier: always fetch the link with the highest priority next."""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass

from jevcrawl.config import Thresholds


@dataclass(frozen=True)
class FrontierItem:
    """A queued URL and where it came from."""

    url: str
    priority: float
    depth: int
    parent_url: str | None = None
    anchor: str = ""
    p_target: float | None = None
    p_hub: float | None = None


def link_priority(
    p_target: float, p_hub: float, page_rel: float, depth: int, profile: Thresholds
) -> float:
    """``max(p_target, hub_weight * p_hub) * page_factor * depth_decay ** (depth + 1)``.

    ``page_factor = (1 - page_weight) + page_weight * page_rel``.

    ``depth`` is the depth of the page the link was found on; ``page_rel`` is the highest block
    relevance on that page (0 if none).
    """
    best = max(p_target, profile.hub_weight * p_hub)
    page_factor = (1 - profile.page_weight) + profile.page_weight * page_rel
    return best * page_factor * profile.depth_decay ** (depth + 1)


class Frontier:
    """A heap of ``(-priority, seq, item)``. Each URL is queued at most once.

    :meth:`breadth_first` switches to ``(depth, seq)`` ordering for degraded mode.
    """

    def __init__(self) -> None:
        self._seq = itertools.count()
        self._heap: list[tuple[float, int, FrontierItem]] = []
        self._queued: set[str] = set()
        self._bfs = False

    def __len__(self) -> int:
        return len(self._heap)

    @property
    def queued(self) -> set[str]:
        """URLs currently waiting in the frontier."""
        return self._queued

    @property
    def is_breadth_first(self) -> bool:
        """True once degraded mode switched the ordering."""
        return self._bfs

    def push(
        self,
        url: str,
        priority: float,
        depth: int,
        parent_url: str | None = None,
        *,
        anchor: str = "",
        p_target: float | None = None,
        p_hub: float | None = None,
    ) -> bool:
        """Queue ``url``; returns False if it was already queued."""
        if url in self._queued:
            return False
        item = FrontierItem(url, priority, depth, parent_url, anchor, p_target, p_hub)
        heapq.heappush(self._heap, (self._key(item), next(self._seq), item))
        self._queued.add(url)
        return True

    def pop(self) -> FrontierItem:
        """Remove and return the next item to fetch."""
        _, _, item = heapq.heappop(self._heap)
        self._queued.discard(item.url)
        return item

    def breadth_first(self) -> None:
        """Re-order the queue by depth (then arrival order); new pushes follow the same rule."""
        self._bfs = True
        old = list(self._heap)
        self._heap = [(self._key(item), seq, item) for _, seq, item in old]
        heapq.heapify(self._heap)

    def _key(self, item: FrontierItem) -> float:
        return float(item.depth) if self._bfs else -item.priority
