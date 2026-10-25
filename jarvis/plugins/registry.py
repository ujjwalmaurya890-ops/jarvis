"""The approved-plugin registry: schemas, permissions, error handling, enable/disable.

Plugins are declared from a manifest, imported lazily, and only run when they
are enabled.  Every run is wrapped so a crashing plugin returns a normal
``{"ok": False, ...}`` instead of taking the assistant down, and the capability
is checked through the same permission broker the built-in tools use.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from plugins.base import PluginError, PluginSpec, load_manifest
from security.permissions import Capability

log = logging.getLogger("jarvis.plugins.registry")


@dataclass
class PluginRecord:
    spec: PluginSpec
    handler: Callable[..., Any] | None = None
    loaded: bool = False
    last_error: str = ""


class PluginRegistry:
    def __init__(self, broker: Any = None, confirm: Callable[[str, str], bool] | None = None) -> None:
        self.broker = broker
        self.confirm = confirm or (lambda summary, cap: False)
        self.plugins: dict[str, PluginRecord] = {}

    # ---- declaration --------------------------------------------------------
    def declare(self, spec: PluginSpec) -> PluginRecord:
        if spec.name in self.plugins:
            raise PluginError(f"A plugin named '{spec.name}' is already registered.")
        rec = PluginRecord(spec=spec)
        self.plugins[spec.name] = rec
        return rec

    def register(self, spec: PluginSpec, handler: Callable[..., Any]) -> PluginRecord:
        rec = self.declare(spec)
        rec.handler = handler
        rec.loaded = True
        return rec

    def load_manifest(self, path: str | Path) -> PluginRecord:
        spec = load_manifest(path)
        rec = self.declare(spec)
        if spec.module:
            self._import(rec)
        return rec

    def load_folder(self, folder: str | Path) -> list[str]:
        """Load every valid manifest in a folder; skip the broken ones."""
        root = Path(folder)
        loaded: list[str] = []
        if not root.is_dir():
            return loaded
        for manifest in sorted(root.glob("*.json")):
            try:
                rec = self.load_manifest(manifest)
                loaded.append(rec.spec.name)
            except PluginError as exc:
                log.warning("Skipping plugin manifest %s: %s", manifest.name, exc)
        return loaded

    def _import(self, rec: PluginRecord) -> None:
        spec = rec.spec
        try:
            module = importlib.import_module(spec.module)
            handler = getattr(module, spec.entrypoint)
            rec.handler = handler
            rec.loaded = True
        except Exception as exc:  # a plugin must never break the core
            rec.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("plugin %s failed to load: %s", spec.name, rec.last_error)

    # ---- lifecycle ---------------------------------------------------------
    def enable(self, name: str) -> bool:
        self._get(name).spec.enabled = True
        return True

    def disable(self, name: str) -> bool:
        self._get(name).spec.enabled = False
        return True

    def _get(self, name: str) -> PluginRecord:
        if name not in self.plugins:
            raise PluginError(f"No such plugin: '{name}'.")
        return self.plugins[name]

    def list_plugins(self) -> list[dict]:
        return [
            {
                "name": r.spec.name,
                "version": r.spec.version,
                "description": r.spec.description,
                "capability": r.spec.capability,
                "dangerous": r.spec.dangerous,
                "enabled": r.spec.enabled,
                "loaded": r.loaded,
                "last_error": r.last_error,
            }
            for r in self.plugins.values()
        ]

    # ---- execution ---------------------------------------------------------
    def run(self, name: str, arguments: dict | None = None) -> dict:
        rec = self._get(name)
        if not rec.spec.enabled:
            return {"ok": False, "error": f"Plugin '{name}' is disabled."}
        if not rec.loaded or rec.handler is None:
            return {"ok": False, "error": f"Plugin '{name}' is not loaded (last error: {rec.last_error})."}
        if self.broker is not None and rec.spec.capability:
            decision = self.broker.request(Capability(rec.spec.capability), summary=name)
            if not decision.allowed and not (
                decision.needs_confirmation and self.confirm(decision.summary or name, rec.spec.capability)
            ):
                return {"ok": False, "error": decision.reason}
        args = dict(arguments or {})
        try:
            result = rec.handler(**args)
        except TypeError as exc:
            return {"ok": False, "error": f"Bad arguments: {exc}"}
        except Exception as exc:  # noqa: BLE001 - plugin boundary
            rec.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("plugin %s crashed: %s", name, rec.last_error)
            return {"ok": False, "error": rec.last_error}
        return {"ok": True, "plugin": name, "result": result}
