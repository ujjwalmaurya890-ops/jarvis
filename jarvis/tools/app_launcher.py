"""Application launching - allow-list only.

Nothing is executed by a shell.  We resolve a name from the user's allow-list
and hand `subprocess.Popen` an argument list with `shell=False`, so there is no
way for "notepad && del c:\\windows" to mean anything.  Bare .exe names are
looked up with `where` semantics but never built from user text.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from security.network import NetworkPolicyError, validate_public_url

log = logging.getLogger("jarvis.apps")


@dataclass
class LaunchResult:
    ok: bool
    message: str
    pid: int = 0
    path: str = ""


class AppLauncher:
    def __init__(
        self, allowlist: dict[str, str], *, require_confirmation: bool = True,
        allowed_sites: list[str] | None = None,
    ) -> None:
        self.allowlist = {k.strip().lower(): v for k, v in (allowlist or {}).items()}
        self.allowed_sites = allowed_sites
        self.require_confirmation = require_confirmation
        self._running: dict[str, int] = {}
        self._lock = threading.Lock()

    # ---- resolution ----------------------------------------------------------
    def resolve(self, name: str) -> Path | None:
        key = (name or "").strip().lower()
        if key in self.allowlist:
            target = self.allowlist[key]
        elif key in {Path(v).stem.lower() for v in self.allowlist.values()}:
            target = next(v for v in self.allowlist.values() if Path(v).stem.lower() == key)
        else:
            return None
        return Path(target)

    def known_names(self) -> list[str]:
        return sorted(self.allowlist)

    def executable_for(self, raw: str) -> str | None:
        """Accepts only a filename from the allow-list, never a full command line."""
        candidate = Path(raw).name
        found = shutil.which(candidate)
        if found:
            return found
        direct = Path(raw)
        return str(direct) if direct.is_file() else None

    # ---- action ---------------------------------------------------------------
    def launch(self, name: str, args: list[str] | None = None) -> LaunchResult:
        if sys.platform != "win32":
            return LaunchResult(False, "Application launching is implemented for Windows only.")
        target = self.resolve(name)
        if target is None:
            return LaunchResult(
                False,
                f"'{name}' is not on the allow-list. Add it in Settings → Applications. Known: "
                + ", ".join(self.known_names()),
            )
        exe = str(target) if target.is_absolute() else (self.executable_for(str(target)) or "")
        if not exe:
            return LaunchResult(False, f"I found the entry '{target}' but the program is not installed.")

        argv = [exe, *[str(a) for a in (args or []) if len(str(a)) < 200]]
        for a in argv[1:]:
            if any(ch in str(a) for ch in "&|`;><$\n\r"):
                return LaunchResult(False, "Arguments with shell characters are refused.")
        try:
            proc = subprocess.Popen(  # noqa: S603 - fixed argv, shell=False
                argv,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        except OSError as exc:
            return LaunchResult(False, f"Could not start '{name}': {exc}")
        with self._lock:
            self._running[name.lower()] = proc.pid
        return LaunchResult(True, f"Opening {name}.", pid=proc.pid, path=exe)

    def focus_or_launch(self, name: str) -> LaunchResult:
        """Best-effort: bring the window forward if it already exists."""
        with self._lock:
            pid = self._running.get(name.lower())
        if pid and focus_window_of_pid(pid):
            return LaunchResult(True, f"Already open - focusing {name}.", pid=pid)
        return self.launch(name)

    def open_url(self, url: str) -> LaunchResult:
        """Open a public URL; validate the resolved IP before involving Windows."""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            validated = validate_public_url(url, self.allowed_sites)
        except NetworkPolicyError as exc:
            return LaunchResult(False, f"Browser URL refused: {exc}")
        try:
            if sys.platform == "win32":
                os.startfile(validated.url)  # type: ignore[attr-defined]
            else:  # keeps the module importable during tests on Linux
                import webbrowser

                webbrowser.open(validated.url)
            return LaunchResult(True, f"Opened {validated.url}", path=validated.url)
        except OSError as exc:
            return LaunchResult(False, f"Could not open the browser: {exc}")


def focus_window_of_pid(pid: int) -> bool:
    """Bring the first visible top-level window of `pid` to the front.

    Pure Win32 via ctypes - no extra dependency.  Returns False on any error or
    on non-Windows systems, so callers can just fall back to launching.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
    except Exception:
        return False

    WinEnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found: list[int] = []

    @WinEnumProc
    def _cb(hwnd, lparam):  # noqa: ANN001
        win_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(win_pid))
        if win_pid.value == pid and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            found.append(int(hwnd))
            return False  # stop enumerating
        return True

    try:
        user32.EnumWindows(_cb, 0)
        if not found:
            return False
        hwnd = found[0]
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        return bool(user32.SetForegroundWindow(hwnd))
    except Exception:
        log.debug("window focus failed", exc_info=True)
        return False
