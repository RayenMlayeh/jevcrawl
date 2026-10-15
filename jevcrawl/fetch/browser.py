"""Optional JavaScript rendering for pages that are empty without it.

Only used when the ``js`` extra is installed (``pip install "jevcrawl[js]"`` and then
``playwright install chromium``), and only for pages the HTML fetch flagged as needing
JavaScript. The browser starts on the first such page, so crawls that never meet one pay
nothing for it.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import importlib.util
from typing import Any, Protocol

from jevcrawl.fetch.http import USER_AGENT, FetchResult
from jevcrawl.log import get_logger

log = get_logger("fetch.browser")

LOAD_TIMEOUT_MS = 20_000
SETTLE_TIMEOUT_MS = 5_000  # extra wait for network quiet after "load"; skipped if it never comes


class Renderer(Protocol):
    """Renders one URL in a browser and returns the resulting HTML."""

    async def render(self, url: str) -> FetchResult: ...

    async def aclose(self) -> None: ...


def playwright_installed() -> bool:
    """True when the optional ``playwright`` package can be imported."""
    return importlib.util.find_spec("playwright") is not None


class Browser:
    """One headless Chromium shared by every render of a session."""

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._lock = asyncio.Lock()
        self._broken = False

    async def _start(self) -> Any:
        async with self._lock:
            if self._browser is None and not self._broken:
                try:
                    api = importlib.import_module("playwright.async_api")
                    self._playwright = await api.async_playwright().start()
                    self._browser = await self._playwright.chromium.launch()
                except Exception as exc:  # e.g. Chromium not downloaded yet
                    self._broken = True
                    log.warning(
                        "JavaScript rendering unavailable (%s). Run `playwright install chromium`.",
                        exc,
                    )
            return self._browser

    async def render(self, url: str) -> FetchResult:
        """The page's HTML after its scripts ran. Never raises."""
        browser = await self._start()
        if browser is None:
            return FetchResult(url, url, error="render_unavailable")
        page = await browser.new_page(user_agent=USER_AGENT)
        try:
            response = await page.goto(url, wait_until="load", timeout=LOAD_TIMEOUT_MS)
            # Chatty pages never go quiet; what has rendered by then is enough.
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
            status = response.status if response is not None else 200
            if status != 200:
                return FetchResult(url, page.url, status, error=f"http_{status}")
            return FetchResult(url, page.url, 200, await page.content(), "text/html")
        except Exception as exc:
            log.info("rendering %s failed: %s", url, type(exc).__name__)
            return FetchResult(url, url, error="render_failed")
        finally:
            await page.close()

    async def aclose(self) -> None:
        """Shut the browser down (if it was started)."""
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._browser = self._playwright = None
