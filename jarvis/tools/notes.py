"""Notes: the only place JARVIS writes by default.

Files live in data/workspace/notes as markdown with a small front-matter block.
Nothing is ever deleted silently - "delete note" moves the file to .trash/.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("jarvis.notes")

SAFE_NAME = re.compile(r"[^a-z0-9\- ]")


def slugify(text: str, limit: int = 48) -> str:
    clean = SAFE_NAME.sub("", (text or "").lower()).strip()
    clean = re.sub(r"\s+", "-", clean) or "note"
    return clean[:limit].strip("-")


@dataclass
class Note:
    title: str
    body: str
    path: Path
    created: float
    tags: list[str]

    def as_dict(self) -> dict[str, object]:
        return {"title": self.title, "path": str(self.path), "created": self.created, "tags": self.tags}


class NotesStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.notes_dir = self.root / "notes"
        self.trash = self.root / ".trash"
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        self.trash.mkdir(parents=True, exist_ok=True)

    # ---- write ---------------------------------------------------------------
    def create(self, title: str, body: str, tags: list[str] | None = None) -> Note:
        title = (title or "Note").strip()[:120]
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = self.notes_dir / f"{stamp}-{slugify(title)}.md"
        tag_list = [t.strip().lower() for t in (tags or []) if t.strip()][:8]
        front = ["---", f"title: {title}", f"created: {time.strftime('%Y-%m-%d %H:%M')}", f"tags: {', '.join(tag_list)}", "---", ""]
        path.write_text("\n".join(front) + (body or "").strip() + "\n", encoding="utf-8")
        log.info("note created: %s", path.name)
        return Note(title=title, body=body or "", path=path, created=time.time(), tags=tag_list)

    def append(self, path: str | Path, text: str) -> Note:
        p = Path(path).resolve()
        if self.notes_dir not in p.parents:
            raise ValueError("Can only append inside the notes folder.")
        existing = p.read_text(encoding="utf-8") if p.exists() else "---\n---\n"
        stamp = time.strftime("%H:%M:%S")
        p.write_text(existing.rstrip() + f"\n\n- [{stamp}] {text.strip()}\n", encoding="utf-8")
        return self._parse(p)

    def latest(self, n: int = 10) -> list[Note]:
        files = sorted(self.notes_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        return [self._parse(f) for f in files[: max(1, n)]]

    def find(self, query: str) -> list[Note]:
        q = (query or "").lower().strip()
        if not q:
            return self.latest(10)
        out = []
        for f in sorted(self.notes_dir.glob("*.md"), reverse=True):
            note = self._parse(f)
            if q in note.title.lower() or q in note.body.lower() or any(q in t for t in note.tags):
                out.append(note)
        return out[:20]

    def delete(self, path: str | Path) -> Path:
        """Soft delete - moves to .trash with a timestamp suffix."""
        p = Path(path).resolve()
        if self.notes_dir not in p.parents or not p.exists():
            raise ValueError("No such note in the notes folder.")
        target = self.trash / f"{p.stem}.{int(time.time())}.md"
        p.rename(target)
        return target

    def word_count(self) -> int:
        return sum(len(f.read_text(encoding="utf-8", errors="ignore").split()) for f in self.notes_dir.glob("*.md"))

    def _parse(self, path: Path) -> Note:
        raw = path.read_text(encoding="utf-8", errors="replace")
        title, tags, body = path.stem, [], raw
        if raw.startswith("---"):
            head, _, body = raw.partition("\n---")
            for line in head.splitlines()[1:]:
                key, _, value = line.partition(":")
                key, value = key.strip().lower(), value.strip()
                if key == "title" and value:
                    title = value
                elif key == "tags":
                    tags = [t.strip() for t in value.split(",") if t.strip()]
        return Note(title=title, body=body.strip(), path=path, created=path.stat().st_mtime, tags=tags)
