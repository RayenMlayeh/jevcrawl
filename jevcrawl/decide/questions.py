"""The exact question wording sent to Jev.

Page text is data, never instructions: it goes only into data fields (``instructions.block``,
``instructions.link``, ``state.excerpts``). Every question sentence and every ``criteria``
string below is a fixed constant written by jevcrawl.
"""

from __future__ import annotations

from typing import Any, Final

from jevcrawl.models import Block, Link

BLOCK_QUESTION: Final = "`block` contains information that helps answer `goal`."
BLOCK_CRITERIA: Final = {
    "true": "States a fact, number, rule or step that helps answer the goal",
    "false": "Navigation, promotion, boilerplate, or a different topic",
}
TARGET_QUESTION: Final = "Following `link` leads directly to a page that answers `goal`."
TARGET_CRITERIA: Final = {
    "true": "The destination page itself holds the answer",
    "false": "It does not",
}
HUB_QUESTION: Final = (
    "Following `link` leads to a section likely to contain pages that answer `goal`."
)
HUB_CRITERIA: Final = {
    "true": "An index or section on the goal's topic",
    "false": "An unrelated section",
}
ANSWERED_QUESTION: Final = "The `excerpts` together fully answer `goal`."
ANSWERED_CRITERIA: Final = {
    "true": "Every part of the goal is covered by the excerpts",
    "false": "Something the goal asks for is missing or only implied",
}

FIXED_QUESTIONS: Final = frozenset(
    {BLOCK_QUESTION, TARGET_QUESTION, HUB_QUESTION, ANSWERED_QUESTION}
)


def block_key(index: int) -> str:
    """Question id for the ``index``-th block sent on a page."""
    return f"b{index}"


def target_key(index: int) -> str:
    """Question id for the link-target question of the ``index``-th link."""
    return f"L{index}_target"


def hub_key(index: int) -> str:
    """Question id for the link-hub question of the ``index``-th link."""
    return f"L{index}_hub"


def page_state(goal: str, url: str, title: str) -> dict[str, Any]:
    """The shared ``state`` of every page request."""
    return {"goal": goal, "page": {"url": url, "title": title}}


def block_question(block: Block) -> dict[str, Any]:
    """Relevance question for one block."""
    return {
        "type": "noul",
        "instructions": {
            "block": {"heading": block.heading_path, "text": block.text},
            "question": BLOCK_QUESTION,
        },
        "criteria": dict(BLOCK_CRITERIA),
    }


def _link_data(link: Link) -> dict[str, str]:
    return {"text": link.text, "url": link.url, "context": link.context}


def target_question(link: Link) -> dict[str, Any]:
    """ "Does following this link lead directly to the answer?" """
    return {
        "type": "noul",
        "instructions": {"link": _link_data(link), "question": TARGET_QUESTION},
        "criteria": dict(TARGET_CRITERIA),
    }


def hub_question(link: Link) -> dict[str, Any]:
    """ "Does following this link lead to a section likely to contain the answer?" """
    return {
        "type": "noul",
        "instructions": {"link": _link_data(link), "question": HUB_QUESTION},
        "criteria": dict(HUB_CRITERIA),
    }


def page_questions(
    blocks: list[Block],
    links: list[Link],
    *,
    ask_blocks: bool = True,
    ask_links: bool = True,
) -> dict[str, dict[str, Any]]:
    """All questions for one page, keyed ``b{i}``, ``L{i}_target`` and ``L{i}_hub``."""
    questions: dict[str, dict[str, Any]] = {}
    if ask_blocks:
        for i, block in enumerate(blocks):
            questions[block_key(i)] = block_question(block)
    if ask_links:
        for i, link in enumerate(links):
            questions[target_key(i)] = target_question(link)
            questions[hub_key(i)] = hub_question(link)
    return questions


def answered_state(goal: str, excerpts: list[dict[str, str]]) -> dict[str, Any]:
    """``state`` of the "answered?" request; each excerpt is ``{url, heading, text}``."""
    return {"goal": goal, "excerpts": excerpts}


def answered_question() -> dict[str, Any]:
    """The single "answered?" question."""
    return {
        "type": "noul",
        "instructions": ANSWERED_QUESTION,
        "criteria": dict(ANSWERED_CRITERIA),
    }
