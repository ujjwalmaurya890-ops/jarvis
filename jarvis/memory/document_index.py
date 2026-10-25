"""Local document index (optional, off by default).

Deliberately simple: chunk text -> store in SQLite -> rank by term overlap.
Why not a vector DB? On an 8 GB laptop an embedding model competes with the LLM
for RAM, and BM25-style ranking is plenty for a personal notes folder. This
module adds a light cosine-over-term-frequencies on top of SQLite so it stays
dependency-free.

It NEVER walks the whole disk: only the folders you pass in, only text-like
files, with a byte budget.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("jarvis.index")

TEXT_EXT = {".txt", ".md", ".py", ".c", ".h", ".cpp", ".java", ".js", ".ts", ".json", ".csv", ".html", ".css", ".tex", ".log", ".ini", ".yaml", ".yml"}
STOP = set(
    """the a an and or of to in is are was were be been it this that for on with as at by from
    i you he she they we my your our not but if then so can will just about""".split()
)
WORD = re.compile(r"[A-Za-z0-9\u0900-\u097F]{3,}")


@dataclass
class DocChunk:
    path: str
    start: int
    text: str
    terms: Counter
    norm: float


class DocumentIndex:
    def __init__(self, db, chunk_words: int = 180, max_file_bytes: int = 300_000) -> None:
        self.db = db
        self.chunk_words = chunk_words
        self.max_file_bytes = max_file_bytes
        self._lock = threading.Lock()
        self.last_scan: dict[str, float] = {}
        self.progress = 0.0
        self.scanning = False

    # ---- ingestion ------------------------------------------------------------
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
                    if not path.is_file() or path.suffix.lower() not in TEXT_EXT:
                        continue
                    if path.stat().st_size > self.max_file_bytes or path.stat().st_size < 20:
                        continue
                    if any(part.startswith(".") for part in path.parts):
                        continue
                    found.append(path)
                except OSError:
                    continue
        return found

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
                    mtime = path.stat().st_mtime
                    if not force and str(path) in already_indexed and self.last_scan.get(str(path)) == mtime:
                        stats["skipped"] += 1
                        continue
                    text = path.read_text(encoding="utf-8", errors="ignore")
                    chunks = self._chunk(text)
                    if chunks:
                        stats["chunks"] += self.db.index_document(str(path), chunks)
                        self.last_scan[str(path)] = mtime
                        stats["files"] += 1
                except (OSError, UnicodeError):
                    stats["skipped"] += 1
            self.db.set_meta("index_updated", time.time())
        finally:
            with self._lock:
                self.scanning = False
        log.info("index done: %s", stats)
        return stats

    def _chunk(self, text: str) -> list[str]:
        words = text.split()
        if not words:
            return []
        out = []
        step = int(self.chunk_words * 0.85) or 1
        for start in range(0, len(words), step):
            piece = " ".join(words[start : start + self.chunk_words]).strip()
            if len(piece) > 80:
                out.append(piece)
            if len(out) >= 400:
                break
        return out

    # ---- retrieval -------------------------------------------------------------
    def search(self, query: str, k: int = 5) -> list[dict[str, object]]:
        rows = self.db.query_docs(query, limit=k * 6)
        if not rows:
            return []
        q_terms = Counter(WORD.findall((query or "").lower()) - STOP)  # type: ignore[operator]
        if not q_terms:
            return []
        q_norm = math.sqrt(sum(v * v for v in q_terms.values()))
        scored: list[tuple[float, dict[str, object]]] = []
        for row in rows:
            d_terms = Counter(WORD.findall(row["text"].lower()) - STOP)  # type: ignore[operator]
            d_norm = math.sqrt(sum(v * v for v in d_terms.values())) or 1.0
            overlap = sum(min(d_terms[t], q_terms[t]) for t in q_terms)
            score = overlap / (q_norm * d_norm) if overlap else 0.0
            score += 0.001 * min(row["words"], 200) / 200  # tiny length prior
            scored.append(
                (
                    score,
                    {
                        "path": row["path"],
                        "chunk": row["chunk"],
                        "score": round(score, 5),
                        "text": row["text"][:700],
                    },
                )
            )
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:k]]

    def status(self) -> dict[str, object]:
        info = self.db.indexed_paths()
        return {
            "documents": len(info),
            "chunks": sum(int(r["chunks"]) for r in info) if info else 0,
            "scanning": self.scanning,
            "progress": round(self.progress, 2),
            "updated": self.db.get_meta("index_updated"),
        }
