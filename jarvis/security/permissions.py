"""Permission broker: the single place that decides whether a tool may run.

Design rules enforced here:
* every capability is denied until the user grants it (default-deny);
* grants can be one-shot, session-only or permanent;
* dangerous capability always needs a fresh confirmation, even if granted;
* the decision is made from *validated* arguments, never from raw model text.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from security.network import NetworkPolicyError, check_url_shape

log = logging.getLogger("jarvis.permissions")


class Capability(str, Enum):
    MIC_LISTEN = "mic.listen"
    MIC_CLAP = "mic.clap"
    NET_SEARCH = "net.search"
    NET_FETCH = "net.fetch"
    NET_BROWSER = "net.browser"
    APP_LAUNCH = "apps.launch"
    MEDIA_CONTROL = "media.control"
    SITE_OPEN = "sites.open"
    FS_READ = "fs.read"
    FS_WRITE = "fs.write"
    RUN_COMMAND = "shell.run"
    POWER = "system.power"
    CLOUD_AI = "ai.cloud"


DANGEROUS: set[Capability] = {Capability.POWER, Capability.RUN_COMMAND, Capability.FS_WRITE}


@dataclass
class Decision:
    allowed: bool
    reason: str
    needs_confirmation: bool = False
    summary: str = ""

    @classmethod
    def deny(cls, reason: str) -> "Decision":
        return cls(False, reason)

    @classmethod
    def grant(cls, reason: str = "", summary: str = "") -> "Decision":
        return cls(True, reason, summary=summary)

    @classmethod
    def confirm(cls, reason: str, summary: str) -> "Decision":
        return cls(False, reason, needs_confirmation=True, summary=summary)


class PermissionBroker:
    def __init__(self, allowed_dirs: list[str] | None = None, store: object | None = None) -> None:
        self._lock = threading.RLock()
        self._grants: dict[Capability, str] = {}  # capability -> "session" | "permanent"
        self._allowed_dirs = [Path(p).resolve() for p in (allowed_dirs or [])]
        self._store = store
        self._load()

    # ---- persistence (best-effort; a missing DB must not break boot) --------
    def _load(self) -> None:
        getter = getattr(self._store, "get_permissions", None)
        if callable(getter):
            try:
                for name, scope in getter():
                    if scope == "permanent":
                        self._grants[Capability(name)] = scope
            except (ValueError, KeyError, OSError) as exc:
                log.warning("Stored permissions could not be loaded: %s", exc)

    def _persist(self) -> None:
        setter = getattr(self._store, "save_permissions", None)
        if callable(setter):
            try:
                setter({c.value: s for c, s in self._grants.items() if s == "permanent"})
            except OSError as exc:
                log.warning("Permission changes could not be persisted: %s", exc)

    # ---- grants --------------------------------------------------------------
    def is_granted(self, cap: Capability) -> bool:
        with self._lock:
            return cap in self._grants

    def grant(self, cap: Capability, scope: str = "session") -> None:
        if scope not in {"one-shot", "session", "permanent"}:
            raise ValueError("Permission scope must be one-shot, session or permanent")
        with self._lock:
            self._grants[cap] = scope
        self._persist()

    def consume_one_shot(self, cap: Capability) -> None:
        with self._lock:
            if self._grants.get(cap) == "one-shot":
                self._grants.pop(cap)

    def revoke(self, cap: Capability) -> None:
        with self._lock:
            self._grants.pop(cap, None)
        self._persist()

    def reset(self) -> None:
        with self._lock:
            self._grants.clear()
        self._persist()

    def scopes(self) -> dict[str, str]:
        with self._lock:
            return dict(self._grants)

    # ---- path sandbox --------------------------------------------------------
    def add_allowed_dir(self, path: str | Path) -> None:
        p = Path(path).resolve()
        if p not in self._allowed_dirs:
            self._allowed_dirs.append(p)

    @property
    def allowed_dirs(self) -> list[Path]:
        return list(self._allowed_dirs)

    def is_path_allowed(self, path: str | Path, *, write: bool = False) -> bool:
        """True only when the resolved path lives inside an allowed root.

        Symlinks and ``..`` are resolved first; on Windows we compare
        case-insensitively because the filesystem is case-insensitive.
        """
        try:
            target = Path(path).expanduser().resolve()
        except (OSError, ValueError):
            return False
        if write and target.exists() and target.is_dir():
            return False  # never overwrite a whole directory
        for root in self._allowed_dirs:
            try:
                root_r = root.resolve()
                if write:
                    # Writing must create a file *inside* the root, not on it.
                    target.relative_to(root_r)
                    ok = target != root_r
                else:
                    target.relative_to(root_r)
                    ok = True
                if ok and _same_ci(target, root_r):
                    return True
            except ValueError:
                continue
        return False

    def check_path(self, path: str | Path, *, write: bool = False) -> Decision:
        if not self.is_path_allowed(path, write=write):
            return Decision.deny(
                f"'{path}' is outside the allowed folders. Add the folder in Settings first."
            )
        return Decision.grant(summary=str(path))

    # ---- URL / app allowlists -------------------------------------------------
    def check_url(self, url: str, allowed_sites: list[str]) -> Decision:
        try:
            normalised = check_url_shape(url if "://" in url else "https://" + url, allowed_sites)
        except NetworkPolicyError as exc:
            return Decision.deny(str(exc))
        return Decision.grant(summary=normalised)

    # ---- top level ------------------------------------------------------------
    def request(
        self,
        cap: Capability,
        *,
        summary: str = "",
        offline: bool = False,
        privacy_mode: bool = False,
    ) -> Decision:
        if cap is Capability.CLOUD_AI and (privacy_mode or offline):
            return Decision.deny("Cloud AI is switched off by privacy/offline mode.")
        if cap in (Capability.NET_SEARCH, Capability.NET_FETCH, Capability.NET_BROWSER, Capability.SITE_OPEN) and offline:
            return Decision.deny("Offline mode is enabled.")
        if not self.is_granted(cap):
            return Decision.confirm(
                "Capability not granted yet.",
                summary or cap.value,
            )
        if cap in DANGEROUS:
            return Decision.confirm("Dangerous action needs a per-call confirmation.", summary or cap.value)
        return Decision.grant(summary=summary)


def _same_ci(target: Path, root: Path) -> bool:
    """Case-insensitive containment check for Windows paths."""
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return str(target).lower().startswith(str(root).lower().rstrip("\\/") + "\\")
