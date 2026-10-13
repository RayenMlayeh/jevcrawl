"""Live test against the real Jev API. Run with:  TYPESAFE_API_KEY=... uv run pytest -m live -s

One request of a few hundred input tokens: a fraction of a cent. Never runs in CI or by default.
"""

import pytest

from jevcrawl.config import load_config
from jevcrawl.decide import questions as q
from jevcrawl.decide.client import DecisionClient
from jevcrawl.segment.blocks import segment_html
from tests.conftest import REAL_TYPESAFE_KEY
from tests.fixtures.fake_site import BASE, read

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not REAL_TYPESAFE_KEY, reason="TYPESAFE_API_KEY not set"),
]

GOAL = "How many days do I have to get a refund?"


async def test_refund_block_outscores_the_footer_on_real_jev():
    config = load_config({"TYPESAFE_API_KEY": REAL_TYPESAFE_KEY})
    page = segment_html(read("help/returns.html"), BASE + "/help/returns", keep_layout_tags=True)
    refund = next(b for b in page.blocks if "Refunds are issued within 14 days" in b.text)
    footer = next(b for b in page.blocks if "Copyright" in b.text)
    questions = q.page_questions([refund, footer], [])
    body = {
        "model": config.model,
        "state": q.page_state(GOAL, page.final_url, page.title),
        "questions": questions,
    }
    async with DecisionClient(config) as client:
        result = await client.evaluate(body)

    cost = result.input_tokens * config.price_per_mtok / 1_000_000
    print(f"\nmodel={result.model} input_tokens={result.input_tokens} cost=${cost:.8f}")
    assert set(result.nouls) == {"b0", "b1"}
    assert all(0.0 <= v <= 1.0 for v in result.nouls.values())
    assert result.input_tokens > 0
    assert result.nouls["b0"] > result.nouls["b1"]
