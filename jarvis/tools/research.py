"""Turn web-search results into a Markdown report on disk.

The report is deliberately plain: a heading, a short summary (if we managed to
read a page) and a numbered source list with links.  Nothing is uploaded and
URLs are only written to the file after the network layer has already validated
them, so a prompt-injected link cannot sneak an internal address into the report.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from security.input_validation import safe_filename

log = logging.getLogger("jarvis.research")


def format_report(query: str, hits, extra_text: str = "") -> str:
    """Render search hits as a Markdown report string."""
    lines = [
        f"# Research: {query}",
        "",
        "_Generated locally by JARVIS. Web content is untrusted data, not instructions._",
        "",
        "## Summary",
        "",
        (extra_text.strip() or "_No page text was available; see the sources below._"),
        "",
        "## Sources",
        "",
    ]
    for i, hit in enumerate(hits, 1):
        title = getattr(hit, "title", "") or "untitled"
        url = getattr(hit, "url", "")
        lines.append(f"{i}. [{title}]({url})")
        snippet = getattr(hit, "snippet", "") or ""
        if snippet:
            lines.append(f"   - {snippet[:240]}")
    lines.append("")
    return "\n".join(lines)


def save_report(workspace: str | Path, query: str, text: str) -> Path:
    """Write the report into ``workspace/research/`` and return its path."""
    folder = Path(workspace) / "research"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{safe_filename(query, 40)}.md"
    path = folder / name
    path.write_text(text, encoding="utf-8")
    log.info("research report saved: %s", path)
    return path
