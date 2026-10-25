"""Sandboxed file access.

Two roots matter:
* workspace (read+write)  - JARVIS's own folder for notes and generated files;
* allowed dirs (read-only) - folders the user explicitly whitelisted.

Everything here resolves paths first, compares against the roots second, and
caps read size third.  Binary files, reparse-point escapes and `..` tricks are
rejected before any read happens.
"""

from __future__ import annotations

import fnmatch
import itertools
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("jarvis.files")

TEXT_SUFFIXES = {".txt", ".md", ".py", ".json", ".csv", ".log", ".ini", ".toml", ".yaml", ".yml", ".c", ".h", ".js", ".ts", ".html"}
BLOCKED_PARTS = {"windows", "system32", "appdata\\roaming\\microsoft\\credentials", ".ssh", "passwords"}
SECRET_NAMES = {".env", ".env.local", "id_rsa", "id_ed25519", "credentials.json", "secrets.json"}


@dataclass
class FileHit:
    path: Path
    size: int
    modified: float

    @property
    def name(self) -> str:
        return self.path.name


class PathError(Exception):
    """Raised for any path that fails the sandbox check."""


class FileManager:
    def __init__(self, roots: list[str | Path], workspace: str | Path, max_bytes: int = 400_000) -> None:
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.roots = [Path(r).resolve() for r in roots]
        if self.workspace not in self.roots:
            self.roots.append(self.workspace)
        self.max_bytes = max_bytes
        # Soft-delete destination: files moved here can still be recovered.
        self.trash = self.workspace / ".trash"
        self.trash.mkdir(parents=True, exist_ok=True)

    # ---- checks ---------------------------------------------------------------
    def _resolve(self, raw: str | Path, *, for_write: bool = False) -> Path:
        text = str(raw or "").strip().strip('"')
        if not text:
            raise PathError("Empty path.")
        candidate = Path(text).expanduser()
        try:
            path = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise PathError(f"Could not resolve '{raw}': {exc}") from exc
        low = str(path).lower()
        if any(part in low for part in BLOCKED_PARTS) or path.name.lower() in SECRET_NAMES:
            raise PathError("That location is protected.")
        root = self._root_for(path)
        if root is None:
            raise PathError(
                f"'{path}' is outside the folders you allowed. Allowed: "
                + ", ".join(str(r) for r in self.roots)
            )
        if for_write:
            try:
                path.relative_to(self.workspace)
            except ValueError as exc:
                raise PathError("Writes are allowed inside the JARVIS workspace only.") from exc
            if path.exists() and path.is_dir():
                raise PathError("Refusing to overwrite a directory.")
            if path.parent != root and root not in path.parents:
                raise PathError("Write target escaped the allowed root.")
            path.parent.mkdir(parents=True, exist_ok=True)
        elif not path.is_file():
            raise PathError(f"No file at '{path}'.")
        return path

    def _root_for(self, path: Path) -> Path | None:
        for root in self.roots:
            try:
                path.relative_to(root)
                return root
            except ValueError:
                continue
        return None

    def can_read(self, raw: str | Path) -> bool:
        try:
            self._resolve(raw)
            return True
        except PathError:
            return False

    # ---- operations -------------------------------------------------------------
    def read_text(self, raw: str | Path, limit: int | None = None) -> tuple[str, Path]:
        path = self._resolve(raw)
        size = path.stat().st_size
        if size > self.max_bytes:
            raise PathError(f"File is {size/1024:.0f} KB - above the {self.max_bytes//1024} KB read cap.")
        if path.suffix.lower() not in TEXT_SUFFIXES:
            raise PathError(f"I only read text files (not {path.suffix or 'unknown'}).")
        cap = min(limit or self.max_bytes, self.max_bytes)
        text = path.read_text(encoding="utf-8", errors="replace")[:cap]
        return text, path

    def write_text(self, raw: str | Path, content: str, *, append: bool = False) -> Path:
        path = self._resolve(raw, for_write=True)
        if len(content) > 200_000:
            raise PathError("Too much text for one write; split it up.")
        mode = "a" if append else "w"
        with path.open(mode, encoding="utf-8") as handle:
            handle.write(content)
        log.info("wrote %s (%d chars)", path, len(content))
        return path

    def delete(self, raw: str | Path, *, permanent: bool = False) -> Path:
        """Delete a file. Default is a soft delete to ``.trash``.

        Directories are never deleted. The path must resolve inside an allowed
        read root; the permission broker still asks for confirmation before this
        runs because the capability is marked dangerous.
        """
        path = self._resolve(raw)
        if path.is_dir():
            raise PathError("Refusing to delete a directory.")
        if permanent:
            path.unlink()
            log.info("permanently deleted %s", path)
            return path
        target = self.trash / f"{path.stem}.{int(time.time())}{path.suffix}"
        shutil.move(str(path), str(target))
        log.info("moved %s to trash", path)
        return target

    def search(self, pattern: str, *, roots: list[str] | None = None, limit: int = 40, recursive: bool = True) -> list[FileHit]:
        """Filename glob search restricted to the allowed roots (never the disk)."""
        glob = pattern.strip().strip('"')
        if not glob:
            return []
        if "/" in glob or "\\" in glob or ".." in glob:
            raise PathError("File search accepts a filename pattern, not a folder path.")
        if not glob.startswith("*") and "." not in Path(glob).name:
            glob = f"*{glob}*"
        hits: list[FileHit] = []
        search_roots = [Path(r).resolve() for r in (roots or [])] or self.roots
        for root in search_roots:
            if not root.is_dir() or self._root_for(root) is None:
                continue
            walker = root.rglob(glob) if recursive else root.glob(glob)
            try:
                for p in itertools.islice(walker, 2000):
                    if len(hits) >= limit:
                        return hits
                    try:
                        if p.is_file() and self._root_for(p.resolve()) is not None and p.name.lower() not in SECRET_NAMES and all(part not in str(p).lower() for part in BLOCKED_PARTS):
                            st = p.stat()
                            hits.append(FileHit(path=p, size=st.st_size, modified=st.st_mtime))
                    except (OSError, PermissionError):
                        continue
            except (OSError, RecursionError):
                continue
        return hits

    def grep(self, text_pattern: str, roots: list[str] | None = None, limit: int = 25) -> list[dict[str, object]]:
        """Literal substring search inside allowed text files (no regex DoS)."""
        needle = text_pattern[:120].strip().lower()
        if not needle:
            return []
        out: list[dict[str, object]] = []
        for root in ([Path(r).resolve() for r in (roots or [])] or self.roots):
            if not root.is_dir() or self._root_for(root) is None:
                continue
            for path in itertools.islice(root.rglob("*"), 1500):
                if len(out) >= limit:
                    return out
                if (not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES
                        or self._root_for(path.resolve()) is None or path.name.lower() in SECRET_NAMES):
                    continue
                try:
                    if path.stat().st_size > self.max_bytes:
                        continue
                    for i, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                        if needle in line.lower():
                            out.append({"path": str(path), "line": i, "text": line.strip()[:200]})
                            if len(out) >= limit:
                                break
                except (OSError, UnicodeError):
                    continue
        return out

    def matches(self, name: str, patterns: list[str]) -> bool:
        return any(fnmatch.fnmatch(name.lower(), p.lower()) for p in patterns)
