"""Browser automation with Playwright (optional, lazy-loaded).

Playwright is only started when a page truly needs JavaScript.  We keep one
browser instance alive, reuse a single context, and always close it on exit.
Everything is treated as hostile input: no downloads are opened, no JS from the
page is ever evaluated by JARVIS, and the extracted text is truncated and
tag-stripped before it reaches the model.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

from security.network import NetworkPolicyError, validate_public_url

log = logging.getLogger("jarvis.browser")

MAX_TEXT = 12000
DENIED_SUFFIXES = (".exe", ".bat", ".cmd", ".ps1", ".msi", ".js", ".jar", ".vbs", ".scr")


@dataclass
class PageResult:
    url: str
    title: str = ""
    text: str = ""
    ok: bool = True
    error: str | None = None
    engine: str = "playwright"

    @property
    def summary(self) -> str:
        return self.text[:400]


class BrowserManager:
    """Ephemeral Chromium wrapper; each read starts/closes on ONE worker thread."""

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self._pw = None
        self._browser = None
        self._context = None
        self._lock = threading.RLock()
        self.available: bool | None = None
        self.error: str | None = None

    # ---- lifecycle ----------------------------------------------------------
    def start(self) -> bool:
        with self._lock:
            if self._browser is not None:
                return True
            try:
                from playwright.sync_api import sync_playwright  # type: ignore
            except Exception as exc:
                self.error = (
                    "Playwright is not installed. Run: pip install playwright "
                    "&& playwright install chromium"
                )
                self.available = False
                log.info(self.error, exc_info=exc)
                return False
            try:
                self._pw = sync_playwright().start()
                self._browser = self._pw.chromium.launch(
                    headless=self.headless,
                    args=["--disable-dev-shm-usage", "--no-first-run", "--disable-extensions"],
                )
                self._context = self._browser.new_context(
                    accept_downloads=False,
                    viewport={"width": 1366, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                    ),
                )
                self._context.set_default_timeout(20000)
                self.available = True
                self.error = None
                return True
            except Exception as exc:
                self.error = f"Could not launch Chromium: {exc}. Try: playwright install chromium"
                self.available = False
                self.close()
                return False

    def close(self) -> None:
        with self._lock:
            for obj in (self._context, self._browser, self._pw):
                try:
                    if obj is not None and hasattr(obj, "close"):
                        obj.close()
                    elif obj is not None and hasattr(obj, "stop"):
                        obj.stop()
                except Exception:
                    pass
            self._context = self._browser = self._pw = None

    @property
    def running(self) -> bool:
        return self._browser is not None

    # ---- actions --------------------------------------------------------------
    def read_page(self, url: str, wait_ms: int = 1200, allowed_sites: list[str] | None = None) -> PageResult:
        try:
            validate_public_url(url, allowed_sites)
            if urlsplit(url).path.lower().endswith(DENIED_SUFFIXES):
                return PageResult(url=url, ok=False, error="I will not download that file type.")
        except (NetworkPolicyError, ValueError) as exc:
            return PageResult(url=url, ok=False, error=f"Blocked URL: {exc}")
        with self._lock:
            return self._read_checked(url, wait_ms, allowed_sites)

    def _read_checked(self, url: str, wait_ms: int, allowed_sites: list[str] | None) -> PageResult:
        if not self.start():
            return self._static_fallback(url, allowed_sites)
        try:
            page = self._context.new_page()  # type: ignore[union-attr]
            try:
                def guard(route) -> None:  # noqa: ANN001
                    try:
                        request = route.request
                        validate_public_url(
                            request.url,
                            allowed_sites if request.is_navigation_request() else None,
                        )
                    except (NetworkPolicyError, ValueError):
                        route.abort()
                        return
                    route.continue_()

                page.route("**/*", guard)  # re-check navigations, redirects and subresources
                resp = page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(wait_ms)
                validate_public_url(page.url, allowed_sites)
                title = page.title() or ""
                text = page.evaluate(
                    """() => {
                        const kill = ['script','style','noscript','svg','nav','footer','iframe'];
                        kill.forEach(t => document.querySelectorAll(t).forEach(e => e.remove()));
                        return (document.body ? document.body.innerText : '').slice(0, %d);
                    }""" % MAX_TEXT
                )
            finally:
                page.close()
            status = getattr(resp, "status", 200) if resp is not None else 200
            if status in (403, 429):
                return PageResult(
                    url=url,
                    ok=False,
                    error=f"The site blocked the request (HTTP {status}). Try opening it yourself.",
                )
            if status == 404:
                return PageResult(url=url, ok=False, error="Page not found (404).")
            return PageResult(url=url, title=title, text=" ".join(str(text).split())[:MAX_TEXT])
        except Exception as exc:
            msg = str(exc)
            if "Timeout" in msg or "timeout" in msg:
                return PageResult(url=url, ok=False, error="The page took too long to load.")
            return PageResult(url=url, ok=False, error=f"Browser error: {msg[:180]}")
        finally:
            self.close()  # Playwright's sync objects must close on their creator thread

    def search_and_read(self, query: str, max_pages: int = 2) -> list[PageResult]:
        from tools.web_search import search_web

        outcome = search_web(query, max_results=max_pages + 1, fetch_pages=False)
        results: list[PageResult] = []
        for hit in outcome.hits[:max_pages]:
            results.append(self.read_page(hit.url))
        return results

    def _static_fallback(self, url: str, allowed_sites: list[str] | None) -> PageResult:
        from tools.web_search import extract_text

        try:
            return PageResult(
                url=url, text=extract_text(url, limit=MAX_TEXT, allowed_sites=allowed_sites), engine="pinned-http"
            )
        except Exception as exc:
            return PageResult(url=url, ok=False, error=f"Playwright unavailable and static fetch failed: {exc}", engine="none")

    # ---- stats -----------------------------------------------------------------
    def stats(self) -> dict[str, object]:
        return {"available": self.available, "running": self.running, "error": self.error, "headless": self.headless}
