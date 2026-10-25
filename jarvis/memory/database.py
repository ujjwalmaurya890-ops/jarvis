"""SQLite memory: preferences, transcripts, summaries, permissions.

Single file at data/jarvis.db.  Rules enforced in code:
* no passwords / API keys / tokens - a redaction pass runs on every write;
* transcripts expire after `retention_days` (0 = keep only the current session);
* `clear_*` methods are the implementation behind "forget my memory";
* WAL is disabled so the DB never holds a lock the UI can't break.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from security.input_validation import escape_like, strip_control_chars

log = logging.getLogger("jarvis.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    ts REAL NOT NULL,
    provider TEXT,
    tokens INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_turns_ts ON turns(ts DESC);
CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session TEXT NOT NULL,
    day TEXT NOT NULL,
    text TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS permissions (
    capability TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    granted REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    value TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS doc_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    chunk INTEGER NOT NULL,
    page INTEGER,
    text TEXT NOT NULL,
    words INTEGER DEFAULT 0,
    UNIQUE(path, chunk)
);
"""

SECRET_PATTERNS = [
    re.compile(r"(?i)\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|bearer)\b(?:\s+(?:is|for|to))?\s*[:=]?\s*[A-Za-z0-9_\-\.]{8,}"),
    re.compile(r"(?i)\bsecret\s+token\b\s*[:=]?\s*[^\s,.;]+"),
    # "password is X", "pin: 1234", "pwd = X" ... dictated secrets are common.
    re.compile(r"(?i)\b(?:password|passwort|passwd|pwd|pin|otp|secret|token)\b(?:\s+(?:is|for|to))?\s*[:=]?\s*[^\s,.;]+"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}\b"),                       # Google-style keys
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{16,}\b"),               # OpenAI-style keys
    re.compile(r"(?<!\d)(?:\d[ \-]?){13,19}(?!\d)"),                  # card-ish numbers
]
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def redact(text: str, *, emails: bool = True) -> str:
    """Best-effort removal of obvious credentials before anything is stored.

    Deliberately replaces the whole match (not a suffix) so nothing sensitive
    survives, and leaves the sentence readable enough to be useful. This is a
    safety net, not encryption: the DB is plain SQLite in your user folder.
    """
    out = (text or "").replace("\x00", "")
    for rx in SECRET_PATTERNS:
        out = rx.sub("[redacted]", out)
    if emails:
        out = EMAIL_PATTERN.sub("[redacted-email]", out)
    return out


class MemoryDB:
    def __init__(self, path: str | Path, retention_days: int = 30) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.retention_days = retention_days
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()
            self._migrate()
        self.purge_expired()

    def _migrate(self) -> None:
        """Add columns introduced after the first release (best-effort)."""
        try:
            cols = {row[1] for row in self._conn.execute("PRAGMA table_info(doc_chunks)")}
            if "page" not in cols:
                self._conn.execute("ALTER TABLE doc_chunks ADD COLUMN page INTEGER")
                self._conn.commit()
        except sqlite3.Error:
            log.warning("doc_chunks migration skipped", exc_info=True)

    # ---- low level ------------------------------------------------------------
    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def close(self) -> None:
        with suppress(Exception):
            self._conn.close()

    # ---- meta / facts -----------------------------------------------------------
    def set_meta(self, key: str, value: Any) -> None:
        self._exec(
            "INSERT INTO meta(key,value,updated) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
            (key, json.dumps(value)[:20000], time.time()),
        )

    def get_meta(self, key: str, default: Any = None) -> Any:
        rows = self._query("SELECT value FROM meta WHERE key=?", (key,))
        if not rows:
            return default
        try:
            return json.loads(rows[0]["value"])
        except (ValueError, TypeError):
            return default

    def remember(self, key: str, value: str) -> None:
        self._exec(
            "INSERT INTO facts(key,value,ts) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
            (redact(strip_control_chars(key))[:120], redact(strip_control_chars(value))[:2000], time.time()),
        )

    def recall(self, key: str) -> str | None:
        rows = self._query("SELECT value FROM facts WHERE key=?", (key,))
        return rows[0]["value"] if rows else None

    def all_facts(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._query("SELECT key,value,ts FROM facts ORDER BY ts DESC LIMIT 200")]

    def forget_fact(self, key: str) -> int:
        return self._exec("DELETE FROM facts WHERE key LIKE ?", (f"%{key}%",)).rowcount

    # ---- conversation ---------------------------------------------------------
    def add_turn(self, session: str, role: str, text: str, provider: str = "", tokens: int = 0) -> int:
        if self.retention_days == 0:
            return 0  # disabled persistence; the in-process transcript is in StateMachine
        from security.input_validation import strip_control_chars

        cur = self._exec(
            "INSERT INTO turns(session,role,text,ts,provider,tokens) VALUES(?,?,?,?,?,?)",
            (session, role, redact(strip_control_chars(text))[:8000], time.time(), provider, tokens),
        )
        return int(cur.lastrowid or 0)

    def recent_turns(self, limit: int = 12, session: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT role,text,ts,provider FROM turns"
        params: tuple = ()
        if session:
            sql += " WHERE session=?"
            params = (session,)
        sql += " ORDER BY id DESC LIMIT ?"
        rows = self._query(sql, (*params, limit))
        return [dict(r) for r in reversed(rows)]

    def context_messages(self, limit: int = 8) -> list[dict[str, str]]:
        out = []
        for row in self.recent_turns(limit):
            role = "assistant" if row["role"] in ("jarvis", "assistant") else "user"
            out.append({"role": role, "content": row["text"]})
        return out

    def search_turns(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        from security.input_validation import escape_like

        rows = self._query(
            "SELECT role,text,ts FROM turns WHERE text LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?",
            (f"%{escape_like(query)}%", limit),
        )
        return [dict(r) for r in rows]

    # ---- summaries --------------------------------------------------------------
    def add_summary(self, session: str, text: str) -> None:
        self._exec(
            "INSERT INTO summaries(session,day,text,ts) VALUES(?,?,?,?)",
            (session, time.strftime("%Y-%m-%d"), redact(strip_control_chars(text))[:4000], time.time()),
        )

    def summaries(self, limit: int = 30) -> list[dict[str, Any]]:
        return [dict(r) for r in self._query("SELECT day,text,ts FROM summaries ORDER BY id DESC LIMIT ?", (limit,))]

    # ---- permissions ------------------------------------------------------------
    def get_permissions(self) -> list[tuple[str, str]]:
        return [(r["capability"], r["scope"]) for r in self._query("SELECT capability,scope FROM permissions")]

    def save_permissions(self, mapping: dict[str, str]) -> None:
        with self._lock, self._conn:  # a failure must not erase all grants halfway
            self._conn.execute("DELETE FROM permissions")
            self._conn.executemany(
                "INSERT INTO permissions(capability,scope,granted) VALUES(?,?,?)",
                [(cap, scope, time.time()) for cap, scope in mapping.items() if scope == "permanent"],
            )

    # ---- document index ---------------------------------------------------------
    def index_document(self, path: str, chunks: list[str], pages: list[int | None] | None = None) -> int:
        self._exec("DELETE FROM doc_chunks WHERE path=?", (path,))
        for i, chunk in enumerate(chunks[:400]):
            page = pages[i] if pages and i < len(pages) else None
            self._exec(
                "INSERT INTO doc_chunks(path,chunk,page,text,words) VALUES(?,?,?,?,?)",
                (path, i, page, chunk, len(chunk.split())),
            )
        return len(chunks)

    def query_docs(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
        terms = [t for t in re.findall(r"[A-Za-z0-9\u0900-\u097F]{3,}", (query or "").lower())][:8]
        if not terms:
            return []
        clauses = " OR ".join(["text LIKE ? ESCAPE '\\'"] * len(terms))
        from security.input_validation import escape_like

        params = tuple(f"%{escape_like(t)}%" for t in terms) + (limit,)
        rows = self._query(
            f"SELECT path, chunk, page, text, words FROM doc_chunks WHERE {clauses} "
            f"ORDER BY words DESC LIMIT ?",
            params,
        )
        return [dict(r) for r in rows]

    def indexed_paths(self) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self._query("SELECT path, COUNT(*) AS chunks, SUM(words) AS words FROM doc_chunks GROUP BY path ORDER BY words DESC")
        ]

    def remove_document(self, path: str) -> int:
        return self._exec("DELETE FROM doc_chunks WHERE path=?", (path,)).rowcount

    # ---- retention --------------------------------------------------------------
    def purge_expired(self) -> int:
        if self.retention_days == 0:
            count = len(self._query("SELECT id FROM turns"))
            self.clear_conversation()
            return count
        cutoff = time.time() - self.retention_days * 86400
        cur = self._exec("DELETE FROM turns WHERE ts < ?", (cutoff,))
        self._exec("DELETE FROM summaries WHERE ts < ?", (cutoff,))
        return cur.rowcount

    def clear_conversation(self) -> None:
        self._exec("DELETE FROM turns", ())
        self._exec("DELETE FROM summaries", ())
        self._exec("VACUUM", ())

    def clear_memory(self) -> None:
        """Forget conversation, facts and document index; preserve tool grants and notes."""
        with self._lock, self._conn:
            for table in ("turns", "summaries", "facts", "doc_chunks"):
                self._conn.execute(f"DELETE FROM {table}")
            self._conn.execute("DELETE FROM meta WHERE key='index_updated'")
        self._exec("VACUUM")

    def clear_all(self) -> None:
        for table in ("turns", "summaries", "facts", "doc_chunks", "permissions"):
            self._exec(f"DELETE FROM {table}", ())  # noqa: S608 - fixed table list
        self._exec("VACUUM", ())

    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {"bytes": self.path.stat().st_size if self.path.exists() else 0}
        for table in ("turns", "summaries", "facts", "doc_chunks"):
            with suppress(sqlite3.Error):
                out[table] = int(self._query(f"SELECT COUNT(*) AS c FROM {table}")[0]["c"])  # noqa: S608
        return out
