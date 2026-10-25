"""Read documents into plain text, keeping page boundaries.

Two things this module guarantees:

* **PDFs work.**  A PDF is opened with ``pypdf`` (pure Python, free) and each
  page becomes one entry in ``DocumentText.pages``.  Text files become a single
  "page" (page 1) so the same chunking code handles both.
* **Nothing is uploaded.**  Text is read straight off the local disk into
  memory; there is no network call here and no path is ever sent anywhere.

The result is deliberately small: page text only, no images, no metadata beyond
what pypdf gives us for free.  A page that cannot be decoded becomes an empty
string rather than raising, so one damaged page never aborts an import.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("jarvis.loader")

# Same set as the old text index, plus PDF.  Keeping it here (not in the index)
# means the loader owns the question "can I read this file?".
TEXT_EXT = {
    ".txt", ".md", ".py", ".c", ".h", ".cpp", ".java", ".js", ".ts", ".json",
    ".csv", ".html", ".css", ".tex", ".log", ".ini", ".yaml", ".yml",
}


class DocumentLoadError(Exception):
    """Raised when a file cannot be turned into page text."""


@dataclass
class DocumentText:
    """A document split into per-page text."""

    path: str
    kind: str                     # "pdf" | "text"
    pages: list[str] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def text(self) -> str:
        return "\n".join(self.pages)

    def page_text(self, number: int) -> str:
        """1-based page lookup; returns '' for a missing page."""
        if 1 <= number <= len(self.pages):
            return self.pages[number - 1]
        return ""


def load_document(path: str | Path, max_file_bytes: int = 300_000) -> DocumentText:
    """Return a :class:`DocumentText` for ``path`` or raise ``DocumentLoadError``."""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise DocumentLoadError(f"Cannot read '{p.name}': {exc}") from exc
    if size == 0:
        raise DocumentLoadError(f"'{p.name}' is empty.")
    if size > max_file_bytes:
        raise DocumentLoadError(
            f"'{p.name}' is {size / 1e6:.1f} MB; the limit is {max_file_bytes / 1e6:.0f} MB."
        )
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        return _load_pdf(p)
    if suffix in TEXT_EXT:
        text = p.read_text(encoding="utf-8", errors="ignore")
        return DocumentText(str(p), "text", [text])
    raise DocumentLoadError(
        f"I can't read '{p.name}' (type {suffix or 'unknown'}). "
        "Supported: PDF, TXT, MD and code/text files."
    )


def _load_pdf(path: Path) -> DocumentText:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - depends on install
        raise DocumentLoadError(
            "PDF support needs the free 'pypdf' package. Run: pip install pypdf"
        ) from exc
    try:
        reader = PdfReader(str(path))
        pages: list[str] = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:  # one damaged page must not abort the whole file
                log.debug("page extraction failed in %s", path.name, exc_info=True)
                pages.append("")
        if not any(t.strip() for t in pages):
            raise DocumentLoadError(
                f"'{path.name}' has no readable text (it may be a scanned image PDF)."
            )
        return DocumentText(str(path), "pdf", pages)
    except DocumentLoadError:
        raise
    except Exception as exc:
        raise DocumentLoadError(f"Could not read PDF '{path.name}': {exc}") from exc
