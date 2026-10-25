"""Input-validation tests: control chars, length caps, LIKE escaping, filenames."""

from __future__ import annotations

import pytest

from memory.database import MemoryDB
from security.input_validation import escape_like, is_blank, safe_filename, sanitize_text, strip_control_chars


def test_strip_control_chars_keeps_tab_and_newline():
    assert strip_control_chars("a\tb\nc\rd") == "a\tb\nc\rd"
    assert strip_control_chars("a\x00b\x07c\x1b[31m") == "abc[31m"
    assert strip_control_chars("") == ""


def test_sanitize_text_caps_length_and_removes_nul():
    long = "x" * 5000
    out = sanitize_text(long, max_len=100)
    assert len(out) == 100
    assert "\x00" not in sanitize_text("evil\x00payload")
    assert sanitize_text(None) == ""
    assert sanitize_text(1234) == "1234"


def test_sanitize_text_keeps_ordinary_speech():
    assert sanitize_text("open YouTube for me") == "open YouTube for me"


def test_escape_like_makes_wildcards_literal():
    assert escape_like("100%") == "100\\%"
    assert escape_like("a_b") == "a\\_b"
    assert escape_like("back\\slash") == "back\\\\slash"
    assert escape_like("plain") == "plain"


def test_is_blank():
    assert is_blank("   ")
    assert is_blank("\x00\n")
    assert not is_blank("hi")


def test_safe_filename_is_filesystem_safe():
    assert safe_filename("My Report: Q3") == "My-Report-Q3"
    assert safe_filename("../../etc/passwd") == "etc-passwd"
    assert safe_filename("") == "document"
    assert len(safe_filename("z" * 200)) <= 80
    assert safe_filename("a\x00b") == "ab"


def test_like_escaping_prevents_wildcard_overmatch(tmp_path):
    db = MemoryDB(tmp_path / "like.db")
    db.add_turn("s", "user", "the report is 100% complete")
    db.add_turn("s", "user", "the ratio is 1000 done")
    db.add_turn("s", "user", "the a_b experiment")
    db.add_turn("s", "user", "the axb experiment")
    hits = db.search_turns("100%")
    assert len(hits) == 1 and "100%" in hits[0]["text"]
    # "a_b" must not match "axb" now that the underscore is escaped.
    assert [h["text"] for h in db.search_turns("a_b")] == ["the a_b experiment"]
    db.close()


def test_control_chars_never_reach_the_database(tmp_path):
    db = MemoryDB(tmp_path / "ctrl.db")
    db.add_turn("s", "user", "hello\x00world\x1b[2J")
    stored = db.recent_turns(1)[0]["text"]
    assert "\x00" not in stored and "\x1b" not in stored
    db.close()
