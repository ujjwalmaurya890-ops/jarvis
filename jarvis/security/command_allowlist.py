"""Allow-listed project commands.

The model NEVER gets a shell.  It may only pick an entry from a named list the
human wrote, and that entry is re-validated here at execution time.  Shell
metacharacters are rejected outright so a prompt-injected string such as
``build && del c:\\`` can never reach ``subprocess``.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

SHELL_METACHARS = re.compile(r"[;&|`$><\n\r]|\$\(|&&|\|\|")
DANGEROUS_TOKENS = re.compile(
    r"(?i)^(rm|del|erase|format|diskpart|shutdown|restart|reg|schtasks|netsh|"
    r"wmic|psexp|cipher|takeown|icacls|bcdedit|vssadmin|disable-"
    r"|remove-item|set-executionpolicy)"
)
MAX_ARG_LENGTH = 240
MAX_ARGS = 6


class CommandNotAllowed(Exception):
    """Raised when a command is rejected before anything is executed."""


@dataclass
class AllowedCommand:
    name: str
    argv: list[str]
    cwd: str = ""
    note: str = ""
    timeout: float = 300.0

    def describe(self) -> str:
        return f"{self.name}: {' '.join(self.argv)}"


def parse_allowlist(raw: list[str] | str) -> dict[str, AllowedCommand]:
    """Parse user entries of the form ``name = command arg1 arg2``.

    Accepts a list or a newline-separated string.  Entries that look like a
    shell pipeline are skipped with a clear reason rather than silently
    mangled.
    """
    lines = raw if isinstance(raw, list) else [ln for ln in raw.splitlines() if ln.strip()]
    out: dict[str, AllowedCommand] = {}
    for line in lines:
        if not line.strip():
            continue
        name, sep, body = line.partition("=")
        if not sep:
            raise CommandNotAllowed(f"'{line}' needs the form 'name = command ...'")
        name, body = name.strip().lower(), body.strip()
        if SHELL_METACHARS.search(body):
            raise CommandNotAllowed(f"'{name}' contains a shell operator; only plain programs are allowed.")
        try:
            argv = shlex.split(body, posix=False)
        except ValueError as exc:  # unbalanced quotes
            raise CommandNotAllowed(f"'{name}' could not be parsed: {exc}") from exc
        argv = [a.strip('"') for a in argv if a.strip('"')]
        if not argv:
            raise CommandNotAllowed(f"'{name}' has no program.")
        if DANGEROUS_TOKENS.match(Path(argv[0]).name):
            raise CommandNotAllowed(f"'{name}' uses the blocked program '{argv[0]}'.")
        if len(argv) - 1 > MAX_ARGS:
            raise CommandNotAllowed(f"'{name}' passes too many arguments.")
        for arg in argv[1:]:
            if len(arg) > MAX_ARG_LENGTH:
                raise CommandNotAllowed(f"'{name}' has an unreasonably long argument.")
        out[name] = AllowedCommand(name=name, argv=argv)
    return out


def resolve(name: str, allowlist: dict[str, AllowedCommand]) -> AllowedCommand:
    """Return the allow-listed entry for ``name`` or raise.  Never builds a new command."""
    key = (name or "").strip().lower()
    if key not in allowlist:
        available = ", ".join(sorted(allowlist)) or "none configured"
        raise CommandNotAllowed(f"'{key}' is not allow-listed. Available: {available}")
    cmd = allowlist[key]
    for arg in cmd.argv:
        if SHELL_METACHARS.search(arg):
            raise CommandNotAllowed("Stored command was modified to contain shell operators.")
    if cmd.cwd:
        cwd = Path(cmd.cwd)
        if not cwd.is_dir():
            raise CommandNotAllowed(f"Working directory '{cwd}' does not exist.")
    return cmd
