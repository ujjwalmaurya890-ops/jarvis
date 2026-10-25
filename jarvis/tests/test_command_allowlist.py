"""Command allow-list tests: no shell, no invented commands, no injection."""

from __future__ import annotations

import pytest

from security.command_allowlist import CommandNotAllowed, parse_allowlist, resolve


def test_parses_named_entry():
    allow = parse_allowlist(["build = python -m build"])
    assert allow["build"].argv == ["python", "-m", "build"]


def test_accepts_multiline_string():
    allow = parse_allowlist("run = python main.py --text\ntest = pytest -q")
    assert set(allow) == {"run", "test"}


def test_rejects_shell_operators():
    for bad in ["rm = del /q C:\\*", "x = dir && notepad", "y = echo hi | more", "z = python $(whoami)"]:
        with pytest.raises(CommandNotAllowed):
            parse_allowlist([bad])


def test_rejects_destructive_programs():
    for bad in ["p = shutdown /s /t 0", "r = rm -rf tmp", "reg = reg delete HKLM\\x"]:
        with pytest.raises(CommandNotAllowed):
            parse_allowlist([bad])


def test_rejects_missing_equals():
    with pytest.raises(CommandNotAllowed):
        parse_allowlist(["just a command"])


def test_unknown_name_cannot_be_run():
    allow = parse_allowlist(["build = python -m build"])
    with pytest.raises(CommandNotAllowed):
        resolve("delete", allow)


def test_resolve_returns_exact_argv_only():
    allow = parse_allowlist(["run = python main.py"])
    cmd = resolve("run", allow)
    assert cmd.argv == ["python", "main.py"]
    with pytest.raises(CommandNotAllowed):
        resolve("run ; notepad", allow)


def test_long_argument_rejected():
    with pytest.raises(CommandNotAllowed):
        parse_allowlist([f"big = python {'x' * 400}"])


def test_too_many_arguments_rejected():
    with pytest.raises(CommandNotAllowed):
        parse_allowlist(["many = python a b c d e f g h"])


def test_quoted_windows_paths_survive():
    allow = parse_allowlist(['run = "C:\\Program Files\\app.exe" --profile "My Profile"'])
    assert allow["run"].argv[0] == "C:\\Program Files\\app.exe"
    assert "My Profile" in allow["run"].argv
