"""URLs the crawler must never fetch are never fetched (respx call counts)."""

import pytest

from jevcrawl.fetch.http import Fetcher
from jevcrawl.segment.blocks import segment_html
from jevcrawl.segment.links import Scope, canonical_url, free_filter_links, is_skippable
from tests.fixtures.fake_site import BASE, mount_site, read


@pytest.fixture
def home():
    return segment_html(read("index.html"), BASE + "/")


def urls(links):
    return [link.url for link in links]


def test_prefilter_drops_login_pdf_and_out_of_scope(home):
    kept = urls(free_filter_links(home.links, Scope(BASE + "/"), set(), set()))
    assert BASE + "/help/returns" in kept
    assert BASE + "/login" not in kept
    assert BASE + "/files/size-guide.pdf" not in kept
    assert "https://other.example.org/partners" not in kept


def test_same_domain_false_allows_other_hosts(home):
    scope = Scope(BASE + "/", same_domain=False)
    assert "https://other.example.org/partners" in urls(
        free_filter_links(home.links, scope, set(), set())
    )


def test_subdomains_are_in_scope_but_siblings_are_not():
    scope = Scope("https://example.com/")
    assert scope.contains("https://docs.example.com/a")
    assert not scope.contains("https://notexample.com/a")
    assert not scope.contains("https://example.org/a")


def test_seen_and_queued_urls_are_skipped(home):
    seen = {BASE + "/help/returns"}
    queued = {BASE + "/jobs"}
    kept = urls(free_filter_links(home.links, Scope(BASE + "/"), seen, set(), queued=queued))
    assert BASE + "/help/returns" not in kept and BASE + "/jobs" not in kept
    assert BASE + "/help/shipping" in kept


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/logout",
        "https://x.com/users/sign-in/../login.php",
        "https://x.com/my-account/orders",
        "https://x.com/cart",
        "https://x.com/checkout/step1",
        "https://x.com/wp-admin/edit.php",
        "https://x.com/post?replytocom=5",
        "https://x.com/events/calendar/2026",
        "https://x.com/shop?sort=price",
        "https://x.com/shop?color=red&filter=new",
        "https://x.com/a/b.PDF",
        "https://x.com/pic.jpeg",
        "https://x.com/deck.pptx",
    ],
)
def test_skippable(url):
    assert is_skippable(url)


@pytest.mark.parametrize(
    "url",
    ["https://x.com/accounting", "https://x.com/help/returns", "https://x.com/blog/cartography",
     "https://x.com/page.html?sorted=1", "https://x.com/loginfo",
     # real: GitHub docs sections whose slugs merely mention a trap word
     "https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens",
     "https://x.com/docs/how-to-login-with-sso-and-manage-your-cart-items"],
)  # fmt: skip
def test_not_skippable(url):
    assert not is_skippable(url)


def test_cap_prefers_text_then_surviving_blocks_then_position():
    from jevcrawl.models import Link
    from jevcrawl.segment.links import cap_links

    links = [
        Link("L0", "https://x.com/a", "", "", 0, "b0"),
        Link("L1", "https://x.com/b", "B", "", 1, "b1"),
        Link("L2", "https://x.com/c", "C", "", 2, "b0"),
        Link("L3", "https://x.com/d", "D", "", 3, "b0"),
        Link("L4", "https://x.com/b", "B again", "", 4, "b0"),  # duplicate URL, better block
    ]
    kept = cap_links(links, 2, {"b0"})
    # L0 has no anchor text; L1 is in a block that did not survive; so L2 and L3 win on position
    assert [link.id for link in kept] == ["L2", "L3"]
    everything = cap_links(links, 10, {"b0"})
    assert len(everything) == 4  # de-duplicated by URL
    assert next(link for link in everything if link.url.endswith("/b")).id == "L4"  # best copy


async def test_forbidden_urls_are_never_requested(respx_mock, home):
    routes = mount_site(respx_mock)
    scope = Scope(BASE + "/")
    candidates = free_filter_links(home.links, scope, {canonical_url(BASE + "/")}, set())
    async with Fetcher() as fetcher:
        for link in candidates:  # a naive crawl would visit every link on the page
            await fetcher.fetch(link.url)

    assert routes["/login"].call_count == 0
    assert routes["/files/size-guide.pdf"].call_count == 0
    assert routes["other"].call_count == 0
    assert routes["/robots.txt"].call_count == 0  # never consulted
    assert routes["/help/returns"].call_count == 1
    assert routes["/jobs"].call_count == 1


def test_cap_prefers_links_that_share_words_with_the_goal():
    from jevcrawl.models import Link
    from jevcrawl.segment.links import cap_links, goal_words

    menu = [
        Link(f"L{i}", f"https://x.com/docs/page-{i}", f"Topic {i}", "", i, None) for i in range(10)
    ]
    late = "https://x.com/docs/managing-your-personal-access-tokens"
    menu.append(Link("L10", late, "Managing tokens", "", 10, None))
    goal = "How do I create a personal access token?"
    assert len(goal_words(goal)) == 4  # create, personal, access, token (stopwords dropped)
    kept = cap_links(menu, 3, set(), goal)
    assert "L10" in {link.id for link in kept}  # the late menu entry beats earlier, unrelated ones
    assert [link.position for link in kept] == sorted(link.position for link in kept)
    assert "L10" not in {link.id for link in cap_links(menu, 3, set())}  # position alone drops it
    # in-content links still beat menu links, goal or not
    content = Link("L99", "https://x.com/unrelated", "Elsewhere", "", 99, "b0")
    assert content in cap_links([*menu, content], 1, {"b0"}, "personal access token")
