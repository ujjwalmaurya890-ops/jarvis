"""Final security checks: shell allowlist, secrets in VCS, loopback-only API."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.server import LocalAPI
from config.settings import Settings
from security.command_allowlist import CommandNotAllowed, parse_allowlist, resolve
from security.permissions import Capability, PermissionBroker
from tools.registry import ToolRegistry
from tools.system_tools import ToolContext, register_system_tools

ROOT = Path(__file__).resolve().parent.parent.parent


# --------------------------------------------------------------- command allowlist
def test_allowlist_rejects_shell_metacharacters():
    with pytest.raises(CommandNotAllowed):
        parse_allowlist(["build = python build.py && rm -rf ."])


def test_allowlist_rejects_dangerous_program():
    with pytest.raises(CommandNotAllowed):
        parse_allowlist(["wipe = rm -rf /"])


def test_allowlist_rejects_malformed_entry():
    with pytest.raises(CommandNotAllowed):
        parse_allowlist(["not-a-command-line"])


def test_resolve_rejects_unknown_name():
    allow = parse_allowlist(["build = python -m compileall"])
    with pytest.raises(CommandNotAllowed):
        resolve("rm -rf /", allow)


def test_allowlist_parses_valid_entry():
    allow = parse_allowlist(["build = python -m compileall"])
    cmd = resolve("build", allow)
    assert cmd.argv[0] == "python" and cmd.argv[1] == "-m"


def test_run_command_tool_refuses_unknown_name(tmp_path):
    settings = Settings()
    settings.automation.allowed_dirs = [str(tmp_path)]
    settings.automation.allowed_commands = ["build = python -m compileall"]
    broker = PermissionBroker([str(tmp_path)])
    broker.grant(Capability.RUN_COMMAND)
    reg = ToolRegistry(broker, confirm=lambda *a: True)
    register_system_tools(reg, ToolContext(settings=settings, launcher=None, files=None,
                                           notes=None, memory=None, browser=None, index=None))
    res = reg.execute("run_command", {"name": "rm -rf /"})
    assert "not allow-listed" in res.speech


# --------------------------------------------------------------- secrets in VCS
def test_gitignore_covers_secrets_and_runtime_data():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in text
    assert "jarvis/data/" in text


def test_git_ignores_env_file():
    result = subprocess.run(["git", "check-ignore", ".env"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, "a .env file should be ignored by git"


# --------------------------------------------------------------- loopback-only API
def test_api_binds_to_loopback_only(tmp_path):
    assistant = SimpleNamespace(
        mic=None,
        state=SimpleNamespace(state=SimpleNamespace(value="offline")),
    )
    server = LocalAPI(assistant, {"providers": []}, tmp_path / "api-token", port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()
