"""Local document index (optional, off by default).

Deliberately simple: load text -> chunk per page -> store in SQLite -> rank by
term overlap.  Why not a vector DB?  On an 8 GB laptop an embedding model
competes with the LLM for RAM, and BM25-style ranking is plenty for a personal
notes folder.  This module adds a light cosine-over-term-frequencies on top of
SQLite so it stays dependency-free.

It NEVER walks the whole disk: only the folders you pass in, only readable
files, with a byte budget.  Every stored chunk remembers the page it came from
(PDFs give real page numbers; text files are page 1) so the answer can cite
"filename (page N)".
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import Counter
from pathlib import Path

from memory.document_loader import DocumentLoadError, DocumentText, load_document

log = logging.getLogger("jarvis.index")

# Text extensions we can read directly, plus PDF (handled by pypdf).
TEXT_EXT = {
    ".txt", ".md", ".py", ".c", ".h", ".cpp", ".java", ".js", ".ts", ".json",
    ".csv", ".html", ".css", ".tex", ".log", ".ini", ".yaml", ".yml",
}
INDEXABLE_EXT = TEXT_EXT | {".pdf"}
STOP = set(
    """the a an and or of to in is are was were be been it this that for on with as at by from
    i you he she they we my your our not but if then so can will just about""".split()
)
WORD = re.compile(r"[A-Za-z0-9\u0900-\u097F]{3,}")


class DocumentIndex:
    def __init__(self, db, chunk_words: int = 180, max_file_bytes: int = 300_000) -> None:
        self.db = db
        self.chunk_words = chunk_words
        self.max_file_bytes = max_file_bytes
        self._lock = threading.Lock()
        self.last_scan: dict[str, float] = {}
        self.progress = 0.0
        self.scanning = False

    # ---- ingestion -----------------------------------------------------------
    def iter_files(self, roots: list[str], limit: int = 400) -> list[Path]:
        found: list[Path] = []
        for raw in roots:
            root = Path(raw).expanduser().resolve()
            if not root.is_dir():
                log.warning("skipping missing folder %s", root)
                continue
            for path in root.rglob("*"):
                if len(found) >= limit:
                    return found
                try:
                    # Never follow a file symlink out of the selected root.
                    # Resolve again immediately before ingestion below as well.
                    if not path.resolve().is_relative_to(root):
                        continue
                    if not path.is_file() or path.suffix.lower() not in INDEXABLE_EXT:
                        continue
                    if path.stat().st_size > self.max_file_bytes or path.stat().st_size < 20:
                        continue
                    if any(part.startswith(".") for part in path.parts):
                        continue
                    found.append(path)
                except OSError:
                    continue
        return found

    def load(self, path: str | Path) -> DocumentText:
        """Read one file into page-aware text (PDF or plain text)."""
        return load_document(path, max_file_bytes=self.max_file_bytes)

    def chunk_document(self, doc: DocumentText) -> list[tuple[int | None, str]]:
        """Split a document into (page_number, chunk_text) pieces.

        Pages are chunked independently so a citation can point at the exact page
        a passage came from.  Long pages produce several chunks on the same page.
        """
        out: list[tuple[int | None, str]] = []
        step = max(1, int(self.chunk_words * 0.85))
        for page_no, page_text in enumerate(doc.pages, start=1):
            words = page_text.split()
            if not words:
                continue
            for start in range(0, len(words), step):
                piece = " ".join(words[start : start + self.chunk_words]).strip()
                if piece:
                    out.append((page_no, piece))
            if len(out) >= 400:
                break
        return out

    def index_file(self, path: str | Path) -> int:
        """Import one file. Returns the number of chunks stored (0 on skip)."""
        p = Path(path)
        doc = self.load(p)
        pieces = self.chunk_document(doc)
        if not pieces:
            return 0
        pages = [page for page, _ in pieces]
        self.db.index_document(str(p), [text for _, text in pieces], pages=pages)
        try:
            self.last_scan[str(p)] = p.stat().st_mtime
        except OSError:
            pass
        log.info("indexed %s (%d chunks, %d pages)", p.name, len(pieces), doc.page_count)
        return len(pieces)

    def index_roots(self, roots: list[str], *, force: bool = False) -> dict[str, int]:
        """Index changed files. Returns a small report dict."""
        with self._lock:
            if self.scanning:
                return {"skipped": 1, "reason": "a scan is already running"}
            self.scanning = True
            self.progress = 0.0
        stats = {"files": 0, "chunks": 0, "skipped": 0}
        try:
            files = self.iter_files(roots)
            already_indexed = {row["path"] for row in self.db.indexed_paths()}
            for i, path in enumerate(files, 1):
                self.progress = i / max(1, len(files))
                try:
                    # A link could have changed since the initial directory scan.
                    resolved = path.resolve()
                    if not any(resolved.is_relative_to(Path(root).expanduser().resolve()) for root in roots):
                        stats["skipped"] += 1
                        continue
                    mtime = path.stat().st_mtime
                    if not force and str(path) in already_indexed and self.last_scan.get(str(path)) == mtime:
                        stats["skipped"] += 1
                        continue
                    count = self.index_file(path)
                    if count:
                        stats["chunks"] += count
                        stats["files"] += 1
                    else:
                        stats["skipped"] += 1
                except (OSError, UnicodeError, DocumentLoadError):
                    stats["skipped"] += 1
            self.db.set_meta("index_updated", time.time())
        finally:
            with self._lock:
                self.scanning = False
        log.info("index done: %s", stats)
        return stats

    # ---- retrieval -------------------------------------------------------------
    def search(self, query: str, k: int = 5) -> list[dict[str, object]]:
        rows = self.db.query_docs(query, limit=k * 6)
        if not rows:
            return []
        q_terms = Counter(set(WORD.findall((query or "").lower())) - STOP)
        if not q_terms:
            return []
        q_norm = math.sqrt(sum(v * v for v in q_terms.values()))
        scored: list[tuple[float, dict[str, object]]] = []
        for row in rows:
            d_terms = Counter(set(WORD.findall(row["text"].lower())) - STOP)
            d_norm = math.sqrt(sum(v * v for v in d_terms.values())) or 1.0
            overlap = sum(min(d_terms[t], q_terms[t]) for t in q_terms)
            score = overlap / (q_norm * d_norm) if overlap else 0.0
            score += 0.001 * min(row["words"], 200) / 200  # tiny length prior
            scored.append(
                (
                    score,
                    {
                        "path": row["path"],
                        "page": row.get("page"),
                        "chunk": row["chunk"],
                        "score": round(score, 5),
                        "text": row["text"][:700],
                    },
                )
            )
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:k]]

    @staticmethod
    def citation(hit: dict[str, object]) -> str:
        """Human-readable 'filename (page N)' for a retrieved chunk."""
        name = Path(str(hit.get("path", "document"))).name
        page = hit.get("page")
        return f"{name} (page {page})" if page else name

    def citations(self, hits: list[dict[str, object]]) -> list[dict[str, str]]:
        return [{"file": Path(str(h.get("path", ""))).name, "path": str(h.get("path", "")),
                 "page": str(h.get("page") or "")} for h in hits]

    def status(self) -> dict[str, object]:
        info = self.db.indexed_paths()
        return {
            "documents": len(info),
            "chunks": sum(int(r["chunks"]) for r in info) if info else 0,
            "scanning": self.scanning,
            "progress": round(self.progress, 2),
            "updated": self.db.get_meta("index_updated"),
        }
