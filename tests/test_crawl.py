"""Phase 3: frontier, loop, packing, scrape, map (tests 5, 6, 8, 10, 11 at crawl level)."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from jevcrawl.config import Thresholds
from jevcrawl.crawl import CrawlError, crawl, map_site, scrape
from jevcrawl.crawl.frontier import Frontier, link_priority
from jevcrawl.crawl.loop import classify
from jevcrawl.crawl.pack import by_relevance, cut_to_tokens, pack_result, top_k_excerpts
from jevcrawl.crawl.stop import Budget
from jevcrawl.models import Block, Stats
from jevcrawl.segment.blocks import segment_html
from tests.fixtures.fake_site import BASE, mount_site, read
from tests.fixtures.jev_fake import FakeJev

GOAL = "How many days do I have to get a refund?"
REFUND = "Refunds are issued within 14 days of receiving the returned item."


@pytest.fixture
def world(respx_mock):
    """The fixture help-center plus a fake Jev, both behind respx."""
    return mount_site(respx_mock), FakeJev().install(respx_mock)


@pytest.fixture(autouse=True)
def no_real_sleeping(monkeypatch):
    real = asyncio.sleep

    async def quick(seconds, *a, **k):
        await real(0)

    monkeypatch.setattr(asyncio, "sleep", quick)


def paths(respx_mock):
    return [
        c.request.url.path for c in respx_mock.calls if c.request.url.host == "help.example.com"
    ]


# ------------------------------------------------------------------ test 10: end to end


async def test_end_to_end_finds_the_refund_sentence_and_stops_answered(world, config, respx_mock):
    routes, _ = world
    result = await crawl(GOAL, BASE + "/", config)
    data = result.to_dict()

    assert paths(respx_mock) == ["/", "/help/returns"]  # returns before jobs; no robots.txt
    assert routes["/jobs"].call_count == 0 and routes["/help/shipping"].call_count == 0
    assert data["stats"]["stopped_because"] == "answered"
    refund = [b for b in data["blocks"] if REFUND in b["text"]]
    assert refund and refund[0]["url"] == BASE + "/help/returns"
    assert refund[0]["relevance"] >= 0.35


async def test_returned_text_is_verbatim_and_offsets_verify(world, config):
    result = (await crawl(GOAL, BASE + "/", config)).to_dict()
    source = read("help/returns.html")
    page = segment_html(source, BASE + "/help/returns")
    record = next(p for p in result["pages"] if p["url"] == BASE + "/help/returns")
    assert record["text_sha256"] == page.text_sha256
    for block in result["blocks"] + result["uncertain"]:
        if block["url"] == record["url"]:
            assert page.text[block["char_start"] : block["char_end"]] == block["text"]


async def test_stats_report_cost_model_and_savings(world, config):
    stats = (await crawl(GOAL, BASE + "/", config)).stats
    assert (
        stats.pages_fetched == 2 and stats.jev_calls == 4
    )  # 2 pages, each followed by "answered?"
    assert stats.jev_input_tokens > 0 and stats.jev_cost_usd == pytest.approx(
        stats.jev_input_tokens * 0.042 / 1e6
    )
    assert (stats.model, stats.degraded) == ("jev-1.13.0", False)
    assert 0 < stats.tokens_returned < stats.tokens_raw
    assert stats.elapsed_seconds >= 0


async def test_robots_txt_is_never_requested(world, config):
    routes, _ = world
    await crawl(GOAL, BASE + "/", config)
    assert routes["/robots.txt"].call_count == 0
    ua = routes["/"].calls.last.request.headers["user-agent"]
    assert ua.startswith("jevcrawl/")


# ------------------------------------------------------------------ test 5 / 6 / 8 at crawl level


async def test_second_identical_crawl_makes_zero_jev_calls(world, config):
    _, fake = world
    first = await crawl(GOAL, BASE + "/", config)
    calls = fake.call_count
    assert calls > 0 and first.stats.jev_cost_usd > 0
    second = await crawl(GOAL, BASE + "/", config)
    assert fake.call_count == calls
    assert second.stats.jev_calls == 0 and second.stats.cache_hits > 0
    assert second.stats.jev_cost_usd == 0
    assert [b.text for b in second.blocks] == [b.text for b in first.blocks]


async def test_tiny_budget_stops_with_budget_before_sending(world, config):
    _, fake = world
    result = await crawl(GOAL, BASE + "/", config, max_decision_usd=1e-9)
    assert result.stats.stopped_because == "budget"
    assert fake.call_count == 0 and result.stats.pages_fetched == 1


async def test_three_failed_requests_degrade_but_still_return_blocks(world, config):
    routes, fake = world
    fake.always = 529
    result = await crawl(GOAL, BASE + "/", config, max_pages=10)
    stats = result.stats
    assert stats.degraded and "consecutive" in stats.degraded_reason
    assert stats.stopped_because == "frontier_empty"
    assert result.blocks == [] and result.uncertain  # nothing scored, nothing lost
    assert all(b.relevance is None for b in result.uncertain)
    assert fake.call_count == 15  # three failed requests of five attempts each, then none
    assert BASE + "/app" in stats.needs_js_urls  # JS shells are reported, not rendered
    assert routes["/jobs"].call_count == 1  # breadth-first within the budget kept going


async def test_401_stops_with_auth_error(world, config):
    _, fake = world
    fake.always = 401
    result = await crawl(GOAL, BASE + "/", config)
    assert result.stats.stopped_because == "auth_error" and fake.call_count == 1


async def test_missing_key_is_a_clear_error_before_any_request(world, config):
    routes, fake = world
    with pytest.raises(CrawlError) as info:
        await crawl(GOAL, BASE + "/", replace(config, api_key=None))
    assert info.value.code == "auth_error" and "TYPESAFE_API_KEY" in info.value.message
    assert routes["/"].call_count == 0 and fake.call_count == 0


# ------------------------------------------------------------------ expected errors


async def test_invalid_start_urls_and_robots_is_ignored(world, config):
    routes, _ = world
    for bad in ("ftp://x.example/", "not a url", "/relative"):
        with pytest.raises(CrawlError) as info:
            await crawl(GOAL, bad, config)
        assert info.value.code == "invalid_url"
    result = await crawl(GOAL, BASE + "/private/area", config)  # robots.txt is not consulted
    assert result.stats.pages_fetched == 1 and routes["/private/area"].call_count == 1


async def test_max_pages_and_frontier_empty(world, config):
    capped = await crawl("shipping jobs refund", BASE + "/", config, max_pages=1)
    assert capped.stats.pages_fetched == 1 and capped.stats.stopped_because == "max_pages"
    # an unanswerable goal keeps going until the links run out
    wide = await crawl("quantum chromodynamics", BASE + "/", config, max_pages=50)
    assert wide.stats.stopped_because == "frontier_empty"


async def test_max_seconds(world, config, monkeypatch):
    ticks = iter(range(0, 1000, 100))
    clock = SimpleNamespace(monotonic=lambda: next(ticks))
    monkeypatch.setattr("jevcrawl.crawl.loop.time", clock)
    result = await crawl(GOAL, BASE + "/", config, max_seconds=150)
    assert result.stats.stopped_because == "max_seconds"


# ------------------------------------------------------------------ test 11: scrape and map


async def test_scrape_without_goal_makes_no_jev_call(world, config):
    routes, fake = world
    result = await scrape(BASE + "/help/returns", replace(config, api_key=None))
    assert fake.call_count == 0 and routes["/help/returns"].call_count == 1
    texts = [b.text for b in result.blocks]
    assert any(REFUND in t for t in texts)
    assert all("cookies" not in t.lower() or "Refund" not in t for t in texts)
    assert all(b.relevance is None for b in result.blocks) and result.uncertain == []
    assert result.stats.jev_calls == 0 and result.stats.stopped_because == "single_page"


async def test_scrape_with_goal_asks_block_questions_only(world, config):
    _, fake = world
    result = await scrape(BASE + "/help/returns", config, GOAL)
    assert fake.call_count == 1
    assert all(qid.startswith("b") for qid in fake.requests[0]["questions"])
    assert any(REFUND in b.text for b in result.blocks)
    assert result.stats.jev_calls == 1 and result.stats.pages_fetched == 1


async def test_scrape_reports_js_shells(world, config):
    result = await scrape(BASE + "/app", replace(config, api_key=None))
    assert result.stats.needs_js_urls == [BASE + "/app"] and result.blocks == []


async def test_map_ranks_returns_first_with_link_questions_only(world, config):
    _, fake = world
    data = await map_site(BASE + "/", GOAL, config, limit=10, max_pages=3)
    urls = [u["url"] for u in data["urls"]]
    assert urls[0] == BASE + "/help/returns"
    assert set(urls[0:1]) <= {BASE + "/help/returns"}
    for body in fake.requests:
        assert all(qid.startswith("L") for qid in body["questions"])
        assert "answered" not in body["questions"]
    first = data["urls"][0]
    assert set(first) == {"url", "priority", "p_target", "p_hub", "anchor", "found_on"}
    assert first["anchor"] == "Returns & refunds" and first["found_on"] == BASE + "/"
    assert [u["priority"] for u in data["urls"]] == sorted(
        (u["priority"] for u in data["urls"]), reverse=True
    )
    assert BASE + "/login" not in urls and BASE + "/files/size-guide.pdf" not in urls
    assert BASE + "/private/area" in urls  # robots.txt is not consulted


async def test_map_limit(world, config):
    data = await map_site(BASE + "/", GOAL, config, limit=1)
    assert len(data["urls"]) == 1


# ------------------------------------------------------------------ units


def test_frontier_is_best_first_and_dedupes():
    f = Frontier()
    f.push("a", 0.2, 1)
    f.push("b", 0.9, 1)
    f.push("c", 0.5, 2)
    assert f.push("b", 1.0, 1) is False and f.queued == {"a", "b", "c"}
    assert [f.pop().url for _ in range(3)] == ["b", "c", "a"]
    assert f.queued == set() and len(f) == 0


def test_frontier_breadth_first_switch_orders_by_depth_then_arrival():
    f = Frontier()
    f.push("deep-high", 0.9, 3)
    f.push("shallow-low", 0.1, 1)
    f.push("shallow-2", 0.2, 1)
    f.breadth_first()
    f.push("shallow-3", 0.99, 1)
    assert [f.pop().url for _ in range(4)] == ["shallow-low", "shallow-2", "shallow-3", "deep-high"]


def test_link_priority_formula():
    p = Thresholds(hub_weight=0.6, page_weight=0.5, depth_decay=0.85)
    expected = max(0.8, 0.6 * 1.0) * (0.5 + 0.5 * 0.4) * 0.85**3
    assert link_priority(0.8, 1.0, 0.4, 2, p) == pytest.approx(expected)
    assert link_priority(0.1, 1.0, 0.0, 0, p) == pytest.approx(0.6 * 0.5 * 0.85)
    # defaults: a strong hub link on an irrelevant listing page keeps most of its score
    assert link_priority(0.3, 0.8, 0.0, 1, Thresholds()) == pytest.approx(0.48 * 0.8 * 0.9**2)


def block(i, rel=None, tokens=100):
    return Block(f"b{i}", "u", "h", f"text {i}", 0, 0, tokens, 0, rel)


def test_classify_thresholds_and_per_page_cap():
    p = Thresholds()
    blocks = [block(i) for i in range(6)]
    kept, unsure = classify(blocks, [0.9, 0.35, 0.34, 0.2, 0.19, None], p, cap=10)
    assert [b.id for b in kept] == ["b0", "b1"]
    assert sorted(b.id for b in unsure) == ["b2", "b3", "b5"]  # 0.19 is dropped
    capped, overflow = classify(blocks, [0.9, 0.8, 0.7, 0.6, 0.5, 0.4], p, cap=2)
    assert [b.id for b in capped] == ["b0", "b1"]
    assert [b.id for b in overflow] == ["b2", "b3", "b4", "b5"]  # moved, not lost


def test_pack_result_sorts_cuts_and_caps_uncertain():
    kept = [block(0, 0.5), block(1, 0.9), block(2, 0.7), block(3, 0.8)]
    unsure = [block(4, 0.3), block(5, None), block(6, 0.25), block(7, 0.22)]
    stats = Stats()
    result = pack_result(kept, unsure, [], stats, return_tokens=400)
    assert [b.id for b in result.blocks] == ["b1", "b3", "b2", "b0"]
    assert [b.id for b in result.uncertain] == ["b4"]  # 25% of 400 tokens = one 100-token block
    assert stats.tokens_returned == 500
    small = pack_result(kept, [], [], Stats(), return_tokens=250)
    assert [b.id for b in small.blocks] == ["b1", "b3"]


def test_by_relevance_puts_unscored_last_in_original_order():
    blocks = [block(0, None), block(1, 0.2), block(2, None), block(3, 0.9)]
    assert [b.id for b in by_relevance(blocks)] == ["b3", "b1", "b0", "b2"]
    assert cut_to_tokens(blocks, 150) == blocks[:1]


def test_excerpts_hold_the_top_blocks_up_to_2000_tokens():
    kept = [block(i, 0.5 + i / 100, tokens=900) for i in range(5)]
    excerpts = top_k_excerpts(kept)
    assert [e["text"] for e in excerpts] == ["text 4", "text 3"]
    assert set(excerpts[0]) == {"url", "heading", "text"}
    huge = [block(0, 0.9, tokens=5000)]
    assert len(top_k_excerpts(huge)) == 1  # always at least the best block


def test_budget_exhaustion():
    b = Budget(max_pages=3, max_seconds=10)
    assert b.exhausted(2, 0.0, now=5.0) is None
    assert b.exhausted(3, 0.0, now=5.0) == "max_pages"
    assert b.exhausted(1, 0.0, now=10.0) == "max_seconds"


async def test_map_does_not_halve_priorities_for_lack_of_block_scores(world, config):
    data = await map_site(BASE + "/", GOAL, config, limit=10, max_pages=1)
    first = data["urls"][0]
    expected = max(first["p_target"], 0.6 * first["p_hub"]) * 0.9  # page factor 1.0, depth 0
    assert first["priority"] == pytest.approx(expected, abs=1e-3)


async def test_degraded_crawl_may_use_the_whole_budget_for_unscored_blocks(world, config):
    _, fake = world
    fake.always = 529
    result = await crawl(GOAL, BASE + "/", config, max_pages=10, return_tokens=4000)
    assert result.stats.degraded and result.blocks == []
    assert result.stats.tokens_returned > 0.25 * 4000 * 0.4  # not squeezed into 25%
    small = pack_result([], [block(i, None, 100) for i in range(10)], [], Stats(degraded=True), 400)
    assert len(small.uncertain) == 4  # degraded: the full 400 tokens
    normal = pack_result([], [block(i, 0.3, 100) for i in range(10)], [], Stats(), 400)
    assert len(normal.uncertain) == 1  # healthy crawl: 25% of the budget


async def test_scheme_less_start_urls_get_https(world, config):
    result = await crawl(GOAL, "help.example.com/", config)
    assert result.stats.pages_fetched >= 1
    for bad in ("not a url", "/relative", "ftp://x.example/", "localhost"):
        with pytest.raises(CrawlError):
            await crawl(GOAL, bad, config)


async def test_start_page_is_retried_after_a_transient_failure(world, config, respx_mock):
    import httpx

    routes, _ = world
    real = routes["/"].side_effect or routes["/"].return_value
    routes["/"].side_effect = [httpx.ConnectError("blip"), real]
    result = await crawl(GOAL, BASE + "/", config)
    assert routes["/"].call_count == 2 and result.stats.stopped_because == "answered"

    respx_mock.get("https://gone.example/").mock(return_value=httpx.Response(404))
    with pytest.raises(CrawlError) as info:  # a 404 is final: asked once, not retried
        await scrape("https://gone.example/", config)
    assert (
        info.value.code == "fetch_failed"
        and respx_mock.calls.last.request.url.host == "gone.example"
    )
    assert sum(c.request.url.host == "gone.example" for c in respx_mock.calls) == 1


async def test_links_below_the_floor_are_a_reserve_not_a_dead_end(world, config):
    # Real Jev scores navigation links low; a floor that drops them all must not end the crawl
    # after one page. Here every link is below the floor.
    floor_everything = replace(config, thresholds=Thresholds(link_floor=0.99))
    result = await crawl("zebra migration patterns", BASE + "/", floor_everything, max_pages=4)
    assert result.stats.pages_fetched == 4 and result.stats.stopped_because == "max_pages"

    # With a normal floor the reserve is only used after the scored links run out.
    normal = await crawl(GOAL, BASE + "/", config)
    assert normal.stats.stopped_because == "answered" and normal.stats.pages_fetched == 2


# ------------------------------------------------------- sitemap, parallel fetching


def mount_sitemap(respx_mock, xml: bytes, *, index_child: bytes | None = None):
    import httpx

    page = "<html><body><h1>Warranty claims</h1><p>To file a warranty claim, send the claim form within 60 days of delivery.</p></body></html>"  # noqa: E501
    deep = respx_mock.get(BASE + "/support/2023/warranty-claim-form").mock(
        return_value=httpx.Response(200, text=page, headers={"content-type": "text/html"})
    )
    respx_mock.get(BASE + "/sitemap.xml").mock(return_value=httpx.Response(200, content=xml))
    for path in ("/sitemap_index.xml", "/wp-sitemap.xml"):
        respx_mock.get(BASE + path).mock(return_value=httpx.Response(404))
    if index_child is not None:
        respx_mock.get(BASE + "/posts-sitemap.xml.gz").mock(
            return_value=httpx.Response(200, content=index_child)
        )
    return deep


URLSET = (
    b'<?xml version="1.0"?><urlset>'
    b"<url><loc>https://help.example.com/jobs</loc></url>"
    b"<url><loc>https://help.example.com/support/2023/warranty-claim-form</loc></url>"
    b"<url><loc>https://other.example.org/warranty-claim</loc></url>"
    b"</urlset>"
)


async def test_sitemap_finds_a_page_no_link_points_to(world, config, respx_mock):
    deep = mount_sitemap(respx_mock, URLSET)
    goal = "How do I file a warranty claim?"
    result = await crawl(goal, BASE + "/", replace(config, use_sitemap=True), max_pages=3)
    assert deep.call_count == 1 and result.stats.stopped_because == "answered"
    assert any("warranty claim" in b.text for b in result.blocks)
    # off: the page is unreachable through links alone
    deep.reset()
    await crawl(goal, BASE + "/", replace(config, cache_dir=config.cache_dir / "2"), max_pages=3)
    assert deep.call_count == 0


async def test_sitemap_index_and_gzip_are_followed(world, config, respx_mock):
    import gzip

    index = (
        b"<sitemapindex><sitemap><loc>https://help.example.com/posts-sitemap.xml.gz</loc>"
        b"</sitemap></sitemapindex>"
    )
    deep = mount_sitemap(respx_mock, index, index_child=gzip.compress(URLSET))
    goal = "How do I file a warranty claim?"
    await crawl(goal, BASE + "/", replace(config, use_sitemap=True), max_pages=3)
    assert deep.call_count == 1


async def test_a_broken_sitemap_never_breaks_the_crawl(world, config, respx_mock):
    import httpx

    respx_mock.get(BASE + "/sitemap.xml").mock(side_effect=httpx.ConnectError("x"))
    for path in ("/sitemap_index.xml", "/wp-sitemap.xml"):
        respx_mock.get(BASE + path).mock(return_value=httpx.Response(200, content=b"\x1f\x8bjunk"))
    result = await crawl(GOAL, BASE + "/", replace(config, use_sitemap=True))
    assert result.stats.stopped_because == "answered"


def test_sitemap_links_need_shared_goal_words_and_stay_in_scope():
    from jevcrawl.crawl.sitemap import sitemap_links, slug_text
    from jevcrawl.segment.links import Scope

    assert slug_text("https://x.org/events/open-session-equity-1-february-10-2023/") == (
        "events / open session equity 1 february 10 2023"
    )
    urls = [
        "https://x.org/events/open-session-equity-1-february-10-2023/",
        "https://x.org/events/open-session-budget-march-3-2023/",
        "https://x.org/info/2124/10671.htm",
        "https://other.org/events/open-session-equity-february-2023/",
    ]
    goal = "What was discussed at the SIGCHI open session on February 10, 2023?"
    links = sitemap_links(urls, goal, Scope("https://x.org/"), set(), 10)
    assert links[0].url == urls[0]  # most shared words first
    assert urls[2] not in {link.url for link in links}  # numeric URL: no words, not offered
    assert all(link.url.startswith("https://x.org/") for link in links)
    assert [link.id for link in links] == [f"S{i}" for i in range(len(links))]
    assert sitemap_links(urls, goal, Scope("https://x.org/"), {urls[0]}, 10)[0].url != urls[0]


async def test_parallel_fetching_keeps_budgets_and_finds_the_answer(world, config):
    parallel = replace(config, fetch_concurrency=4)
    result = await crawl(GOAL, BASE + "/", parallel)
    assert result.stats.stopped_because == "answered"
    assert any("14 days" in b.text for b in result.blocks)
    capped = await crawl(
        "zebra migration",
        BASE + "/",
        replace(parallel, cache_dir=config.cache_dir / "p"),
        max_pages=2,
    )
    assert capped.stats.pages_fetched == 2


def test_goal_words_split_chinese_into_bigrams():
    from jevcrawl.models import Link
    from jevcrawl.segment.links import goal_overlap, goal_words

    words = goal_words("北京工业大学计算机学院在哪两个国际会议上发表论文")
    assert {"北京", "会议", "论文"} <= words
    hit = Link("", "https://x/info/1333/2209.htm", "计算机学院教师在国际会议发表论文", "", 0)
    miss = Link("", "https://x/a", "学校首页", "", 0)
    assert goal_overlap(words, hit) > 3 and goal_overlap(words, miss) == 0
