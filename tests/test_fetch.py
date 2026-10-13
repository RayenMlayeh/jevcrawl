"""Fetcher behaviour: redirects, decoding, size cap, errors; robots.txt is never consulted."""

import httpx

from jevcrawl.fetch.http import MAX_BODY_BYTES, USER_AGENT, Fetcher, decode_body
from tests.fixtures.fake_site import BASE, mount_site

HTML = {"content-type": "text/html; charset=utf-8"}


async def test_fetches_a_page_with_an_honest_user_agent(respx_mock):
    routes = mount_site(respx_mock)
    async with Fetcher() as fetcher:
        result = await fetcher.fetch(BASE + "/help/returns")
    assert result.ok and result.status == 200 and "Refunds are issued" in result.html
    request = routes["/help/returns"].calls.last.request
    assert request.headers["user-agent"] == USER_AGENT
    assert USER_AGENT.startswith("jevcrawl/") and "(+" in USER_AGENT
    assert "text/html" in request.headers["accept"]
    assert routes["/robots.txt"].call_count == 0  # never consulted


async def test_non_html_is_reported_not_parsed(respx_mock):
    respx_mock.get(f"{BASE}/data").mock(
        return_value=httpx.Response(
            200, content=b"{}", headers={"content-type": "application/json"}
        )
    )
    async with Fetcher() as fetcher:
        result = await fetcher.fetch(BASE + "/data")
    assert result.error == "non_html" and result.html is None


async def test_redirects_are_followed_and_final_url_reported(respx_mock):
    mount_site(respx_mock)
    respx_mock.get(f"{BASE}/old").mock(
        return_value=httpx.Response(301, headers={"location": "/help/returns"})
    )
    async with Fetcher() as fetcher:
        result = await fetcher.fetch(BASE + "/old")
    assert result.ok and result.final_url == BASE + "/help/returns"


async def test_redirect_loop_gives_up(respx_mock):
    respx_mock.get(f"{BASE}/loop").mock(
        return_value=httpx.Response(302, headers={"location": "/loop"})
    )
    async with Fetcher() as fetcher:
        assert (await fetcher.fetch(BASE + "/loop")).error == "too_many_redirects"


async def test_body_is_capped_at_5_mb(respx_mock):
    respx_mock.get(f"{BASE}/big").mock(
        return_value=httpx.Response(200, content=b"a" * (MAX_BODY_BYTES + 1000), headers=HTML)
    )
    async with Fetcher() as fetcher:
        result = await fetcher.fetch(BASE + "/big")
    assert result.ok and len(result.html) == MAX_BODY_BYTES


async def test_network_and_http_errors_are_reported_not_raised(respx_mock):
    respx_mock.get(f"{BASE}/down").mock(side_effect=httpx.ConnectError("boom"))
    respx_mock.get(f"{BASE}/gone").mock(return_value=httpx.Response(404))
    async with Fetcher() as fetcher:
        assert (await fetcher.fetch(BASE + "/down")).error == "network"
        gone = await fetcher.fetch(BASE + "/gone")
    assert (gone.error, gone.status) == ("http_404", 404)


async def test_fetch_raw_returns_bodies_of_any_type_or_none(respx_mock):
    respx_mock.get(f"{BASE}/sitemap.xml").mock(return_value=httpx.Response(200, content=b"<x/>"))
    respx_mock.get(f"{BASE}/missing.xml").mock(return_value=httpx.Response(404))
    respx_mock.get(f"{BASE}/down.xml").mock(side_effect=httpx.ConnectError("boom"))
    async with Fetcher() as fetcher:
        assert await fetcher.fetch_raw(BASE + "/sitemap.xml") == b"<x/>"
        assert await fetcher.fetch_raw(BASE + "/missing.xml") is None
        assert await fetcher.fetch_raw(BASE + "/down.xml") is None


def test_decode_prefers_header_then_meta_then_utf8():
    latin = "café".encode("latin-1")
    assert decode_body(latin, "text/html; charset=iso-8859-1") == "café"
    assert decode_body(b'<meta charset="iso-8859-1">' + latin, "text/html").endswith("café")
    assert decode_body("café".encode(), "text/html") == "café"
    assert "�" in decode_body(latin, "text/html")  # invalid utf-8 is replaced, not raised
    assert decode_body(b"abc", "text/html; charset=nonsense") == "abc"
