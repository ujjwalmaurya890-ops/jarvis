"""System monitoring: memory pressure, disk space, services, local models.

Everything here is read-only and dependency-light.  The important idea for an
8 GB machine is :func:`is_low_memory`: when free RAM drops below a threshold
JARVIS should shed heavy features (document indexing, page extraction) rather
than thrash.  :class:`ResourceGuard` bundles that decision so the assistant can
ask it before doing something expensive.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
from dataclasses import dataclass

log = logging.getLogger("jarvis.monitor")

# Below this many MB of *available* RAM we treat the machine as low on memory.
LOW_MEMORY_MB = 1500.0


def _meminfo() -> dict[str, float]:
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            values: dict[str, float] = {}
            for line in fh:
                key, _, rest = line.partition(":")
                parts = rest.split()
                if parts:
                    values[key.strip()] = float(parts[0])  # reported in kB
            return values
    except OSError:
        return {}


def memory_status() -> dict[str, float | bool]:
    """Return RAM readings in MB. Works on Windows and Linux."""
    total = used = available = 0.0
    percent: float | bool = 0.0
    if platform.system() == "Windows":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
                total = stat.ullTotalPhys / 1e6
                available = stat.ullAvailPhys / 1e6
                used = total - available
                percent = float(stat.dwMemoryLoad)
        except Exception:
            log.debug("GlobalMemoryStatusEx failed", exc_info=True)
    else:
        v = _meminfo()
        total = v.get("MemTotal", 0.0) / 1024.0
        available = v.get("MemAvailable", v.get("MemFree", 0.0)) / 1024.0
        used = total - available
        percent = round((used / total) * 100, 1) if total > 0 else 0.0
    return {
        "ram_total_mb": round(total, 1),
        "ram_available_mb": round(available, 1),
        "ram_used_mb": round(used, 1),
        "ram_percent": percent,
    }


def is_low_memory(threshold_mb: float = LOW_MEMORY_MB) -> bool:
    return memory_status().get("ram_available_mb", 0) < threshold_mb


def disk_status(path: str | None = None) -> dict[str, object]:
    target = path or os.path.expanduser("~")
    try:
        usage = shutil.disk_usage(target)
        return {
            "path": target,
            "total_gb": round(usage.total / 1e9, 2),
            "free_gb": round(usage.free / 1e9, 2),
            "used_percent": round((usage.used / usage.total) * 100, 1),
        }
    except OSError:
        return {"path": target, "error": "could not read disk usage"}


def service_status(name: str) -> dict[str, object]:
    """Check a Windows service state (running/stopped). Windows-only."""
    name = (name or "").strip()
    if not name:
        return {"supported": False, "status": "no service name given"}
    if platform.system() != "Windows":
        return {"supported": False, "service": name, "status": "service inspection is Windows-only"}
    try:
        import subprocess

        out = subprocess.run(["sc", "query", name], shell=False, capture_output=True, text=True, timeout=10)  # noqa: S603
        text = ((out.stdout or "") + (out.stderr or "")).lower()
        if "does not exist" in text:
            state = "not installed"
        elif "running" in text:
            state = "running"
        elif "stopped" in text:
            state = "stopped"
        else:
            state = "unknown"
        return {"supported": True, "service": name, "status": state}
    except Exception as exc:
        return {"supported": True, "service": name, "status": f"error: {exc}"}


def model_availability(base_url: str = "http://127.0.0.1:11434", names: list[str] | None = None) -> dict[str, object]:
    """Ask the local Ollama instance which models are loaded/available."""
    try:
        import requests

        resp = requests.get(base_url.rstrip("/") + "/api/tags", timeout=3)
        if resp.status_code != 200:
            return {"available": False, "reason": f"ollama returned {resp.status_code}"}
        available_names = {m.get("name", "") for m in resp.json().get("models", [])}
    except Exception:
        return {"available": False, "reason": "ollama is not reachable"}
    checks = list(names or [])
    return {
        "available": True,
        "models": [n for n in checks if n in available_names],
        "missing": [n for n in checks if n not in available_names],
    }


def system_snapshot() -> dict[str, object]:
    mem = memory_status()
    return {
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "memory": mem,
        "low_memory": is_low_memory(),
        "disk": disk_status(),
    }


@dataclass
class ResourceGuard:
    """Decide whether expensive features may run based on free RAM."""

    threshold_mb: float = LOW_MEMORY_MB

    def pressure(self) -> dict[str, float | bool]:
        return memory_status()

    @property
    def is_low(self) -> bool:
        return memory_status().get("ram_available_mb", 0) < self.threshold_mb

    def heavy_features_allowed(self) -> bool:
        """Document indexing and page extraction are heavy; disable under pressure."""
        return not self.is_low
