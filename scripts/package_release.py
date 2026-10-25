"""Build a source-only ZIP; fail closed if runtime/private files leak in.

Usage from the project root:  python scripts/package_release.py
The allow-list keeps package-lock.json, Python tests and the generated source
browser module, but excludes all caches, user data, models, logs and secrets.
"""

from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "release" / "jarvis-corrected.zip"
ROOT_FILES = {
    ".gitignore", "README.md", "index.html", "package.json",
    "package-lock.json", "tsconfig.json", "vite.config.ts",
}
JARVIS_FILES = {".gitignore", ".env.example", "README.md", "main.py", "requirements.txt"}
JARVIS_DIRS = {"api", "config", "core", "audio", "ai", "memory", "security", "tools", "ui", "tests", "docs"}
JARVIS_SUFFIXES = {".py", ".md", ".txt", ".ps1", ".toml"}
FRONTEND_SUFFIXES = {".ts", ".tsx", ".css"}
SCRIPT_SUFFIXES = {".mjs", ".py"}
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".venv", "venv", "node_modules", "dist", "logs", "models"}
REQUIRED = {
    "package-lock.json", "src/App.tsx", "src/lib/useBackend.ts", "src/data/project.ts",
    "jarvis/main.py", "jarvis/api/server.py", "jarvis/requirements.txt",
    "jarvis/.env.example", "jarvis/tests/test_local_api.py",
}


def source_files() -> list[Path]:
    files = [ROOT / name for name in ROOT_FILES]
    files.extend(p for p in (ROOT / "src").rglob("*") if p.is_file() and p.suffix in FRONTEND_SUFFIXES)
    files.extend(p for p in (ROOT / "scripts").rglob("*") if p.is_file() and p.suffix in SCRIPT_SUFFIXES)
    base = ROOT / "jarvis"
    files.extend(base / name for name in JARVIS_FILES)
    for folder in JARVIS_DIRS:
        files.extend(p for p in (base / folder).rglob("*") if p.is_file() and p.suffix in JARVIS_SUFFIXES)
    return sorted(set(files))


def validate(files: list[Path]) -> list[str]:
    paths: list[str] = []
    for file in files:
        if not file.is_file():
            raise FileNotFoundError(f"Required source file is missing: {file}")
        relative = file.relative_to(ROOT).as_posix()
        if any(part in EXCLUDED_PARTS for part in file.parts):
            raise ValueError(f"Refusing generated artifact: {relative}")
        if relative.startswith(("jarvis/data/", "jarvis/logs/")):
            raise ValueError(f"Refusing private runtime data: {relative}")
        if file.name == ".env" or (file.name.startswith(".env.") and file.name != ".env.example"):
            raise ValueError(f"Refusing credentials: {relative}")
        paths.append(relative)
    if not REQUIRED.issubset(paths):
        raise ValueError(f"Release is missing required source: {sorted(REQUIRED.difference(paths))}")
    return paths


def main() -> None:
    files = source_files()
    paths = validate(files)
    OUTPUT.parent.mkdir(exist_ok=True)
    with ZipFile(OUTPUT, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for file, path in zip(files, paths):
            archive.write(file, arcname=path)
    with ZipFile(OUTPUT) as archive:
        broken = archive.testzip()
        if broken:
            raise ValueError(f"ZIP integrity check failed on: {broken}")
        if set(archive.namelist()) != set(paths):
            raise ValueError("ZIP contents differ from the reviewed source allow-list")
    print(f"Created {OUTPUT.relative_to(ROOT)} ({OUTPUT.stat().st_size:,} bytes, {len(paths)} reviewed source files)")
    print("Includes package-lock.json and all Python tests; excludes jarvis/data, logs, models and caches.")


if __name__ == "__main__":
    main()