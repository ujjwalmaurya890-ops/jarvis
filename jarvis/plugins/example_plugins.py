"""Bundled example plugins.

These are deliberately tiny so users can copy the pattern.  A real plugin is a
module plus a JSON manifest; the registry imports the module lazily and only
runs it when it is enabled.
"""

from __future__ import annotations


def uppercase(text: str) -> str:
    """Return the text in upper case."""
    return str(text).upper()


def word_count(text: str) -> int:
    """Return the number of whitespace-separated words."""
    return len(str(text).split())


def boom() -> str:
    """An example plugin that crashes - used to prove error handling."""
    raise RuntimeError("example plugin failure")
