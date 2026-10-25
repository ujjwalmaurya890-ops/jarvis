"""Input validation helpers.

Everything a user (or a webpage, or a model) hands us is treated as untrusted
text.  Before it reaches a tool, the SQLite database, a regex or the network,
it is cleaned here:

* control characters (including NUL and terminal escapes) are stripped, so a
  pasted blob cannot smuggle escape codes or break SQL/HTML parsing;
* the length is capped, so a huge paste cannot exhaust memory or feed a
  pathological regex;
* SQL ``LIKE`` wildcards are escaped, so searching for ``100%`` matches a
  literal percent instead of every row (and cannot be used to slow the
  database down).

These helpers are deliberately dependency-free and side-effect free so they can
be unit-tested without any hardware or network.
"""

from __future__ import annotations

import re

# Characters that are never meaningful in a command, a search or a filename:
# everything below 0x20 except tab/newline/carriage-return, plus DEL and the
# other C1 control ranges that sometimes ride along in pasted text.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# SQL LIKE metacharacters.  Backslash is the default escape character, so it
# must be escaped first.
_LIKE_ESCAPES = (("\\", "\\\\"), ("%", "\\%"), ("_", "\\_"))


def strip_control_chars(text: str) -> str:
    """Remove control characters, keeping tab/newline/carriage-return."""
    if not text:
        return ""
    return _CONTROL.sub("", text)


def sanitize_text(text: str, max_len: int = 2000) -> str:
    """Return cleaned text, truncated to ``max_len`` characters.

    This is the single gate every inbound string should pass through.  It never
    raises: an empty or non-string input simply becomes an empty string, and the
    caller decides whether that is an error.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    cleaned = strip_control_chars(text)
    if max_len and max_len > 0 and len(cleaned) > max_len:
        cleaned = cleaned[:max_len]
    return cleaned


def escape_like(text: str) -> str:
    """Escape ``%``, ``_`` and ``\\`` so a value is matched literally.

    SQLite's ``LIKE`` treats ``%`` and ``_`` as wildcards.  Without escaping, a
    search for ``100%`` or ``a_b`` would match far more than intended.
    """
    out = text or ""
    for src, dst in _LIKE_ESCAPES:
        out = out.replace(src, dst)
    return out


def is_blank(text: str) -> bool:
    return not strip_control_chars(text or "").strip()


def safe_filename(name: str, max_len: int = 80) -> str:
    """Make a short, filesystem-safe name from arbitrary text.

    Used when a tool needs to turn a model-supplied title into a file name.
    Only letters, numbers, dot, dash and underscore survive; everything else is
    collapsed to a single dash and trimmed.
    """
    cleaned = strip_control_chars(name or "").strip()
    cleaned = re.sub(r"[^0-9A-Za-z._\-]+", "-", cleaned).strip("-._")
    return cleaned[:max_len] or "document"
