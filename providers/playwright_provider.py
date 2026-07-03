"""
Shared Playwright plumbing for scraping-based providers.

Both BookMyShow and District need the same thing: launch a headless browser,
open a page with realistic headers, grab the rendered HTML (and/or intercept
XHR/fetch responses), and clean up afterwards. That shared lifecycle lives
here so providers/bookmyshow.py and providers/district.py only contain
site-specific parsing logic, per the "no duplicated code" requirement.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from providers.base_provider import BaseProvider, ProviderError

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class PlaywrightProvider(BaseProvider):
    """
    Base class that manages a single shared Browser/Context across all calls
    made during one checker.py run, so we don't pay browser-launch cost per
    subscription. Call `await provider.close()` when done (checker.py and
    conversation.py both do this in a `finally` block).
    """

    def __init__(self, cookies: str = "", headless: bool = True, navigation_timeout_ms: int = 30000) -> None:
        self._cookies_raw = cookies
        self._headless = headless
        self._navigation_timeout_ms = navigation_timeout_ms
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    async def _ensure_browser(self) -> BrowserContext:
        if self._context is not None:
            return self._context
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._headless)
        self._context = await self._browser.new_context(
            user_agent=DEFAULT_USER_AGENT,
            locale="en-IN",
            viewport={"width": 1366, "height": 900},
        )
        self._context.set_default_navigation_timeout(self._navigation_timeout_ms)
        if self._cookies_raw:
            await self._apply_cookies(self._cookies_raw)
        return self._context

    async def _apply_cookies(self, cookie_header: str) -> None:
        """
        Optional: some providers gate content behind a city/location cookie
        rather than a login. Accepts a "key=value; key2=value2" string via
        the relevant GitHub Secret (e.g. BOOKMYSHOW_COOKIES) and applies it
        to the browser context. Safe no-op if empty.
        """
        if not self._context:
            return
        pairs = [p.strip() for p in cookie_header.split(";") if "=" in p]
        cookies = []
        for pair in pairs:
            key, _, value = pair.partition("=")
            cookies.append(
                {
                    "name": key.strip(),
                    "value": value.strip(),
                    "domain": self.cookie_domain,
                    "path": "/",
                }
            )
        if cookies:
            await self._context.add_cookies(cookies)

    @property
    def cookie_domain(self) -> str:
        """Override in subclasses (e.g. '.bookmyshow.com')."""
        raise NotImplementedError

    async def fetch_html(self, url: str, wait_selector: Optional[str] = None) -> str:
        """Navigate to a URL and return the fully-rendered HTML."""
        context = await self._ensure_browser()
        page: Page = await context.new_page()
        try:
            response = await page.goto(url, wait_until="domcontentloaded")
            if response is not None and response.status >= 400:
                raise ProviderError(f"{self.name}: {url} returned HTTP {response.status}")
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=self._navigation_timeout_ms)
                except Exception:
                    # Selector not found -- page structure may have changed, or
                    # there's genuinely nothing to show. Caller decides what to do.
                    logger.debug("wait_selector %r not found on %s", wait_selector, url)
            html = await page.content()
            return html
        finally:
            await page.close()

    async def fetch_json_via_network(
        self, url: str, api_url_substring: str, wait_selector: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Navigate to `url` and capture the first XHR/fetch response whose URL
        contains `api_url_substring`, returning its parsed JSON body.

        This is the "reverse engineer network requests" technique: rather
        than guessing internal endpoint URLs and payload shapes ahead of
        time, we let the real front-end make its real calls and read the
        response. It's more resilient to internal API changes than hardcoding
        endpoint paths, at the cost of a slightly heavier page load.
        """
        context = await self._ensure_browser()
        page: Page = await context.new_page()
        captured: Dict[str, Any] = {}

        async def _on_response(response) -> None:
            if api_url_substring in response.url and response.ok:
                try:
                    captured["data"] = await response.json()
                    captured["url"] = response.url
                except Exception:  # noqa: BLE001
                    pass

        page.on("response", _on_response)
        try:
            await page.goto(url, wait_until="domcontentloaded")
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=self._navigation_timeout_ms)
                except Exception:
                    pass
            # Give in-flight XHRs a moment to resolve after DOM is ready.
            await page.wait_for_timeout(1500)
            return captured.get("data")
        finally:
            await page.close()

    async def close(self) -> None:
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
