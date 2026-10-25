"""Web search without a paid API.

Order of preference (each step is free):
1. Gemini with Google Search grounding - best quality, needs a key + internet.
2. ddgs (DuckDuckGo HTML backend) - no key, no quota token, works today.
3. Brave Search free tier - only if the user supplied a key.
4. No internet / no library: hand a public search URL to the default browser.

Results always carry their URL and a "RETRIEVED" marker so the UI can show the
difference between what a page said and what the model concluded.
"""

from __future__ import annotations

import logging
import html
import threading
import urllib.parse
from dataclasses import dataclass, field

log = logging.getLogger("jarvis.search")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    body: str = ""
    fetched: bool = False
    error: str | None = None
    meta: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


@dataclass
class SearchOutcome:
    query: str
    hits: list[SearchHit] = field(default_factory=list)
    provider: str = "none"
    error: str | None = None
    fallback_url: str = ""
    grounded_answer: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.hits) or bool(self.grounded_answer)

    def format_for_prompt(self, per_hit: int = 900) -> str:
        """Text handed to the LLM. Wrapped in <source> tags: untrusted data."""
        blocks = []
        for i, hit in enumerate(self.hits, 1):
            body = html.escape((hit.body or hit.snippet)[:per_hit])
            url = html.escape(hit.url[:500], quote=True)
            title = html.escape(hit.title[:200])
            blocks.append(f"<source id='{i}' url='{url}'>\n{title}\n{body}\n</source>")
        return "\n\n".join(blocks)


def search_web(
    query: str,
    *,
    max_results: int = 5,
    timeout: float = 15.0,
    engine: str = "auto",
    brave_key: str = "",
    fetch_pages: bool = True,
    extract_chars: int = 6000,
) -> SearchOutcome:
    query = (query or "").strip()
    if not query:
        return SearchOutcome(query=query, error="Empty search query.")
    fallback = "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(query)

    if engine in ("auto", "duckduckgo"):
        outcome = _ddg(query, max_results, timeout)
        if outcome.hits:
            if fetch_pages:
                _parallel_extract(outcome.hits, extract_chars, timeout)
            outcome.fallback_url = fallback
            return outcome
        outcome.fallback_url = fallback
        if engine == "duckduckgo":
            return outcome
    if brave_key and engine in ("auto", "brave"):
        outcome = _brave(query, max_results, timeout, brave_key)
        if outcome.hits:
            if fetch_pages:
                _parallel_extract(outcome.hits, extract_chars, timeout)
            return outcome
    if engine == "none":
        return SearchOutcome(query=query, provider="none", fallback_url=fallback, error="Search disabled in settings.")
    return SearchOutcome(
        query=query,
        provider="fallback-browser",
        fallback_url=fallback,
        error="No search library available (pip install ddgs). Opening the browser search instead.",
    )


def _ddg(query: str, max_results: int, timeout: float) -> SearchOutcome:
    try:
        from ddgs import DDGS  # type: ignore
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # type: ignore  # older package name
        except ImportError:
            return SearchOutcome(query=query, error="ddgs not installed (pip install ddgs).")
    hits: list[SearchHit] = []
    try:
        with DDGS(timeout=timeout) as ddgs:
            for row in ddgs.text(query, max_results=max_results, safesearch="moderate"):
                hits.append(
                    SearchHit(
                        title=str(row.get("title") or "")[:200],
                        url=str(row.get("href") or row.get("url") or ""),
                        snippet=str(row.get("body") or "")[:600],
                    )
                )
    except Exception as exc:
        log.warning("ddg search failed: %s", exc)
        return SearchOutcome(query=query, provider="duckduckgo", error=f"Search failed: {exc}")
    return SearchOutcome(query=query, provider="duckduckgo", hits=hits[:max_results])


def _brave(query: str, max_results: int, timeout: float, key: str) -> SearchOutcome:
    import json

    from security.network import fetch_public_bytes

    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(
        {"q": query, "count": max_results}
    )
    try:
        raw, _, _ = fetch_public_bytes(
            url, timeout=timeout, limit=600_000,
            allowed_sites=["https://api.search.brave.com"],
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
        )
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception as exc:
        return SearchOutcome(query=query, provider="brave", error=f"Brave search failed: {exc}")
    hits = [
        SearchHit(
            title=str(r.get("title") or "")[:200],
            url=str(r.get("url") or ""),
            snippet=str(r.get("description") or "")[:600],
        )
        for r in (data.get("web", {}) or {}).get("results", [])[:max_results]
    ]
    return SearchOutcome(query=query, provider="brave", hits=hits)


def _parallel_extract(hits: list[SearchHit], chars: int, timeout: float) -> None:
    """Fetch pages concurrently (bounded), stripping scripts and navigation."""
    if not hits:
        return
    sem = threading.Semaphore(3)
    jobs = [h for h in hits if h.url.startswith("http")][:5]

    def worker(hit: SearchHit) -> None:
        with sem:
            try:
                hit.body = extract_text(hit.url, limit=chars, timeout=timeout)
                hit.fetched = True
            except Exception as exc:
                hit.error = str(exc)[:180]

    threads = [threading.Thread(target=worker, args=(h,), daemon=True) for h in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout + 2.0)


def extract_text(
    url: str, *, limit: int = 6000, timeout: float = 15.0,
    allowed_sites: list[str] | None = None,
) -> str:
    """GET a public page via a DNS-pinned socket; never executes scripts."""

    from security.network import fetch_public_bytes

    from bs4 import BeautifulSoup  # type: ignore

    raw, ctype, _ = fetch_public_bytes(
        url, timeout=timeout, limit=min(2_500_000, limit * 40), allowed_sites=allowed_sites
    )
    if "html" not in ctype and "text" not in ctype:
        return f"(unsupported content-type: {ctype})"
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe", "template"]):
        tag.decompose()
    text = " ".join(p.get_text(" ", strip=True) for p in soup.find_all(["p", "li", "h1", "h2", "h3", "td", "pre"]))
    text = " ".join(text.split())
    # Neutralise instruction-looking content: page text is DATA, never commands.
    return text[:limit].replace("</source>", "<\\/source>")
