import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional

# Sites JARVIS can actually type a query into and submit — selectors are
# best-effort (real site markup drifts) with a direct search-URL fallback so
# a selector miss degrades to "still searched it" rather than a hard failure.
SEARCHABLE_SITES: Dict[str, dict] = {
    "youtube": {
        "name": "YouTube",
        "url": "https://www.youtube.com",
        "search_selector": "input#search",
        "search_url": "https://www.youtube.com/results?search_query={query}",
    },
    "google": {
        "name": "Google",
        "url": "https://www.google.com",
        "search_selector": "textarea[name=q], input[name=q]",
        "search_url": "https://www.google.com/search?q={query}",
    },
}

# Homepage-only: no typing/clicking on JARVIS's behalf beyond opening the
# site. Not just a technical limit — automating further into these
# platforms' own UIs likely runs against their Terms of Service, especially
# for DRM-gated streaming playback.
HOMEPAGE_ONLY_SITES: Dict[str, dict] = {
    "netflix": {"name": "Netflix", "url": "https://www.netflix.com"},
    "peacock": {"name": "Peacock", "url": "https://www.peacocktv.com"},
    "hulu": {"name": "Hulu", "url": "https://www.hulu.com"},
    "disneyplus": {"name": "Disney+", "url": "https://www.disneyplus.com"},
    "gmail": {"name": "Gmail", "url": "https://mail.google.com"},
    "calendar": {"name": "Google Calendar", "url": "https://calendar.google.com"},
}

ALL_SITES: Dict[str, dict] = {**SEARCHABLE_SITES, **HOMEPAGE_ONLY_SITES}


class BrowserAutomationError(Exception):
    pass


class BrowserAutomationService:
    """Owns one persistent, visible, JARVIS-controlled Chrome instance —
    separate from the user's own browser — plus a registry of named open
    tabs so a later "close youtube" can find the right one.

    Playwright's sync API requires every call to happen on the same thread
    that started it; FastAPI's request handlers are async and may run on
    different threads, so all real work here is dispatched onto one
    dedicated single-worker thread pool rather than called directly.
    """

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser-automation")
        self._playwright = None
        self._browser = None
        self._pages: Dict[str, "object"] = {}

    def _ensure_browser(self):
        if self._browser is None or not self._browser.is_connected():
            from playwright.sync_api import sync_playwright

            if self._playwright is None:
                self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=False, args=["--start-maximized"])
        return self._browser

    def _open_site_blocking(self, site_key: str, query: str) -> str:
        site = ALL_SITES.get(site_key)
        if site is None:
            raise BrowserAutomationError(f"{site_key or '(no site)'} isn't a site I can open")

        browser = self._ensure_browser()
        page = self._pages.get(site_key)
        if page is None or page.is_closed():
            context = browser.contexts[0] if browser.contexts else browser.new_context(no_viewport=True)
            page = context.new_page()
            self._pages[site_key] = page

        page.goto(site["url"], wait_until="domcontentloaded", timeout=20000)
        page.bring_to_front()

        if query and site_key in SEARCHABLE_SITES:
            try:
                page.click(site["search_selector"], timeout=5000)
                page.fill(site["search_selector"], query)
                page.press(site["search_selector"], "Enter")
            except Exception:
                # Selector drift, slow load, consent dialog, etc. — a direct
                # search URL still gets the user to real results.
                page.goto(site["search_url"].format(query=query), wait_until="domcontentloaded", timeout=20000)
            return f'Searched {site["name"]} for "{query}".'

        if site_key in SEARCHABLE_SITES:
            return f'Opened {site["name"]}. What would you like to search for?'
        return f'Opened {site["name"]}.'

    def _close_site_blocking(self, site_key: str) -> str:
        site = ALL_SITES.get(site_key)
        name = site["name"] if site else (site_key or "(no site)")
        page = self._pages.pop(site_key, None)
        if page is None or page.is_closed():
            return f"{name} isn't currently open."
        page.close()
        return f"Closed {name}."

    def _close_all_blocking(self) -> None:
        for page in list(self._pages.values()):
            try:
                if not page.is_closed():
                    page.close()
            except Exception:
                pass
        self._pages.clear()
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    async def open_site(self, site_key: str, query: str = "") -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._open_site_blocking, site_key, query)

    async def close_site(self, site_key: str) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._close_site_blocking, site_key)

    async def close_all(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._close_all_blocking)


_service: Optional[BrowserAutomationService] = None
_service_lock = threading.Lock()


def get_browser_automation_service() -> BrowserAutomationService:
    global _service
    with _service_lock:
        if _service is None:
            _service = BrowserAutomationService()
        return _service
