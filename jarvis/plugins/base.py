"""Plugin manifest schema, validation and loading.

A plugin is described by a small JSON manifest:

    {
      "name": "uppercase",
      "description": "Uppercases text.",
      "capability": "fs.read",
      "schema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"]
      },
      "module": "plugins.example_plugins",
      "entrypoint": "uppercase"
    }

The manifest is validated before anything is imported, so a broken plugin can
never crash the core.  The module/entrypoint are only imported on demand by the
:class:`~plugins.registry.PluginRegistry`.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from security.permissions import Capability

log = logging.getLogger("jarvis.plugins")

REQUIRED_KEYS = {"name", "description", "schema", "capability"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,40}$")
_KNOWN_CAPABILITIES = {c.value for c in Capability}


class PluginError(Exception):
    """Raised for any invalid or unreadable plugin manifest."""


@dataclass
class PluginSpec:
    name: str
    description: str = ""
    schema: dict = field(default_factory=dict)
    capability: str | None = None
    dangerous: bool = False
    version: str = "1.0.0"
    enabled: bool = True
    module: str = ""
    entrypoint: str = "run"

    @property
    def arguments(self) -> list[str]:
        props = self.schema.get("properties") if isinstance(self.schema, dict) else None
        return list(props.keys()) if props else []


def validate_manifest(data: object) -> PluginSpec:
    if not isinstance(data, dict):
        raise PluginError("A plugin manifest must be a JSON object.")
    missing = REQUIRED_KEYS - set(data)
    if missing:
        raise PluginError(f"Missing required keys: {', '.join(sorted(missing))}")
    name = str(data["name"]).strip()
    if not NAME_RE.match(name):
        raise PluginError("Plugin name must be lowercase alphanumeric (_ . - allowed), up to 40 chars.")
    if not isinstance(data["schema"], dict):
        raise PluginError("schema must be an object describing the arguments.")
    capability = str(data["capability"])
    if capability not in _KNOWN_CAPABILITIES:
        raise PluginError(f"capability must be one of: {', '.join(sorted(_KNOWN_CAPABILITIES))}")
    return PluginSpec(
        name=name,
        description=str(data.get("description", ""))[:200],
        schema=data["schema"],
        capability=capability,
        dangerous=bool(data.get("dangerous", False)),
        version=str(data.get("version", "1.0.0")),
        enabled=bool(data.get("enabled", True)),
        module=str(data.get("module", "")),
        entrypoint=str(data.get("entrypoint", "run")),
    )


def load_manifest(path: str | Path) -> PluginSpec:
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PluginError(f"Could not read manifest '{p.name}': {exc}") from exc
    return validate_manifest(data)
