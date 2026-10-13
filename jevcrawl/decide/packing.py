"""Pack a page's questions into requests under Jev's context limits."""

from __future__ import annotations

import json
from typing import Any

from jevcrawl.models import estimate_tokens

MAX_REQUEST_TOKENS = 55_000  # Jev allows 64k per request; leave headroom
MAX_STATE_PLUS_QUESTION_TOKENS = 30_000  # Jev allows 32k for state plus the longest question


class PackingError(ValueError):
    """The state plus one question cannot fit in a request."""


def dumps(value: Any) -> str:
    """JSON as sent on the wire."""
    return json.dumps(value, ensure_ascii=False)


def estimate_request_tokens(body: dict[str, Any]) -> int:
    """Token estimate of a request body."""
    return estimate_tokens(dumps(body))


def _tokens(value: Any) -> int:
    return estimate_tokens(dumps(value))


def pack_questions(
    model: str,
    state: dict[str, Any],
    questions: dict[str, dict[str, Any]],
    *,
    max_tokens: int = MAX_REQUEST_TOKENS,
) -> list[dict[str, Any]]:
    """Split ``questions`` into request bodies of at most ``max_tokens`` estimated tokens.

    A page normally fits one request (the "speculative fan-out" pattern). A larger page is
    cut into chunks that each repeat ``state``. Raises :class:`PackingError` if ``state`` plus
    the longest single question would exceed Jev's 32k limit (or one chunk could not hold it).
    """
    base = estimate_request_tokens({"model": model, "state": state, "questions": {}})
    if questions:
        longest = max(_tokens({qid: q}) for qid, q in questions.items())
        if base + longest >= MAX_STATE_PLUS_QUESTION_TOKENS:
            raise PackingError(
                f"state ({base} tokens) plus the longest question ({longest} tokens) "
                f"reaches the {MAX_STATE_PLUS_QUESTION_TOKENS}-token limit"
            )
    chunks: list[dict[str, dict[str, Any]]] = []
    current: dict[str, dict[str, Any]] = {}
    used = base
    for qid, question in questions.items():
        cost = _tokens({qid: question})
        if current and used + cost > max_tokens:
            chunks.append(current)
            current, used = {}, base
        if base + cost > max_tokens:
            raise PackingError(f"question {qid!r} does not fit in a {max_tokens}-token request")
        current[qid] = question
        used += cost
    if current:
        chunks.append(current)
    return [{"model": model, "state": state, "questions": chunk} for chunk in chunks]
