"""Serves tests/fixtures/site/* through respx as https://help.example.com (no network)."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

SITE_DIR = Path(__file__).parent / "site"
BASE = "https://help.example.com"

# URL path -> file under SITE_DIR
PAGES = {
    "/": "index.html",
    "/help/returns": "help/returns.html",
    "/help/shipping": "help/shipping.html",
    "/jobs": "jobs.html",
    "/app": "app.html",
    "/private/area": "private/area.html",
}


def read(name: str) -> str:
    """Contents of a fixture file."""
    return (SITE_DIR / name).read_text(encoding="utf-8")


def mount_site(router: respx.MockRouter, base: str = BASE) -> dict[str, respx.Route]:
    """Register every fixture page; returns routes by path so tests can assert call counts.

    Paths the crawler must never request (``/login``, the PDF, the external partner site) get
    routes too, so a request to them is recorded rather than failing with a transport error.
    """
    routes: dict[str, respx.Route] = {}
    for path, filename in PAGES.items():
        routes[path] = router.get(f"{base}{path}").mock(
            return_value=httpx.Response(
                200, text=read(filename), headers={"content-type": "text/html; charset=utf-8"}
            )
        )
    routes["/robots.txt"] = router.get(f"{base}/robots.txt").mock(
        return_value=httpx.Response(
            200, text=read("robots.txt"), headers={"content-type": "text/plain"}
        )
    )
    routes["/login"] = router.get(f"{base}/login").mock(
        return_value=httpx.Response(200, text="<html><body>login</body></html>")
    )
    routes["/files/size-guide.pdf"] = router.get(f"{base}/files/size-guide.pdf").mock(
        return_value=httpx.Response(
            200, content=b"%PDF", headers={"content-type": "application/pdf"}
        )
    )
    routes["other"] = router.route(host="other.example.org").mock(
        return_value=httpx.Response(200, text="<html><body>partners</body></html>")
    )
    return routes
