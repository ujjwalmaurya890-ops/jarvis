"""The actual tools JARVIS can call.

Every handler is deliberately narrow: no shell strings built from model text, no
recursive deletes, no network calls to hosts the user did not allow. Each tool
declares (a) a description the model sees, (b) the capability it needs and
(c) the argument types. `ToolRegistry` validates the arguments and checks
permissions before any handler runs.

Scope note: features that need OS-specific libraries this project does not
depend on (system volume, keyboard synthesis, clicking inside other apps'
windows) are intentionally NOT stubbed. See README → "Deliberately not
implemented" for where to add them.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from security.permissions import Capability
from tools.registry import ToolResult, ToolRegistry, sanitise_query

log = logging.getLogger("jarvis.system_tools")


@dataclass
class ToolContext:
    """Everything the tools are allowed to touch. Built once, at startup."""

    settings: object
    launcher: object
    files: object
    notes: object
    memory: object = None          # MemoryDB or None
    browser: object = None         # BrowserManager or None
    index: object = None           # DocumentIndex or None


def register_system_tools(reg: ToolRegistry, ctx: ToolContext) -> None:
    settings = ctx.settings
    launcher, files, notes = ctx.launcher, ctx.files, ctx.notes
    mem = lambda: ctx.memory  # resolved lazily: the DB may attach after boot

    # ------------------------------------------------------------------ apps --
    @reg.tool(
        "launch_app",
        "Launch a desktop application the user allow-listed, by short name (e.g. 'notepad', 'code').",
        Capability.APP_LAUNCH,
        {"name": str, "args": list},
        examples=["open notepad", "launch code"],
    )
    def launch_app(name: str, args: list | None = None) -> ToolResult:
        result = launcher.focus_or_launch(str(name))
        if not result.ok:
            return ToolResult.fail(result.message)
        return ToolResult(True, speech=result.message, display=result.message, data={"pid": result.pid, "path": result.path})

    # ----------------------------------------------------------------- sites --
    @reg.tool(
        "open_website",
        "Open an allow-listed website in the default browser. Use for 'open youtube / github / gmail'.",
        Capability.SITE_OPEN,
        {"url": str},
        examples=["open youtube"],
    )
    def open_website(url: str) -> ToolResult:
        target = str(url).strip()
        decision = reg.broker.check_url(target, settings.automation.allowed_sites)
        if not decision.allowed:
            return ToolResult.say(f"I can't open that: {decision.reason}")
        result = launcher.open_url(target)
        if not result.ok:
            return ToolResult.fail(result.message)
        return ToolResult(True, speech=f"Opening {target[:70]}.", display=f"Opened {target}", data={"url": target})

    @reg.tool(
        "web_search",
        "Search the web and return the top results with their URLs. For news, prices, facts, docs.",
        Capability.NET_SEARCH,
        {"query": str},
        examples=["search the web for python 3.13 release notes"],
    )
    def web_search(query: str) -> ToolResult:
        from tools.web_search import search_web

        clean = sanitise_query(str(query))
        if not clean:
            return ToolResult.fail("Your search had no usable words.")
        outcome = search_web(
            clean,
            max_results=settings.search.max_results,
            timeout=settings.search.request_timeout,
            engine="auto" if settings.search.engine == "gemini" else settings.search.engine,
            extract_chars=settings.search.extract_chars,
        )
        if not outcome.hits:
            if outcome.fallback_url:
                opened = launcher.open_url(outcome.fallback_url)
                if not opened.ok:
                    return ToolResult.fail(opened.message)
                return ToolResult(True, speech="I opened the search in your browser instead.", data={"url": outcome.fallback_url})
            return ToolResult.fail(outcome.error or "No results.")
        lines = [f"{i}. {h.title}\n   {h.url}\n   {h.snippet[:180]}" for i, h in enumerate(outcome.hits, 1)]
        return ToolResult(
            True,
            speech=f"I found {len(outcome.hits)} results.",
            display="Web results — RETRIEVED, untrusted:\n" + "\n".join(lines),
            sources=[h.as_dict() for h in outcome.hits],
            data={"query": clean, "provider": outcome.provider},
        )

    @reg.tool(
        "read_url",
        "Fetch one web page and return its readable text (renders with Chromium if Playwright is installed).",
        Capability.NET_FETCH,
        {"url": str, "chars": int},
    )
    def read_url(url: str, chars: int = 6000) -> ToolResult:
        target = str(url).strip()
        decision = reg.broker.check_url(target, settings.automation.allowed_sites)
        if not decision.allowed:
            return ToolResult.say(f"I won't fetch that: {decision.reason}")
        limit = max(500, min(int(chars or 6000), 20000))
        if ctx.browser is not None and ctx.browser.available is not False:
            page = ctx.browser.read_page(target, allowed_sites=settings.automation.allowed_sites)
            if page.ok:
                return ToolResult(
                    True,
                    speech=f"I read about {len(page.text)} characters.",
                    display=f"{page.title}\n{page.url}\n\n{page.text[:limit]}",
                    sources=[{"title": page.title or target, "url": page.url}],
                    data={"text": page.text[:limit]},
                )
            error = page.error
        else:
            from tools.web_search import extract_text

            try:
                text = extract_text(
                    target, limit=limit, timeout=settings.search.request_timeout,
                    allowed_sites=settings.automation.allowed_sites,
                )
            except Exception as exc:
                return ToolResult.fail(f"Could not fetch the page: {exc}")
            error = None
            page = None
        if error:
            return ToolResult.fail(error)
        return ToolResult(
            True, speech=f"Read {len(text)} characters from that page.",
            display=f"{target}\n\n{text[:limit]}",
            data={"text": text[:limit]}, sources=[{"title": target, "url": target}],
        )

    # ---------------------------------------------------------------- files --
    @reg.tool(
        "find_files",
        "Search FILE NAMES inside the allowed folders only. Accepts a fragment or a *.ext pattern.",
        Capability.FS_READ,
        {"pattern": str},
        examples=["find files named assignment"],
    )
    def find_files(pattern: str) -> ToolResult:
        hits = files.search(str(pattern)[:80])
        if not hits:
            return ToolResult.say(f"No files matching '{pattern}' in the folders I can read.")
        listing = "\n".join(
            f"- {h.path}  ({h.size/1024:.0f} KB)" if h.size >= 1024 else f"- {h.path}  ({h.size} B)"
            for h in hits[:20]
        )
        return ToolResult(True, speech=f"I found {len(hits)} matching files.", display=listing, data={"paths": [str(h.path) for h in hits[:20]]})

    @reg.tool("read_file", "Read a text file that lives inside an allowed folder.", Capability.FS_READ, {"path": str})
    def read_file(path: str) -> ToolResult:
        from tools.file_manager import PathError

        try:
            text, resolved = files.read_text(str(path))
        except PathError as exc:
            return ToolResult.say(str(exc))
        return ToolResult(True, speech=f"Read {len(text)} characters from {resolved.name}.", display=f"{resolved}\n\n{text[:6000]}")

    @reg.tool(
        "summarise_document",
        "Load a permitted document or a note so its text can be summarised.",
        Capability.FS_READ,
        {"path_or_query": str},
    )
    def summarise_document(path_or_query: str) -> ToolResult:
        from tools.file_manager import PathError

        candidate = str(path_or_query).strip()
        try:
            if candidate and "." not in Path(candidate).name and not Path(candidate).is_absolute():
                hits = files.search(candidate)
                if not hits:
                    found = notes.find(candidate)
                    if not found:
                        return ToolResult.say(f"I found no document named '{candidate}'.")
                    return ToolResult(True, speech="Found your note.", display=found[0].body[:6000], data={"text": found[0].body[:12000]})
                candidate = str(hits[0].path)
            text, resolved = files.read_text(candidate)
        except PathError as exc:
            return ToolResult.say(str(exc))
        return ToolResult(True, speech=f"Loaded {Path(candidate).name}. Ask me to summarise it.", display=f"{resolved}\n\n{text[:8000]}", data={"text": text[:12000]})

    # ---------------------------------------------------------------- notes --
    @reg.tool(
        "write_note",
        "Create a markdown note in JARVIS's own workspace folder.",
        Capability.FS_WRITE,
        {"title": str, "body": str, "tags": list},
        examples=["make a note that the lab report is due Friday"],
    )
    def write_note(title: str, body: str, tags: list | None = None) -> ToolResult:
        note = notes.create(str(title or "Note")[:120], str(body or "")[:8000], [str(t) for t in (tags or [])][:6])
        return ToolResult(True, speech=f"Note saved: {note.title}.", display=f"Saved to {note.path}", data={"path": str(note.path)})

    @reg.tool("read_latest_notes", "Read the most recent notes back.", Capability.FS_READ, {"count": int})
    def read_latest_notes(count: int = 5) -> ToolResult:
        items = notes.latest(max(1, min(int(count or 5), 20)))
        if not items:
            return ToolResult.say("You have no notes yet.")
        return ToolResult(
            True,
            speech=f"Here are your last {len(items)} notes.",
            display="\n\n".join(f"### {n.title}\n{n.body[:400]}" for n in items),
        )

    # --------------------------------------------------------------- memory --
    @reg.tool("remember_fact", "Store a personal preference or fact the user asked you to keep.", Capability.FS_WRITE, {"key": str, "value": str})
    def remember_fact(key: str, value: str) -> ToolResult:
        db = mem()
        if db is None:
            return ToolResult.say("Memory is not attached, so I can't store that.")
        db.remember(str(key), str(value))
        return ToolResult(True, speech="Noted.", data={"key": key})

    @reg.tool("recall_fact", "Recall a stored fact or preference by keyword.", Capability.FS_READ, {"key": str})
    def recall_fact(key: str) -> ToolResult:
        db = mem()
        if db is None:
            return ToolResult.say("Memory is not attached.")
        value = db.recall(str(key))
        if value is None:
            matches = db.search_turns(str(key), limit=3)
            if matches:
                return ToolResult(True, speech="I found it in our history.", display="\n".join(m["text"][:200] for m in matches))
            return ToolResult.say(f"I have nothing stored about '{key}'.")
        return ToolResult(True, speech=value[:400], display=value)

    @reg.tool("show_memory", "Show what is stored locally: counts, facts and indexed documents.", Capability.FS_READ, {})
    def show_memory() -> ToolResult:
        db = mem()
        if db is None:
            return ToolResult.say("Memory is not attached.")
        lines = [f"{k}: {v}" for k, v in db.stats().items()]
        facts = db.all_facts()[:15]
        if facts:
            lines.append("\nStored facts:")
            lines += [f"- {f['key']} = {f['value'][:80]}" for f in facts]
        indexed = db.indexed_paths()[:10]
        if indexed:
            lines.append("\nIndexed documents:")
            lines += [f"- {r['path']} ({r['chunks']} chunks)" for r in indexed]
        return ToolResult(True, speech=f"You have {len(facts)} facts stored.", display="\n".join(lines))

    @reg.tool(
        "clear_memory",
        "Delete stored conversation turns and facts. Notes and files are never touched.",
        Capability.FS_WRITE,
        {},
        dangerous=True,
    )
    def clear_memory() -> ToolResult:
        db = mem()
        if db is None:
            return ToolResult.say("Memory is not attached.")
        db.clear_memory()
        return ToolResult(True, speech="Conversation memory cleared. Notes and permission grants were not touched.")

    @reg.tool("index_documents", "Re-scan the allowed folders into the local search index (opt-in).", Capability.FS_WRITE, {"force": bool})
    def index_documents(force: bool = False) -> ToolResult:
        if ctx.index is None:
            return ToolResult.say("The document index is disabled. Turn it on in Settings.")
        report = ctx.index.index_roots(settings.automation.allowed_dirs, force=bool(force))
        return ToolResult(True, speech=f"Indexed {report.get('files', 0)} files.", display=str(report))

    @reg.tool(
        "import_document",
        "Import ONE PDF or text document into the local search index by path.",
        Capability.FS_READ,
        {"path": str},
    )
    def import_document(path: str) -> ToolResult:
        if ctx.index is None:
            return ToolResult.say("The document index is disabled. Turn it on in Settings.")
        candidate = str(path).strip().strip('\"')
        decision = reg.broker.check_path(candidate)
        if not decision.allowed:
            # A sandbox refusal is a failed action, not a polite reply.
            return ToolResult.fail(decision.reason)
        from memory.document_loader import DocumentLoadError

        try:
            count = ctx.index.index_file(candidate)
        except DocumentLoadError as exc:
            return ToolResult.say(str(exc))
        if not count:
            return ToolResult.say("That document had no readable text to index.")
        return ToolResult(
            True,
            speech=f"Imported {Path(candidate).name} ({count} chunks).",
            display=f"Indexed {Path(candidate).name}: {count} chunks.",
            data={"path": candidate, "chunks": count},
        )

    @reg.tool(
        "ask_documents",
        "Answer a question using your LOCALLY indexed documents and cite the source filename and page.",
        Capability.FS_READ,
        {"question": str},
    )
    def ask_documents(question: str) -> ToolResult:
        if ctx.index is None:
            return ToolResult.say("The document index is disabled. Turn it on in Settings.")
        from security.input_validation import sanitize_text

        q = sanitize_text(str(question), max_len=400).strip()
        if not q:
            return ToolResult.say("Ask me a question about your documents.")
        hits = ctx.index.search(q, k=5)
        if not hits:
            return ToolResult.say("I couldn't find anything relevant in your indexed documents.")
        citations = ctx.index.citations(hits)
        blocks = []
        for i, hit in enumerate(hits, 1):
            blocks.append(
                f"[{i}] {ctx.index.citation(hit)}\n<source>\n{hit['text']}\n</source>"
            )
        context = "\n\n".join(blocks)
        sources = [{"title": c["file"], "url": c["path"]} for c in citations]
        return ToolResult(
            True,
            speech="I found relevant passages in your documents.",
            display="Sources:\n" + "\n".join(
                f"- {c['file']}" + (f" (page {c['page']})" if c["page"] else "") for c in citations
            ),
            sources=sources,
            data={"text": context, "citations": citations},
        )

    # --------------------------------------------------------------- system --
    @reg.tool("system_stats", "Report RAM, CPU, disk and platform of this machine.", Capability.FS_READ, {})
    def system_stats() -> ToolResult:
        info = _quick_stats()
        parts = []
        if "ram_total" in info:
            parts.append(f"{info['ram_used']:.1f} of {info['ram_total']:.1f} GB RAM in use")
        if "cpu" in info:
            parts.append(f"CPU about {info['cpu']:.0f}%")
        if "disk_free" in info:
            parts.append(f"{info['disk_free']:.0f} GB free on disk")
        text = ", ".join(str(p) for p in parts) or "No readings available."
        return ToolResult(True, speech=f"{text}.", display=f"{text}\n{info.get('system','')} / Python {info.get('python','')}", data=dict(info))

    @reg.tool(
        "system_power",
        "Shut down or restart Windows. Always schedules with a delay so it can be cancelled.",
        Capability.POWER,
        {"action": str, "delay_seconds": int},
        dangerous=True,
    )
    def system_power(action: str = "shutdown", delay_seconds: int = 60) -> ToolResult:
        verb = str(action).lower().strip()
        if verb not in {"shutdown", "restart"}:
            return ToolResult.say("I only know 'shutdown' and 'restart'.")
        delay = max(30, min(int(delay_seconds or 60), 3600))
        if platform.system() != "Windows":
            return ToolResult.say("Power control is wired up for Windows only, and this is not Windows.")
        argv = ["shutdown", "/s" if verb == "shutdown" else "/r", "/t", str(delay)]
        try:
            subprocess.run(argv, shell=False, check=False, capture_output=True, timeout=10)  # noqa: S603
        except (OSError, subprocess.SubprocessError) as exc:
            return ToolResult.fail(f"Windows refused the request ({exc}). Use the Start menu instead.")
        return ToolResult(
            True,
            speech=f"{verb.title()} scheduled in {delay} seconds. Say cancel the shutdown to stop it.",
            display="Scheduled: " + " ".join(argv),
        )

    @reg.tool("cancel_power", "Abort a scheduled Windows shutdown or restart.", Capability.POWER, {}, dangerous=True)
    def cancel_power() -> ToolResult:
        if platform.system() != "Windows":
            return ToolResult.say("Only Windows schedules shutdowns.")
        try:
            subprocess.run(["shutdown", "/a"], shell=False, check=False, capture_output=True, timeout=10)  # noqa: S603
        except (OSError, subprocess.SubprocessError) as exc:
            return ToolResult.fail(f"Could not cancel: {exc}")
        return ToolResult(True, speech="Shutdown cancelled.")

    @reg.tool(
        "run_command",
        "Run ONE command the user typed into the allow-list in Settings, referenced by its name.",
        Capability.RUN_COMMAND,
        {"name": str},
        dangerous=True,
    )
    def run_command(name: str) -> ToolResult:
        from security.command_allowlist import CommandNotAllowed, parse_allowlist, resolve

        try:
            cmd = resolve(str(name), parse_allowlist(settings.automation.allowed_commands))
        except CommandNotAllowed as exc:
            return ToolResult.say(str(exc))
        try:
            proc = subprocess.run(  # noqa: S603 - argv comes only from the human-written list
                cmd.argv,
                shell=False,
                cwd=cmd.cwd or None,
                capture_output=True,
                text=True,
                timeout=min(cmd.timeout, 300),
            )
        except subprocess.TimeoutExpired:
            return ToolResult.fail(f"'{cmd.name}' timed out after {min(cmd.timeout, 300):.0f}s.")
        except OSError as exc:
            return ToolResult.fail(f"Could not run '{cmd.name}': {exc}")
        out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        return ToolResult(
            proc.returncode == 0,
            speech=f"{cmd.name} exited with code {proc.returncode}.",
            display=f"$ {' '.join(cmd.argv)}\nexit {proc.returncode}\n\n{out[:4000]}",
            error=None if proc.returncode == 0 else f"exit code {proc.returncode}",
        )


def _quick_stats() -> dict[str, float | str]:
    """RAM / CPU / disk readings with no extra dependency (psutil not required)."""
    info: dict[str, float | str] = {
        "system": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": platform.python_version(),
    }
    try:
        usage = shutil.disk_usage(os.path.expanduser("~"))
        info["disk_free"] = round(usage.free / 1e9, 1)
        info["disk_total"] = round(usage.total / 1e9, 1)
    except OSError:
        pass
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
                info["ram_total"] = round(stat.ullTotalPhys / 1e9, 2)
                info["ram_used"] = round((stat.ullTotalPhys - stat.ullAvailPhys) / 1e9, 2)
                info["ram_percent"] = float(stat.dwMemoryLoad)
        except Exception:
            log.debug("GlobalMemoryStatusEx failed", exc_info=True)
    else:
        try:
            with open("/proc/meminfo", encoding="utf-8") as fh:
                values: dict[str, float] = {}
                for line in fh:
                    key, _, rest = line.partition(":")
                    parts = rest.split()
                    if parts:
                        values[key.strip()] = float(parts[0]) / 1e6
            info["ram_total"] = round(values.get("MemTotal", 0.0), 2)
            info["ram_used"] = round(values.get("MemTotal", 0.0) - values.get("MemAvailable", 0.0), 2)
        except OSError:
            pass
    cpu = _cpu_percent()
    if cpu is not None:
        info["cpu"] = cpu
    return info


def _cpu_percent(sample: float = 0.2) -> float | None:
    """Busy-percentage from /proc/stat (Linux) or GetSystemTimes (Windows)."""
    if platform.system() == "Windows":
        try:
            import ctypes

            class FILETIME(ctypes.Structure):
                _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]

            def read() -> tuple[float, float]:
                idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
                ctypes.windll.kernel32.GetSystemTimes(  # type: ignore[attr-defined]
                    ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
                )

                def secs(t: FILETIME) -> float:
                    return ((t.hi << 32) | t.lo) * 1e-7

                return secs(idle), secs(kernel) + secs(user)

            i0, k0 = read()
            time.sleep(sample)
            i1, k1 = read()
            total = (k1 - k0) + (i1 - i0)
            return round(100.0 * (1 - (i1 - i0) / total), 1) if total > 0 else 0.0
        except Exception:
            return None
    try:
        with open("/proc/stat", encoding="utf-8") as fh:
            first = fh.readline().split()
        values = [float(v) for v in first[1:]]
        idle0 = values[3] + (values[4] if len(values) > 4 else 0)
        total0 = sum(values)
        time.sleep(sample)
        with open("/proc/stat", encoding="utf-8") as fh:
            second = fh.readline().split()
        values = [float(v) for v in second[1:]]
        idle1 = values[3] + (values[4] if len(values) > 4 else 0)
        total1 = sum(values)
        dt = total1 - total0
        return round(100.0 * (1 - (idle1 - idle0) / dt), 1) if dt > 0 else 0.0
    except (OSError, IndexError, ValueError):
        return None
