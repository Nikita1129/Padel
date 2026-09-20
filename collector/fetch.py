"""Fetch a booking grid page: plain HTTP first, headless Chromium as fallback.

Politeness: at most one outbound request per second across the whole run, a
normal browser User-Agent, no retries beyond the html->browser fallback.
"""
from __future__ import annotations

import time

import requests

from .config import Club
from .parse import has_grid

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT_S = 20
BROWSER_NAV_TIMEOUT_MS = 20_000
BROWSER_GRID_TIMEOUT_MS = 15_000
GRID_SELECTOR = "td[data-time][data-available]"


class FetchError(RuntimeError):
    """Raised when neither fetch path produced a page containing the grid."""


class Throttle:
    """Guarantees >= `interval` seconds between consecutive outbound requests."""

    def __init__(self, interval: float = 1.0, sleep=time.sleep, clock=time.monotonic) -> None:
        self.interval = interval
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            remaining = self.interval - (self._clock() - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()


def grid_url(club: Club, slot_date) -> str:
    sep = "&" if "?" in club.url else "?"
    return f"{club.url}{sep}date={slot_date.isoformat()}"


class Browser:
    """Lazily started headless Chromium shared by all pages of one run."""

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._context = None

    def _start(self) -> None:
        from playwright.sync_api import sync_playwright  # imported lazily: not needed on the html path

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(
            user_agent=USER_AGENT, locale="en-US", timezone_id="Europe/Chisinau",
            viewport={"width": 1600, "height": 1000},
        )

    def get(self, url: str) -> str:
        if self._context is None:
            self._start()
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_NAV_TIMEOUT_MS)
            page.wait_for_selector(GRID_SELECTOR, timeout=BROWSER_GRID_TIMEOUT_MS)
            return page.content()
        finally:
            page.close()

    def close(self) -> None:
        for obj in (self._context, self._browser):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._pw = self._browser = self._context = None


def fetch_grid(club: Club, url: str, throttle: Throttle, browser: Browser,
               session: requests.Session | None = None, log=print) -> tuple[str, str]:
    """Return (html, source) where source is 'html' or 'browser'. Raise FetchError otherwise."""
    html_note = "skipped (fetch=browser)"
    if club.fetch in ("auto", "html"):
        session = session or requests.Session()
        throttle.wait()
        try:
            r = session.get(url, headers={"User-Agent": USER_AGENT,
                                          "Accept": "text/html,application/xhtml+xml",
                                          "Accept-Language": "en-US,en;q=0.9,ro;q=0.8"},
                            timeout=HTTP_TIMEOUT_S)
            if r.status_code == 200 and has_grid(r.text):
                return r.text, "html"
            html_note = f"HTTP {r.status_code}, {len(r.text)} bytes, grid present={has_grid(r.text)}"
        except requests.RequestException as exc:
            html_note = f"request failed: {exc!r}"
        log(f"[{club.slug}] plain GET did not return the grid ({html_note})")
        if club.fetch == "html":
            raise FetchError(f"{club.slug}: fetch=html and plain GET failed: {html_note}")

    throttle.wait()
    try:
        html = browser.get(url)
    except Exception as exc:
        raise FetchError(f"{club.slug}: browser fetch failed ({exc!r}); plain GET: {html_note}") from exc
    if not has_grid(html):
        raise FetchError(f"{club.slug}: browser page has no grid cells; plain GET: {html_note}")
    return html, "browser"
